#!/bin/bash

# VPC Network Deployment Script
# This script deploys the VPC, subnets, security groups, and VPC endpoints
# All configuration is read from infra.yaml
#
# Usage Examples:
#   # Deploy to development environment
#   ./scripts/deploy/deploy_network.sh dev deploy
#
#   # Deploy with auto-confirmation (skip prompt)
#   ./scripts/deploy/deploy_network.sh dev deploy -y
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_network.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_network.sh dev status
#
#   # Delete stack
#   ./scripts/deploy/deploy_network.sh dev delete

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Script Configuration
# =============================================================================

RESOURCE_NAME="network"
RESOURCE_DISPLAY_NAME="VPC Network"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "VPC Network Deployment Script"
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

STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT") || true
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME") || true

# use_defaults is under resources.network (not under config)
USE_DEFAULTS=$(yq '.resources.network.use_defaults // false' "$INFRA_CONFIG_PATH")
# Normalize use_defaults (yq may return true/false or null)
[ "$USE_DEFAULTS" = "true" ] || USE_DEFAULTS=false
[ "$USE_DEFAULTS" != "false" ] && USE_DEFAULTS=true

# Custom network config (only used when use_defaults is false)
VPC_CIDR=$(get_resource_config "$RESOURCE_NAME" "vpc_cidr" "$ENVIRONMENT")
ENABLE_NAT_GATEWAY=$(get_resource_config "$RESOURCE_NAME" "enable_nat_gateway" "$ENVIRONMENT")
[ "$VPC_CIDR" = "null" ] || [ -z "$VPC_CIDR" ] && VPC_CIDR="10.0.0.0/16"
[ "$ENABLE_NAT_GATEWAY" = "null" ] || [ -z "$ENABLE_NAT_GATEWAY" ] && ENABLE_NAT_GATEWAY=false

# When use_defaults is true: read VPC ID from environments.<env>.vpc_id (the canonical location).
EXISTING_VPC_ID=$(get_environment_vpc_id "$ENVIRONMENT")


