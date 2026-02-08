#!/bin/bash

# GitHub Actions Deployer IAM Role Deployment Script
# This script deploys the IAM role used by the deploy workflow (.github/workflows/deploy.yml)
# for OIDC authentication. The role has permissions to run the full deployment (network,
# S3, DB, knowledge base, Lambda, cost tags) scoped to the specified environment.
#
# PREREQUISITE: GitHub OIDC Identity Provider
# The GitHub OIDC identity provider must be created in your AWS account BEFORE using
# this script. See docs/oidc_github_identity_provider_setup.md for setup steps.
# You must pass the provider ARN with --oidc-provider-arn when deploying or updating.
#
# Deploy one stack per environment (dev, staging, prod). Add the role ARN as the
# AWS_DEPLOYER_ROLE_ARN secret for the matching GitHub environment so the deploy
# workflow can assume this role.
#
# Usage Examples:
#   # Deploy to development environment (OIDC provider ARN required)
#   ./scripts/deploy/deploy_deployer_github_action_role.sh dev deploy \
#     --aws-account-id 123456789012 \
#     --github-org myorg \
#     --github-repo chat-template \
#     --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com
#
#   # Deploy to staging
#   ./scripts/deploy/deploy_deployer_github_action_role.sh staging deploy \
#     --aws-account-id 123456789012 \
#     --github-org myorg \
#     --github-repo chat-template \
#     --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_deployer_github_action_role.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_deployer_github_action_role.sh dev status

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# Function to show usage
show_usage() {
    echo "GitHub Actions Deployer IAM Role Deployment Script"
    echo ""
    echo "PREREQUISITE: Create the GitHub OIDC identity provider in AWS before using this script."
    echo "See docs/oidc_github_identity_provider_setup.md for setup steps."
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment (default)"
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
    echo "Required Options (for deploy/update):"
    echo "  --aws-account-id <id>       - AWS Account ID (12 digits)"
    echo "  --github-org <org>          - GitHub organization or username"
    echo "  --github-repo <repo>        - GitHub repository name"
    echo "  --oidc-provider-arn <arn>   - ARN of GitHub OIDC identity provider (create first; see docs)"
    echo ""
    echo "Optional Options:"
    echo "  --region <region>           - AWS region (default: us-east-1)"
    echo "  --project-name <name>      - Project name (default: chat-template)"
    echo "  -y, --yes                   - Skip confirmation prompt (deploy/update/delete)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy --aws-account-id 123456789012 --github-org myorg --github-repo chat-template --oidc-provider-arn arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
    echo "  $0 staging deploy --aws-account-id 123456789012 --github-org myorg --github-repo chat-template --oidc-provider-arn arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
    echo "  $0 dev status"
    echo ""
    echo "After deployment, add the role ARN to the GitHub environment secret AWS_DEPLOYER_ROLE_ARN"
    echo "(Repository Settings → Environments → <env> → Environment secrets)."
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=${1:-dev}
ACTION=${2:-deploy}
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"
OIDC_PROVIDER_ARN=""
AWS_ACCOUNT_ID=""
GITHUB_ORG=""
GITHUB_REPO=""

PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"
AUTO_CONFIRM=false

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --aws-account-id)
            AWS_ACCOUNT_ID="$2"
            shift 2
            ;;
        --github-org)
            GITHUB_ORG="$2"
            shift 2
            ;;
        --github-repo)
            GITHUB_REPO="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --oidc-provider-arn)
            OIDC_PROVIDER_ARN="$2"
            shift 2
            ;;
        --project-name)
            PROJECT_NAME="$2"
            shift 2
            ;;
        *)
            # If it's not a recognized option, it might be the action
            if [[ "$ACTION" == "deploy" && "$1" != "deploy" && "$1" != "update" && "$1" != "delete" && "$1" != "validate" && "$1" != "status" ]]; then
                ACTION="$1"
            fi
            shift
            ;;
    esac
done

print_header "Starting deployer role deployment for $ENVIRONMENT environment"

# Validate environment
case $ENVIRONMENT in
    dev|staging|prod)
        print_info "Using environment: $ENVIRONMENT"
        ;;
    *)
        print_error "Invalid environment: $ENVIRONMENT"
        show_usage
        exit 1
        ;;
esac

