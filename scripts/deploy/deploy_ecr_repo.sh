#!/bin/bash

# ECR Repository Deployment Script
# This script deploys the ECR repository for Lambda container images
# All configuration is read from infra.yaml
#
# Usage Examples:
#   # Deploy to development environment
#   ./scripts/deploy/deploy_ecr_repo.sh dev deploy
#
#   # Deploy with auto-confirmation
#   ./scripts/deploy/deploy_ecr_repo.sh dev deploy -y
#
#   # Check stack status
#   ./scripts/deploy/deploy_ecr_repo.sh dev status
#
#   # Delete stack
#   ./scripts/deploy/deploy_ecr_repo.sh dev delete

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Script Configuration
# =============================================================================

RESOURCE_NAME="rag_lambda_ecr"
RESOURCE_DISPLAY_NAME="ECR Repository"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "ECR Repository Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Actions:"
    echo "  deploy    - Deploy the stack (default)"
    echo "  update    - Update the stack"
    echo "  delete    - Delete the stack"
    echo "  validate  - Validate the template"
    echo "  status    - Show stack status"
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
        deploy|update|delete|validate|status)
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

print_header "$RESOURCE_DISPLAY_NAME Deployment"

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

STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME")

# Get config values (with variable substitution)
REPOSITORY_NAME=$(get_resource_config "$RESOURCE_NAME" "repository_name" "$ENVIRONMENT")
MAX_IMAGE_COUNT=$(get_resource_config "$RESOURCE_NAME" "max_image_count")

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
# Template Validation
# =============================================================================

do_validate_template() {
    print_step "Validating CloudFormation template..."
    
    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi
    
    if aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" >/dev/null 2>&1; then
        print_complete "Template validation successful"
    else
        print_error "Template validation failed"
        exit 1
    fi
}

# =============================================================================
# Stack Status
# =============================================================================

check_stack_status() {
    local status=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null)
    
    if [ -z "$status" ]; then
        return 1
    fi
    
    case "$status" in
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED)
            print_error "Stack is in failed state: $status"
            return 1
            ;;
        CREATE_COMPLETE|UPDATE_COMPLETE)
            return 0
            ;;
        *)
            return 0
            ;;
    esac
}

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

# =============================================================================
# Deploy Stack
# =============================================================================

deploy_stack() {
    # Show deploy summary
    print_resource_summary "$RESOURCE_NAME" "$ENVIRONMENT" "$ACTION"
    print_info "Repository Name: $REPOSITORY_NAME"
    print_info "Max Image Count: $MAX_IMAGE_COUNT"
    
    # Confirm deployment
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment || exit 0
    fi
    
    print_step "Deploying CloudFormation stack: $STACK_NAME"
    
    # Create parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    cat > "$param_file" << EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "RepositoryName", "ParameterValue": "$REPOSITORY_NAME"},
  {"ParameterKey": "MaxImageCount", "ParameterValue": "$MAX_IMAGE_COUNT"}
]
EOF
    
    # Check if stack exists
    local stack_exists=false
    local no_updates=false
    
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        stack_exists=true
        print_info "Stack $STACK_NAME already exists. Updating..."
        
        local update_output
        update_output=$(aws_cmd cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" 2>&1) || {
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_info "No updates needed for stack $STACK_NAME"
                no_updates=true
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
            --parameters "file://$param_file" || {
            print_error "Stack creation failed"
            exit 1
        }
    fi
    
    # Wait for stack operation
    if [ "$no_updates" = false ]; then
        print_info "Waiting for stack operation to complete..."
        
        local wait_cmd="stack-create-complete"
        [ "$stack_exists" = true ] && wait_cmd="stack-update-complete"
        
        if aws_cmd cloudformation wait "$wait_cmd" --stack-name "$STACK_NAME" 2>/dev/null; then
            print_complete "Stack operation completed successfully"
        else
            if ! check_stack_status; then
                print_error "Stack deployment failed"
                exit 1
            fi
        fi
    fi
    
    # Verify final status
    if ! check_stack_status; then
        print_error "Stack is in a failed state"
        exit 1
    fi
    
    # Display outputs
    print_complete "$RESOURCE_DISPLAY_NAME deployment finished"
    echo ""
    
    local repo_uri=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`RepositoryUri`].OutputValue' \
        --output text 2>/dev/null)
    
    [ -n "$repo_uri" ] && [ "$repo_uri" != "None" ] && print_info "Repository URI: $repo_uri"
}

# =============================================================================
# Delete Stack
# =============================================================================

delete_stack() {
    print_warning "This will delete:"
    print_warning "  - ECR Repository"
    print_warning "  - All container images in the repository"
    echo ""
    print_warning "WARNING: This will permanently delete all images!"
    
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete" || exit 0
    fi
    
    # Force delete all images first (ECR won't delete non-empty repos)
    local repo_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`RepositoryName`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -n "$repo_name" ] && [ "$repo_name" != "None" ]; then
        print_info "Deleting all images from repository: $repo_name"
        
        # Get all image IDs
        local image_ids=$(aws_cmd ecr list-images \
            --repository-name "$repo_name" \
            --query 'imageIds[*]' \
            --output json 2>/dev/null)
        
        if [ -n "$image_ids" ] && [ "$image_ids" != "[]" ]; then
            aws_cmd ecr batch-delete-image \
                --repository-name "$repo_name" \
                --image-ids "$image_ids" >/dev/null 2>&1 || true
        fi
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
    validate)
        do_validate_template
        ;;
    status)
        show_status
        ;;
    deploy|update)
        do_validate_template
        deploy_stack
        ;;
    delete)
        delete_stack
        ;;
    *)
        print_error "Invalid action: $ACTION"
        show_usage
        exit 1
        ;;
esac

print_complete "$RESOURCE_DISPLAY_NAME operation completed successfully"
