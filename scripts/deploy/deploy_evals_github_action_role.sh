#!/bin/bash

# GitHub Actions IAM Role Deployment Script for Evaluations
# This script deploys IAM policies and the GitHub Actions role for OIDC authentication
# to enable GitHub Actions workflows to perform evaluations on AWS resources.
#
# PREREQUISITE: GitHub OIDC Identity Provider
# The GitHub OIDC identity provider must be created in your AWS account BEFORE using
# this script. See docs/oidc_github_identity_provider_setup.md for setup steps.
# You must pass the provider ARN with --oidc-provider-arn when deploying or updating.
#
# IMPORTANT: Environment-Specific Permissions
# The role deployed by this script is scoped to the environment you specify. GitHub Actions
# will only have permissions to access resources in that specific environment. For example:
# - If you deploy to "staging", GitHub Actions can only access staging resources (staging
#   Lambda functions, staging S3 buckets, staging Bedrock endpoints, etc.)
# - If you deploy to "dev", GitHub Actions can only access dev resources
# - If you deploy to "prod", GitHub Actions can only access production resources
#
# This ensures that evaluation workflows running in CI/CD can only interact with the
# environment they are intended to test, providing better security and isolation.
#
# It first deploys the required policies, then the role that uses them.
#
# Usage Examples:
#   # Deploy to development environment (OIDC provider ARN required)
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
#     --aws-account-id 123456789012 \
#     --github-org myorg \
#     --github-repo chat-template \
#     --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com
#
#   # Deploy to staging with custom branch
#   ./scripts/deploy/deploy_evals_github_action_role.sh staging deploy \
#     --aws-account-id 123456789012 \
#     --github-org myorg \
#     --github-repo chat-template \
#     --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com \
#     --github-source-branch main
#
#   # Deploy with Lambda policy (for lambda mode evaluations)
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
#     --aws-account-id 123456789012 \
#     --github-org myorg \
#     --github-repo chat-template \
#     --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com \
#     --include-lambda-policy
#
#   # Deploy with Bedrock knowledge base scoped to this environment (recommended for same-account multi-env)
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
#     --aws-account-id 123456789012 \
#     --github-org myorg \
#     --github-repo chat-template \
#     --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com \
#     --knowledge-base-id YOUR_KB_ID
#
#   # Validate templates before deployment
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev status

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}[EVALS GITHUB ACTIONS ROLE]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "GitHub Actions IAM Role Deployment Script"
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
    echo "  deploy    - Deploy the stacks (default)"
    echo "  update    - Update the stacks"
    echo "  delete    - Delete the stacks"
    echo "  validate  - Validate the templates"
    echo "  status    - Show stack status"
    echo ""
    echo "Required Options (for deploy/update):"
    echo "  --aws-account-id <id>        - AWS Account ID (12 digits)"
    echo "  --github-org <org>            - GitHub organization or username"
    echo "  --github-repo <repo>          - GitHub repository name"
    echo "  --oidc-provider-arn <arn>      - ARN of GitHub OIDC identity provider (create first; see docs/oidc_github_identity_provider_setup.md)"
    echo ""
    echo "Optional Options:"
    echo "  --github-source-branch <branch> - GitHub source branch for PRs (default: development)"
    echo "  --github-target-branch <branch>  - GitHub target branch for PRs (default: main)"
    echo "  --region <region>                - AWS region (default: us-east-1)"
    echo "  --include-lambda-policy        - Include Lambda invoke policy (for lambda mode)"
    echo "  --knowledge-base-id <id>      - Bedrock knowledge base ID for this environment (recommended for same-account multi-env)"
    echo "  --project-name <name>          - Project name (default: chat-template)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy --aws-account-id 123456789012 --github-org myorg --github-repo chat-template --oidc-provider-arn arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com"
    echo "  $0 staging deploy --aws-account-id 123456789012 --github-org myorg --github-repo chat-template --oidc-provider-arn arn:aws:iam::ACCOUNT:oidc-provider/token.actions.githubusercontent.com --github-source-branch main"
    echo "  $0 dev status"
    echo ""
    echo "Note: This script deploys policies first, then the role that uses them."
    echo "      The role ARN will be printed at the end for use in GitHub secrets."
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
GITHUB_SOURCE_BRANCH="development"
GITHUB_TARGET_BRANCH="main"
INCLUDE_LAMBDA_POLICY=false
OIDC_PROVIDER_ARN=""
KNOWLEDGE_BASE_ID=""
AWS_ACCOUNT_ID=""
GITHUB_ORG=""
GITHUB_REPO=""

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
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
        --github-source-branch)
            GITHUB_SOURCE_BRANCH="$2"
            shift 2
            ;;
        --github-target-branch)
            GITHUB_TARGET_BRANCH="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --include-lambda-policy)
            INCLUDE_LAMBDA_POLICY=true
            shift
            ;;
        --oidc-provider-arn)
            OIDC_PROVIDER_ARN="$2"
            shift 2
            ;;
        --knowledge-base-id)
            KNOWLEDGE_BASE_ID="$2"
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