# Validate required parameters for deploy/update actions
if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        print_error "AWS Account ID is required (--aws-account-id)"
        show_usage
        exit 1
    fi

    if [ -z "$GITHUB_ORG" ]; then
        print_error "GitHub organization is required (--github-org)"
        show_usage
        exit 1
    fi

    if [ -z "$GITHUB_REPO" ]; then
        print_error "GitHub repository is required (--github-repo)"
        show_usage
        exit 1
    fi

    # Validate AWS Account ID format (12 digits)
    if ! [[ "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
        print_error "Invalid AWS Account ID format. Must be 12 digits."
        exit 1
    fi

    if [ -z "$OIDC_PROVIDER_ARN" ]; then
        print_error "OIDC provider ARN is required (--oidc-provider-arn). Create the GitHub OIDC identity provider first; see docs/oidc_github_identity_provider_setup.md"
        show_usage
        exit 1
    fi

    print_step "Deploying deployer role for $ENVIRONMENT"
    print_info "Environment: $ENVIRONMENT | Region: $AWS_REGION | Stack: ${PROJECT_NAME}-${ENVIRONMENT}-deployer-role"
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Proceed with $ACTION?" || exit 0
    fi
fi

# Stack and template
ROLE_STACK="${PROJECT_NAME}-${ENVIRONMENT}-deployer-role"
ROLE_TEMPLATE="infra/roles/deployer_role.yaml"

# Function to validate template
validate_template() {
    local template_file=$1
    local stack_name=$2

    print_info "Validating CloudFormation template: $template_file"
    if aws cloudformation validate-template --template-body file://$template_file --region $AWS_REGION >/dev/null 2>&1; then
        print_info "Template validation successful: $stack_name"
    else
        print_error "Template validation failed: $template_file"
        exit 1
    fi
}

# Function to check stack status and print errors
check_stack_status() {
    local stack_name=$1
    local stack_status=$(aws cloudformation describe-stacks \
        --stack-name $stack_name \
        --region $AWS_REGION \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null)

    if [ -z "$stack_status" ]; then
        return 1
    fi

    case "$stack_status" in
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED|ROLLBACK_FAILED)
            print_error "Stack $stack_name is in failed state: $stack_status"
            echo ""

            # Get stack status reason
            local status_reason=$(aws cloudformation describe-stacks \
                --stack-name $stack_name \
                --region $AWS_REGION \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)

            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Stack Status Reason: $status_reason"
                echo ""
            fi

            # Get recent stack events with errors
            print_error "Recent stack events with errors:"
            echo ""
            aws cloudformation describe-stack-events \
                --stack-name $stack_name \
                --region $AWS_REGION \
                --max-items 20 \
                --query 'StackEvents[?contains(ResourceStatus, `FAILED`) || contains(ResourceStatus, `ROLLBACK`)].{Time:Timestamp,Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}' \
                --output table 2>/dev/null || true

            echo ""
            print_error "For more details, check the AWS Console or run:"
            print_error "aws cloudformation describe-stack-events --stack-name $stack_name --region $AWS_REGION"
            echo ""
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

# Function to get stack output
get_stack_output() {
    local stack_name=$1
    local output_key=$2
    aws cloudformation describe-stacks \
        --stack-name $stack_name \
        --region $AWS_REGION \
        --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
        --output text 2>/dev/null
}

# Function to deploy the role stack
deploy_stack() {
    local stack_name=$1
    local template_file=$2
    local param_file=$3

    print_info "Deploying stack: $stack_name"

    # Get current stack status before attempting update
    local initial_status=""
    if aws cloudformation describe-stacks --stack-name $stack_name --region $AWS_REGION >/dev/null 2>&1; then
        initial_status=$(aws cloudformation describe-stacks \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        print_info "Current stack status: $initial_status"
        print_warning "Stack $stack_name already exists. Attempting update..."

        local update_output=$(aws cloudformation update-stack \
            --stack-name $stack_name \
            --template-body file://$template_file \
            --parameters file://$param_file \
            --capabilities CAPABILITY_NAMED_IAM \
            --region $AWS_REGION 2>&1)
        local result=$?

        # Check output for "No updates are to be performed" regardless of exit code
        if echo "$update_output" | grep -qi "No updates are to be performed"; then
            print_info "No updates needed for stack $stack_name (template and parameters unchanged)"
            return 0
        fi

        if [ $result -ne 0 ]; then
            print_error "Stack update failed: $update_output"
            return 1
        fi

        # Update was triggered successfully - verify it actually started
        print_info "Update command succeeded. Verifying update was triggered..."
        sleep 3

        local verify_status=$(aws cloudformation describe-stacks \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)

        local update_triggered=false
        if [ "$verify_status" = "UPDATE_IN_PROGRESS" ]; then
            update_triggered=true
            print_info "✓ Update confirmed: Stack transitioned to UPDATE_IN_PROGRESS"
        fi

        local event_status=$(aws cloudformation describe-stack-events \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --max-items 1 \
            --query 'StackEvents[0].ResourceStatus' \
            --output text 2>/dev/null)
        local event_type=$(aws cloudformation describe-stack-events \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --max-items 1 \
            --query 'StackEvents[0].ResourceType' \
            --output text 2>/dev/null)

        if [ "$event_status" = "UPDATE_IN_PROGRESS" ] && [ "$event_type" = "AWS::CloudFormation::Stack" ]; then
            update_triggered=true
        fi

        if [ "$update_triggered" = false ] && [ "$verify_status" != "$initial_status" ]; then
            sleep 5
            verify_status=$(aws cloudformation describe-stacks \
                --stack-name $stack_name \
                --region $AWS_REGION \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            [ "$verify_status" = "UPDATE_IN_PROGRESS" ] && update_triggered=true
        fi

        if [ "$update_triggered" = false ]; then
            print_info "No updates needed for stack $stack_name"
            return 0
        fi
    else
        print_info "Creating new stack: $stack_name"
        aws cloudformation create-stack \
            --stack-name $stack_name \
            --template-body file://$template_file \
            --parameters file://$param_file \
            --capabilities CAPABILITY_NAMED_IAM \
            --region $AWS_REGION
    fi

    print_info "Waiting for stack $stack_name to complete..."

    local max_wait_time=1800
    local elapsed_time=0
    local poll_interval=10
    local stack_status=""

    while [ $elapsed_time -lt $max_wait_time ]; do
        stack_status=$(aws cloudformation describe-stacks \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)

        if [ -z "$stack_status" ]; then
            print_error "Stack $stack_name not found"
            return 1
        fi

        case "$stack_status" in
            CREATE_COMPLETE|UPDATE_COMPLETE)
                print_info "Stack $stack_name completed successfully"
                return 0
                ;;
            ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED|ROLLBACK_FAILED)
                print_error "Stack $stack_name entered failed state: $stack_status"
                check_stack_status $stack_name
                exit 1
                ;;
            CREATE_IN_PROGRESS|UPDATE_IN_PROGRESS|UPDATE_ROLLBACK_IN_PROGRESS|ROLLBACK_IN_PROGRESS|DELETE_IN_PROGRESS)
                if [ $((elapsed_time % 60)) -eq 0 ]; then
                    print_info "Stack $stack_name status: $stack_status (waiting...)"
                fi
                ;;
            *)
                print_warning "Stack $stack_name in unknown state: $stack_status"
                ;;
        esac

        sleep $poll_interval
        elapsed_time=$((elapsed_time + poll_interval))
    done

    print_error "Timeout waiting for stack $stack_name to complete (waited ${max_wait_time}s)"
    print_error "Current stack status: $stack_status"
    exit 1
}

