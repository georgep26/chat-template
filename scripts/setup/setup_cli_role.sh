#!/bin/bash

# CLI Role Setup Script
# This script creates the CLI access IAM role for local development
# All configuration is read from infra.yaml
#
# Usage Examples:
#   # Set up CLI role in dev account
#   ./scripts/setup/setup_cli_role.sh dev
#
#   # Set up with auto-confirmation
#   ./scripts/setup/setup_cli_role.sh dev -y

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "CLI Role Setup Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Actions:"
    echo "  deploy    - Deploy the role stack (default)"
    echo "  delete    - Delete the role stack"
    echo "  status    - Check stack status"
    echo ""
    echo "Options:"
    echo "  -y, --yes   - Skip confirmation prompt"
    echo ""
    echo "Note: All configuration is read from infra/infra.yaml"
}

# =============================================================================
# Argument Parsing
# =============================================================================

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

# Default values
ACTION="deploy"
AUTO_CONFIRM=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        deploy|delete|status)
            ACTION="$1"
            shift
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# =============================================================================
# Configuration Loading
# =============================================================================

print_header "CLI Role Setup"

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration
print_step "Loading configuration for $ENVIRONMENT environment"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Get values from config
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")
STACK_NAME=$(get_role_stack_name "cli" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_role_template "cli")

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

# =============================================================================
# AWS CLI Helper
# =============================================================================

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# =============================================================================
# Stack Functions
# =============================================================================

show_status() {
    print_step "Checking stack status: $STACK_NAME"
    
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        aws_cmd cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_info "Stack outputs:"
        aws_cmd cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

deploy_stack() {
    # Show summary
    echo ""
    print_info "Deploying CLI role with:"
    print_info "  Stack Name: $STACK_NAME"
    print_info "  Environment: $ENVIRONMENT"
    print_info "  Account: $ACCOUNT_ID"
    echo ""
    
    # Confirm
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Deploy CLI role?" || exit 0
    fi
    
    print_step "Deploying CloudFormation stack: $STACK_NAME"
    
    # Validate template
    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi
    
    # Create parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    cat > "$param_file" << EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"}
]
EOF
    
    # Check if stack exists
    local stack_exists=false
    
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        stack_exists=true
        print_info "Stack exists. Updating..."
        
        local update_output
        update_output=$(aws_cmd cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1) || {
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_info "No updates needed"
                show_status
                return 0
            else
                print_error "Stack update failed: $update_output"
                exit 1
            fi
        }
    else
        print_info "Creating new stack: $STACK_NAME"
        aws_cmd cloudformation create-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM || {
            print_error "Stack creation failed"
            exit 1
        }
    fi
    
    # Wait for stack operation
    print_info "Waiting for stack operation to complete..."
    
    local wait_cmd="stack-create-complete"
    [ "$stack_exists" = true ] && wait_cmd="stack-update-complete"
    
    if aws_cmd cloudformation wait "$wait_cmd" --stack-name "$STACK_NAME" 2>/dev/null; then
        print_complete "Stack operation completed successfully"
    else
        print_error "Stack operation failed"
        exit 1
    fi
    
    # Show outputs
    local role_arn=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`RoleArn`].OutputValue' \
        --output text 2>/dev/null)
    
    print_complete "CLI role created successfully"
    echo ""
    print_info "Role ARN: $role_arn"
    echo ""
    print_info "To use this role, add to your AWS config (~/.aws/config):"
    echo ""
    echo "[profile ${PROJECT_NAME}-${ENVIRONMENT}-cli]"
    echo "role_arn = $role_arn"
    echo "source_profile = default  # or your base profile"
    echo "region = $AWS_REGION"
}

delete_stack() {
    print_warning "Deleting CLI role stack: $STACK_NAME"
    
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete CLI role" || exit 0
    fi
    
    print_step "Deleting CloudFormation stack: $STACK_NAME"
    
    if aws_cmd cloudformation delete-stack --stack-name "$STACK_NAME"; then
        print_info "Stack deletion initiated"
        print_info "Waiting for deletion to complete..."
        
        if aws_cmd cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" 2>/dev/null; then
            print_complete "Stack deleted successfully"
        else
            print_warning "Stack deletion may still be in progress"
        fi
    else
        print_error "Failed to initiate stack deletion"
        exit 1
    fi
}

# =============================================================================
# Main Execution
# =============================================================================

case $ACTION in
    deploy)
        deploy_stack
        ;;
    delete)
        delete_stack
        ;;
    status)
        show_status
        ;;
    *)
        print_error "Invalid action: $ACTION"
        show_usage
        exit 1
        ;;
esac

print_complete "CLI role operation completed"