print_header "Starting GitHub Actions role deployment for evaluations in $ENVIRONMENT environment"

# Validate environment
case $ENVIRONMENT in
    dev|staging|prod)
        print_status "Using environment: $ENVIRONMENT"
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
fi

# Stack names
SECRETS_MANAGER_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-secrets-manager-policy"
S3_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-s3-evaluation-policy"
BEDROCK_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-bedrock-evaluation-policy"
LAMBDA_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-lambda-invoke-policy"
ROLE_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-github-actions-role"

# Template files
SECRETS_MANAGER_POLICY_TEMPLATE="infra/policies/evals_secrets_manager_policy.yaml"
S3_POLICY_TEMPLATE="infra/policies/evals_s3_policy.yaml"
BEDROCK_POLICY_TEMPLATE="infra/policies/evals_bedrock_policy.yaml"
LAMBDA_POLICY_TEMPLATE="infra/policies/evals_lambda_policy.yaml"
ROLE_TEMPLATE="infra/roles/evals_github_action_role.yaml"

# Function to validate template
validate_template() {
    local template_file=$1
    local stack_name=$2
    
    print_status "Validating CloudFormation template: $template_file"
    if aws cloudformation validate-template --template-body file://$template_file --region $AWS_REGION >/dev/null 2>&1; then
        print_status "Template validation successful: $stack_name"
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
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED)
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