# Function to show stack status
show_status() {
    print_info "Checking stack status..."
    echo ""

    if aws cloudformation describe-stacks --stack-name $ROLE_STACK --region $AWS_REGION >/dev/null 2>&1; then
        local status=$(aws cloudformation describe-stacks \
            --stack-name $ROLE_STACK \
            --region $AWS_REGION \
            --query 'Stacks[0].StackStatus' \
            --output text)
        print_info "$ROLE_STACK: $status"
    else
        print_warning "$ROLE_STACK: Does not exist"
    fi
}

# Function to validate template
validate_all_templates() {
    print_header "Validating CloudFormation template"
    validate_template "$ROLE_TEMPLATE" "$ROLE_STACK"
    print_info "Template validated successfully"
}

# Function to delete stack
delete_stacks() {
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete deployer role stack ($ROLE_STACK)" || return 0
    fi

    print_step "Deleting stack..."

    if aws cloudformation describe-stacks --stack-name $ROLE_STACK --region $AWS_REGION >/dev/null 2>&1; then
        print_info "Deleting stack: $ROLE_STACK"
        aws cloudformation delete-stack --stack-name $ROLE_STACK --region $AWS_REGION
        aws cloudformation wait stack-delete-complete --stack-name $ROLE_STACK --region $AWS_REGION
        print_info "Stack deleted"
    else
        print_warning "Stack $ROLE_STACK does not exist"
    fi
}

# Function to deploy the role stack
deploy_all_stacks() {
    print_step "Deploying GitHub Actions deployer IAM role"

    local temp_dir=$(mktemp -d)
    trap "rm -rf $temp_dir" EXIT

    print_info "Deploying deployer role..."
    local role_params_file="$temp_dir/role_params.json"
    cat > "$role_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "GitHubOrg", "ParameterValue": "$GITHUB_ORG"},
  {"ParameterKey": "GitHubRepo", "ParameterValue": "$GITHUB_REPO"},
  {"ParameterKey": "OIDCProviderArn", "ParameterValue": "$OIDC_PROVIDER_ARN"}
]
EOF

    deploy_stack "$ROLE_STACK" "$ROLE_TEMPLATE" "$role_params_file"
    local ROLE_ARN=$(get_stack_output "$ROLE_STACK" "RoleArn")

    echo ""
    print_step "Deployment Summary"
    echo ""
    print_info "GitHub Actions Deployer Role ARN: $ROLE_ARN"
    echo ""
    print_warning "IMPORTANT: Add this role ARN to your GitHub environment secret:"
    print_warning "  Secret name: AWS_DEPLOYER_ROLE_ARN"
    print_warning "  Secret value: $ROLE_ARN"
    echo ""
    print_info "Go to: Repository Settings → Environments → $ENVIRONMENT → Environment secrets"
    print_info "Add or update AWS_DEPLOYER_ROLE_ARN with the value above so the deploy workflow can assume this role."
}

# Main execution
case $ACTION in
    deploy)
        deploy_all_stacks
        ;;
    update)
        deploy_all_stacks
        ;;
    validate)
        validate_all_templates
        ;;
    status)
        show_status
        ;;
    delete)
        delete_stacks
        ;;
    *)
        print_error "Unknown action: $ACTION"
        show_usage
        exit 1
        ;;
esac