# When use_defaults is true we only read VPC/subnets; use CLI profile so we use the same account as the console (deployer profile can point at a different account or fail to assume).
if [ "$USE_DEFAULTS" = "true" ]; then
    AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
    [ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
fi

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

# Defaults when template/stack_name are commented out (e.g. when using use_defaults)
[ -z "$STACK_NAME" ] || [ "$STACK_NAME" = "null" ] && STACK_NAME="$(get_project_name)-vpc-${ENVIRONMENT}"
[ -z "$TEMPLATE_FILE" ] || [ "$TEMPLATE_FILE" = "null" ] && TEMPLATE_FILE="${PROJECT_ROOT}/infra/resources/vpc_template.yaml"

if [ "$USE_DEFAULTS" = "true" ]; then
    if [ -n "$EXISTING_VPC_ID" ]; then
        print_info "Using existing VPC (use_defaults: true, network.config.vpc_id set). No CloudFormation stack will be deployed; script will verify VPC and subnets exist."
    else
        print_info "Using default VPC (use_defaults: true). No CloudFormation stack will be deployed; script will verify default VPC, subnets, and security group exist."
    fi
    echo ""
fi

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
# Default VPC / Subnets / Security Group Check (when use_defaults: true)
# =============================================================================

# Verifies that a given VPC exists and has at least two subnets (and default SG when check_sg=true).
# Prints IDs and returns 0 on success; prints errors and returns 1 on failure.
# Usage: check_existing_vpc_resources <vpc_id> [check_sg]
check_existing_vpc_resources() {
    local vpc_id=$1
    local check_sg=${2:-true}
    print_step "Checking VPC $vpc_id and subnets in region $AWS_REGION"
    
    local vpc_exists aws_stderr
    aws_stderr=$(mktemp)
    vpc_exists=$(aws_cmd ec2 describe-vpcs \
        --vpc-ids "$vpc_id" \
        --query 'Vpcs[0].VpcId' \
        --output text 2>"$aws_stderr") || true
    if [ -z "$vpc_exists" ] || [ "$vpc_exists" = "None" ]; then
        if [ -s "$aws_stderr" ]; then
            print_error "AWS error while describing VPC:"
            sed 's/^/  /' < "$aws_stderr" >&2
        fi
        rm -f "$aws_stderr"
        print_error "VPC $vpc_id not found in region $AWS_REGION. Check the ID, ensure the profile targets the correct account (use_defaults uses CLI profile), or set network.config.vpc_id in infra/infra.yaml."
        rm -f "$aws_stderr"
        return 1
    fi
    rm -f "$aws_stderr"
    print_info "VPC: $vpc_id"
    
    local subnet_ids
    subnet_ids=$(aws_cmd ec2 describe-subnets \
        --filters "Name=vpc-id,Values=$vpc_id" \
        --query 'Subnets[*].SubnetId' \
        --output text 2>/dev/null)
    
    if [ -z "$subnet_ids" ]; then
        print_error "No subnets found for VPC $vpc_id."
        return 1
    fi
    
    local subnet_count
    subnet_count=$(echo "$subnet_ids" | wc -w | tr -d ' ')
    if [ "$subnet_count" -lt 2 ]; then
        print_error "VPC $vpc_id has only $subnet_count subnet(s). At least 2 subnets (in different AZs) are required for Aurora."
        return 1
    fi
    print_info "Subnets ($subnet_count): $subnet_ids"
    
    if [ "$check_sg" = "true" ]; then
        local sg_id
        sg_id=$(aws_cmd ec2 describe-security-groups \
            --filters "Name=vpc-id,Values=$vpc_id" "Name=group-name,Values=default" \
            --query 'SecurityGroups[0].GroupId' \
            --output text 2>/dev/null)
        
        if [ -z "$sg_id" ] || [ "$sg_id" = "None" ]; then
            print_error "Default security group not found for VPC $vpc_id."
            return 1
        fi
        print_info "Default security group: $sg_id"
    fi
    
    print_complete "VPC resources are present and valid"
    echo ""
    print_info "Use the VPC ID and subnet IDs above when deploying the DB (e.g. deploy_chat_template_db.sh --vpc-id $vpc_id --subnet-ids <comma-separated>)."
    return 0
}

# Discover a VPC when none is configured. Tries the default VPC first, then the first available VPC.
# Sets DISCOVERED_VPC_ID on success.
discover_vpc() {
    print_step "No VPC configured for $ENVIRONMENT. Discovering VPC in region $AWS_REGION..."

    # Try the default VPC first
    local vpc_id
    vpc_id=$(aws_cmd ec2 describe-vpcs \
        --filters "Name=is-default,Values=true" \
        --query 'Vpcs[0].VpcId' \
        --output text 2>/dev/null)

    if [ -n "$vpc_id" ] && [ "$vpc_id" != "None" ]; then
        print_info "Found default VPC: $vpc_id"
        DISCOVERED_VPC_ID="$vpc_id"
        return 0
    fi

    print_warning "No default VPC found. Looking for any available VPC..."

    # Fall back to the first available VPC
    vpc_id=$(aws_cmd ec2 describe-vpcs \
        --query 'Vpcs[0].VpcId' \
        --output text 2>/dev/null)

    if [ -n "$vpc_id" ] && [ "$vpc_id" != "None" ]; then
        print_info "Found VPC: $vpc_id"
        DISCOVERED_VPC_ID="$vpc_id"
        return 0
    fi

    print_error "No VPCs found in region $AWS_REGION. Create a VPC first, or set use_defaults to false and deploy the network stack."
    return 1
}

# Write the discovered/verified VPC ID and subnet IDs back to infra.yaml under environments.<env>.
write_vpc_to_infra_yaml() {
    local vpc_id=$1
    local subnet_csv=$2   # comma-separated subnet IDs

    print_step "Recording VPC info in infra.yaml for $ENVIRONMENT environment"

    yq -i ".environments.${ENVIRONMENT}.vpc_id = \"${vpc_id}\"" "$INFRA_CONFIG_PATH"
    yq -i ".environments.${ENVIRONMENT}.subnet_ids = \"${subnet_csv}\"" "$INFRA_CONFIG_PATH"

    print_complete "Wrote vpc_id=$vpc_id and subnet_ids to infra.yaml (environments.$ENVIRONMENT)"
}

# Run the full use_defaults VPC flow:
#   1. If environments.<env>.vpc_id is set, verify it exists
#   2. If not set, discover (default VPC -> first VPC)
#   3. Verify subnets and security group
#   4. Write vpc_id and subnet_ids back to infra.yaml
run_use_defaults_vpc_check() {
    local vpc_id="$EXISTING_VPC_ID"

    # Step 1/2: Get a VPC ID
    if [ -n "$vpc_id" ]; then
        print_step "Using VPC from infra.yaml: $vpc_id"
    else
        discover_vpc || return 1
        vpc_id="$DISCOVERED_VPC_ID"
    fi

    # Step 3: Verify the VPC and get subnets
    check_existing_vpc_resources "$vpc_id" || return 1

    # Collect subnet IDs as comma-separated for writing to infra.yaml
    local subnet_ids_csv
    subnet_ids_csv=$(aws_cmd ec2 describe-subnets \
        --filters "Name=vpc-id,Values=$vpc_id" \
        --query 'Subnets[*].SubnetId' \
        --output text 2>/dev/null | tr '\t' ',')

    # Step 4: Write back to infra.yaml
    write_vpc_to_infra_yaml "$vpc_id" "$subnet_ids_csv"

    return 0
}

# Show default/existing VPC details (for status action when use_defaults: true)
show_default_vpc_status() {
    if [ -n "$EXISTING_VPC_ID" ]; then
        print_step "Existing VPC status (use_defaults: true, vpc_id=$EXISTING_VPC_ID, no stack deployed)"
    else
        print_step "Default VPC status (use_defaults: true, no stack deployed)"
    fi
    if run_use_defaults_vpc_check; then
        echo ""
        print_info "No CloudFormation stack is used when use_defaults is true. Other resources (e.g. DB) can use the VPC and subnets listed above."
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
    print_info "Template: $TEMPLATE_FILE"
    
    local validate_out validate_rc
    set +e
    validate_out=$(aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" 2>&1)
    validate_rc=$?
    set -e
    if [ "$validate_rc" -eq 0 ]; then
        print_complete "Template validation successful"
    else
        print_error "Template validation failed"
        [ -n "$validate_out" ] && echo "$validate_out" | sed 's/^/  /'
        print_info "To use the account's default VPC instead of deploying a custom network, set network.use_defaults to true in infra/infra.yaml"
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
    
    if [ "$USE_DEFAULTS" = "true" ]; then
        if [ -n "$EXISTING_VPC_ID" ]; then
            print_step "Verifying existing VPC $EXISTING_VPC_ID (no stack deployment)"
        else
            print_step "Verifying default VPC (no stack deployment)"
        fi
        if run_use_defaults_vpc_check; then
            echo ""
            print_complete "VPC is in use and has been verified. No network stack deployed."
            return 0
        else
            exit 1
        fi
    fi
    
    print_info "VPC CIDR: $VPC_CIDR"
    print_info "NAT Gateway: $ENABLE_NAT_GATEWAY"
    
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
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"},
  {"ParameterKey": "VpcCidr", "ParameterValue": "$VPC_CIDR"},
  {"ParameterKey": "EnableNatGateway", "ParameterValue": "$ENABLE_NAT_GATEWAY"}
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
    print_info "Stack outputs:"
    
    local vpc_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' \
        --output text 2>/dev/null)
    
    local private_subnets=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`PrivateSubnetIds`].OutputValue' \
        --output text 2>/dev/null)
    
    local lambda_sg=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`LambdaSecurityGroupId`].OutputValue' \
        --output text 2>/dev/null)
    
    [ -n "$vpc_id" ] && [ "$vpc_id" != "None" ] && print_info "  VPC ID: $vpc_id"
    [ -n "$private_subnets" ] && [ "$private_subnets" != "None" ] && print_info "  Private Subnets: $private_subnets"
    [ -n "$lambda_sg" ] && [ "$lambda_sg" != "None" ] && print_info "  Lambda Security Group: $lambda_sg"
}

# =============================================================================
# Delete Stack
# =============================================================================

delete_stack() {
    if [ "$USE_DEFAULTS" = "true" ]; then
        print_info "use_defaults: true — no CloudFormation stack is deployed; nothing to delete."
        return 0
    fi
    
    print_warning "This will delete:"
    print_warning "  - VPC and all subnets"
    print_warning "  - Internet Gateway"
    print_warning "  - NAT Gateway (if enabled)"
    print_warning "  - VPC Endpoints"
    print_warning "  - Security Groups"
    print_warning "  - Route Tables"
    echo ""
    print_warning "WARNING: This will affect all resources using this VPC!"
    print_warning "Make sure to delete dependent resources (DB, Lambda) first."
    
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete" || exit 0
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
        if [ "$USE_DEFAULTS" = "true" ]; then
            run_use_defaults_vpc_check || exit 1
        else
            do_validate_template
        fi
        ;;
    status)
        if [ "$USE_DEFAULTS" = "true" ]; then
            show_default_vpc_status
        else
            show_status
        fi
        ;;
    deploy|update)
        if [ "$USE_DEFAULTS" = "true" ]; then
            deploy_stack
        else
            do_validate_template
            deploy_stack
        fi
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