# Function to deploy policy stack
deploy_policy_stack() {
    local stack_name=$1
    local template_file=$2
    local param_file=$3
    
    print_status "Deploying policy stack: $stack_name"
    
    # Get current stack status before attempting update
    local initial_status=""
    if aws cloudformation describe-stacks --stack-name $stack_name --region $AWS_REGION >/dev/null 2>&1; then
        initial_status=$(aws cloudformation describe-stacks \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        print_status "Current stack status: $initial_status"
        print_warning "Stack $stack_name already exists. Attempting update..."
        
        local update_output=$(aws cloudformation update-stack \
            --stack-name $stack_name \
            --template-body file://$template_file \
            --parameters file://$param_file \
            --capabilities CAPABILITY_NAMED_IAM \
            --region $AWS_REGION 2>&1)
        local result=$?
        
        # Check output for "No updates are to be performed" regardless of exit code
        # Sometimes AWS CLI returns 0 but includes this message
        if echo "$update_output" | grep -qi "No updates are to be performed"; then
            print_status "No updates needed for stack $stack_name (template and parameters unchanged)"
            return 0
        fi
        
        if [ $result -ne 0 ]; then
            print_error "Stack update failed: $update_output"
            return 1
        fi
        
        # Update was triggered successfully - verify it actually started
        print_status "Update command succeeded (exit code 0). Verifying update was triggered..."
        print_status "Initial stack status: $initial_status"
        
        # Wait a moment for CloudFormation to process the update request
        sleep 3
        
        # Check stack status
        local verify_status=$(aws cloudformation describe-stacks \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        
        print_status "Stack status after update command: $verify_status"
        
        # Check the most recent stack event to see if an update was actually initiated
        # Look for UPDATE_IN_PROGRESS event for the stack itself (not resources)
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
        
        local event_time=$(aws cloudformation describe-stack-events \
            --stack-name $stack_name \
            --region $AWS_REGION \
            --max-items 1 \
            --query 'StackEvents[0].Timestamp' \
            --output text 2>/dev/null)
        
        # Determine if update was actually triggered
        local update_triggered=false
        
        if [ "$verify_status" = "UPDATE_IN_PROGRESS" ]; then
            update_triggered=true
            print_status "✓ Update confirmed: Stack transitioned to UPDATE_IN_PROGRESS"
        elif [ "$event_status" = "UPDATE_IN_PROGRESS" ] && [ "$event_type" = "AWS::CloudFormation::Stack" ]; then
            # Most recent event is an UPDATE_IN_PROGRESS for the stack itself
            update_triggered=true
            print_status "✓ Update confirmed: Found UPDATE_IN_PROGRESS event (time: $event_time)"
        elif [ "$verify_status" != "$initial_status" ]; then
            # Status changed but not to UPDATE_IN_PROGRESS - might be transitioning
            print_warning "Stack status changed from $initial_status to $verify_status"
            print_warning "Waiting to see if update starts..."
            sleep 5
            
            verify_status=$(aws cloudformation describe-stacks \
                --stack-name $stack_name \
                --region $AWS_REGION \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            
            if [ "$verify_status" = "UPDATE_IN_PROGRESS" ]; then
                update_triggered=true
                print_status "✓ Update confirmed: Stack is now in UPDATE_IN_PROGRESS state"
            fi
        fi
        
        if [ "$update_triggered" = false ]; then
            # No update was triggered - CloudFormation determined no changes are needed
            print_warning "Stack status unchanged after update command: $verify_status"
            
            if [ -n "$event_time" ]; then
                print_status "Most recent stack event: $event_status ($event_type) at $event_time"
            fi
            
            print_status "No updates needed for stack $stack_name"
            print_status "Template and parameters are identical to the current stack configuration"
            print_status "This is expected behavior when there are no changes to deploy."
            return 0  # Return 0 (success) since this is expected when no changes exist
        fi
    else
        print_status "Creating new stack: $stack_name"
        aws cloudformation create-stack \
            --stack-name $stack_name \
            --template-body file://$template_file \
            --parameters file://$param_file \
            --capabilities CAPABILITY_NAMED_IAM \
            --region $AWS_REGION
    fi
    
    print_status "Waiting for stack $stack_name to complete..."
    
    # Poll stack status with timeout (30 minutes max)
    local max_wait_time=1800  # 30 minutes in seconds
    local elapsed_time=0
    local poll_interval=10     # Check every 10 seconds
    local stack_status=""
    local is_update=false
    
    # Determine if this is an update or create
    if [ -n "$initial_status" ]; then
        is_update=true
    fi
    
    # Poll until stack reaches a terminal state
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
        
        # Check for terminal success states
        case "$stack_status" in
            CREATE_COMPLETE|UPDATE_COMPLETE)
                print_status "Stack $stack_name completed successfully"
                return 0
                ;;
            # Check for failure states
            ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED|ROLLBACK_FAILED)
                print_error "Stack $stack_name entered failed state: $stack_status"
                check_stack_status $stack_name
                exit 1
                ;;
            # Check for in-progress states
            CREATE_IN_PROGRESS|UPDATE_IN_PROGRESS|UPDATE_ROLLBACK_IN_PROGRESS|ROLLBACK_IN_PROGRESS|DELETE_IN_PROGRESS)
                # Still in progress, continue waiting
                if [ $((elapsed_time % 60)) -eq 0 ]; then
                    # Print status every minute
                    print_status "Stack $stack_name status: $stack_status (waiting...)"
                fi
                ;;
            *)
                # Unknown state, log and continue
                print_warning "Stack $stack_name in unknown state: $stack_status"
                ;;
        esac
        
        sleep $poll_interval
        elapsed_time=$((elapsed_time + poll_interval))
    done
    
    # Timeout reached
    print_error "Timeout waiting for stack $stack_name to complete (waited ${max_wait_time}s)"
    print_error "Current stack status: $stack_status"
    print_error "Check stack status manually: aws cloudformation describe-stacks --stack-name $stack_name --region $AWS_REGION"
    exit 1
}

# Function to show stack status
show_status() {
    print_status "Checking stack statuses..."
    echo ""
    
    local stacks=("$SECRETS_MANAGER_POLICY_STACK" "$S3_POLICY_STACK" "$BEDROCK_POLICY_STACK")
    if [ "$INCLUDE_LAMBDA_POLICY" = true ]; then
        stacks+=("$LAMBDA_POLICY_STACK")
    fi
    stacks+=("$ROLE_STACK")
    
    for stack in "${stacks[@]}"; do
        if aws cloudformation describe-stacks --stack-name $stack --region $AWS_REGION >/dev/null 2>&1; then
            local status=$(aws cloudformation describe-stacks \
                --stack-name $stack \
                --region $AWS_REGION \
                --query 'Stacks[0].StackStatus' \
                --output text)
            print_status "$stack: $status"
        else
            print_warning "$stack: Does not exist"
        fi
    done
}

# Function to validate all templates
validate_all_templates() {
    print_header "Validating all CloudFormation templates"
    
    validate_template "$SECRETS_MANAGER_POLICY_TEMPLATE" "$SECRETS_MANAGER_POLICY_STACK"
    validate_template "$S3_POLICY_TEMPLATE" "$S3_POLICY_STACK"
    validate_template "$BEDROCK_POLICY_TEMPLATE" "$BEDROCK_POLICY_STACK"
    if [ "$INCLUDE_LAMBDA_POLICY" = true ]; then
        validate_template "$LAMBDA_POLICY_TEMPLATE" "$LAMBDA_POLICY_STACK"
    fi
    validate_template "$ROLE_TEMPLATE" "$ROLE_STACK"
    
    print_status "All templates validated successfully"
}

# Function to delete stacks
delete_stacks() {
    print_warning "This will delete all stacks. Are you sure? (yes/no)"
    read -r confirmation
    
    if [ "$confirmation" != "yes" ]; then
        print_status "Deletion cancelled"
        return 0
    fi
    
    print_header "Deleting stacks in reverse order..."
    
    # Delete role first (depends on policies)
    if aws cloudformation describe-stacks --stack-name $ROLE_STACK --region $AWS_REGION >/dev/null 2>&1; then
        print_status "Deleting role stack: $ROLE_STACK"
        aws cloudformation delete-stack --stack-name $ROLE_STACK --region $AWS_REGION
        aws cloudformation wait stack-delete-complete --stack-name $ROLE_STACK --region $AWS_REGION
    fi
    
    # Delete policies
    local policy_stacks=("$LAMBDA_POLICY_STACK" "$BEDROCK_POLICY_STACK" "$S3_POLICY_STACK" "$SECRETS_MANAGER_POLICY_STACK")
    for stack in "${policy_stacks[@]}"; do
        if aws cloudformation describe-stacks --stack-name $stack --region $AWS_REGION >/dev/null 2>&1; then
            print_status "Deleting policy stack: $stack"
            aws cloudformation delete-stack --stack-name $stack --region $AWS_REGION
            aws cloudformation wait stack-delete-complete --stack-name $stack --region $AWS_REGION
        fi
    done
    
    print_status "All stacks deleted"
}

# Function to deploy all stacks
deploy_all_stacks() {
    print_header "Deploying GitHub Actions IAM role and policies for evaluations"
    
    # Create temporary directory for parameter files
    local temp_dir=$(mktemp -d)
    trap "rm -rf $temp_dir" EXIT
    
    # Deploy Secrets Manager Policy
    print_status "Deploying Secrets Manager policy..."
    local secrets_params_file="$temp_dir/secrets_params.json"
    cat > "$secrets_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"}
]
EOF
    deploy_policy_stack "$SECRETS_MANAGER_POLICY_STACK" "$SECRETS_MANAGER_POLICY_TEMPLATE" "$secrets_params_file"
    local SECRETS_MANAGER_POLICY_ARN=$(get_stack_output "$SECRETS_MANAGER_POLICY_STACK" "PolicyArn")
    
    # Deploy S3 Policy (scoped to this environment's evals bucket pattern)
    print_status "Deploying S3 evaluation policy..."
    local s3_bucket_pattern="${PROJECT_NAME}-evals-${ENVIRONMENT}"
    local s3_params_file="$temp_dir/s3_params.json"
    cat > "$s3_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "S3BucketPattern", "ParameterValue": "$s3_bucket_pattern"}
]
EOF
    deploy_policy_stack "$S3_POLICY_STACK" "$S3_POLICY_TEMPLATE" "$s3_params_file"
    local S3_POLICY_ARN=$(get_stack_output "$S3_POLICY_STACK" "PolicyArn")
    
    # Deploy Bedrock Policy (optionally scoped to this environment's knowledge base)
    print_status "Deploying Bedrock evaluation policy..."
    local bedrock_params_file="$temp_dir/bedrock_params.json"
    if [ -n "$KNOWLEDGE_BASE_ID" ]; then
        cat > "$bedrock_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"},
  {"ParameterKey": "KnowledgeBaseId", "ParameterValue": "$KNOWLEDGE_BASE_ID"}
]
EOF
    else
        cat > "$bedrock_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"}
]
EOF
    fi
    deploy_policy_stack "$BEDROCK_POLICY_STACK" "$BEDROCK_POLICY_TEMPLATE" "$bedrock_params_file"
    local BEDROCK_POLICY_ARN=$(get_stack_output "$BEDROCK_POLICY_STACK" "PolicyArn")
    
    # Deploy Lambda Policy (optional)
    local LAMBDA_POLICY_ARN=""
    if [ "$INCLUDE_LAMBDA_POLICY" = true ]; then
        print_status "Deploying Lambda invoke policy..."
        local lambda_params_file="$temp_dir/lambda_params.json"
        cat > "$lambda_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"}
]
EOF
        deploy_policy_stack "$LAMBDA_POLICY_STACK" "$LAMBDA_POLICY_TEMPLATE" "$lambda_params_file"
        LAMBDA_POLICY_ARN=$(get_stack_output "$LAMBDA_POLICY_STACK" "PolicyArn")
    fi
    
    # Deploy GitHub Actions Role
    print_status "Deploying GitHub Actions role..."
    local role_params_file="$temp_dir/role_params.json"
    
    # Build role parameters
    local role_params="[
  {\"ParameterKey\": \"ProjectName\", \"ParameterValue\": \"$PROJECT_NAME\"},
  {\"ParameterKey\": \"Environment\", \"ParameterValue\": \"$ENVIRONMENT\"},
  {\"ParameterKey\": \"GitHubOrg\", \"ParameterValue\": \"$GITHUB_ORG\"},
  {\"ParameterKey\": \"GitHubRepo\", \"ParameterValue\": \"$GITHUB_REPO\"},
  {\"ParameterKey\": \"GitHubSourceBranch\", \"ParameterValue\": \"$GITHUB_SOURCE_BRANCH\"},
  {\"ParameterKey\": \"GitHubTargetBranch\", \"ParameterValue\": \"$GITHUB_TARGET_BRANCH\"},
  {\"ParameterKey\": \"SecretsManagerPolicyArn\", \"ParameterValue\": \"$SECRETS_MANAGER_POLICY_ARN\"},
  {\"ParameterKey\": \"S3EvaluationPolicyArn\", \"ParameterValue\": \"$S3_POLICY_ARN\"},
  {\"ParameterKey\": \"BedrockEvaluationPolicyArn\", \"ParameterValue\": \"$BEDROCK_POLICY_ARN\"}"
    
    if [ -n "$LAMBDA_POLICY_ARN" ]; then
        role_params="$role_params,
  {\"ParameterKey\": \"LambdaInvokePolicyArn\", \"ParameterValue\": \"$LAMBDA_POLICY_ARN\"}"
    fi

    role_params="$role_params,
  {\"ParameterKey\": \"OIDCProviderArn\", \"ParameterValue\": \"$OIDC_PROVIDER_ARN\"}"
    role_params="$role_params
]"
    
    echo "$role_params" > "$role_params_file"
    
    deploy_policy_stack "$ROLE_STACK" "$ROLE_TEMPLATE" "$role_params_file"
    local ROLE_ARN=$(get_stack_output "$ROLE_STACK" "RoleArn")
    
    # Print summary
    echo ""
    print_header "Deployment Summary"
    echo ""
    print_status "Secrets Manager Policy ARN: $SECRETS_MANAGER_POLICY_ARN"
    print_status "S3 Evaluation Policy ARN: $S3_POLICY_ARN"
    print_status "Bedrock Evaluation Policy ARN: $BEDROCK_POLICY_ARN"
    if [ -n "$LAMBDA_POLICY_ARN" ]; then
        print_status "Lambda Invoke Policy ARN: $LAMBDA_POLICY_ARN"
    fi
    echo ""
    print_status "GitHub Actions Role ARN: $ROLE_ARN"
    echo ""
    print_warning "IMPORTANT: Add this role ARN to your GitHub repository secrets:"
    print_warning "  Secret name: AWS_ROLE_ARN"
    print_warning "  Secret value: $ROLE_ARN"
    echo ""
    print_status "You can find this in: Repository Settings → Secrets and variables → Actions"
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

