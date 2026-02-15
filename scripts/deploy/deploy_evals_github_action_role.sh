#!/bin/bash

# GitHub Actions IAM Role Deployment Script for Evaluations
# This script deploys IAM policies and the GitHub Actions role for OIDC authentication
# to enable GitHub Actions workflows to perform evaluations on AWS resources.
#
# Reads configuration from infra/infra.yaml and uses the CLI role profile per environment
# to assume into the target account. GitHub org/repo come from infra (github.github_repo);
# use --github-org/--github-repo to override. OIDC provider is discovered in the account
# when not passed via --oidc-provider-arn.
#
# PREREQUISITE: GitHub OIDC Identity Provider must exist in the target account (e.g. via
# setup_oidc_provider.sh). See docs/oidc_github_identity_provider_setup.md.
#
# It first deploys the required policies, then the role that uses them.
#
# Usage Examples:
#   # Deploy to dev (uses infra.yaml + CLI profile for dev)
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy -y
#
#   # Override GitHub org/repo or pass OIDC ARN
#   ./scripts/deploy/deploy_evals_github_action_role.sh staging deploy --github-org myorg --github-repo myrepo
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy --oidc-provider-arn arn:aws:iam::ACCOUNT:oidc-provider/...
#
#   # Optional: Lambda policy, Bedrock knowledge base, branches
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy --include-lambda-policy --knowledge-base-id ID
#
#   # Validate or status
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev validate
#   ./scripts/deploy/deploy_evals_github_action_role.sh dev status

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/github_repo.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# Function to show usage
show_usage() {
    echo "GitHub Actions Evals Role Deployment Script"
    echo ""
    echo "Reads infra/infra.yaml and uses CLI role profile per environment."
    echo "GitHub org/repo from github.github_repo; OIDC provider discovered if not passed."
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments: dev, staging, prod"
    echo "Actions: deploy (default), update, delete, validate, status"
    echo ""
    echo "Options (override infra when provided):"
    echo "  --github-org <org>            - Override GitHub org"
    echo "  --github-repo <repo>          - Override GitHub repo"
    echo "  --oidc-provider-arn <arn>      - Override OIDC provider (default: discover in account)"
    echo "  --github-source-branch <branch> - Source branch for PRs (default: development)"
    echo "  --github-target-branch <branch>  - Target branch for PRs (default: main)"
    echo "  --region <region>             - Override AWS region"
    echo "  --project-name <name>         - Override project name"
    echo "  --include-lambda-policy      - Include Lambda invoke policy"
    echo "  --knowledge-base-id <id>      - Bedrock knowledge base ID for this environment"
    echo "  -y, --yes                     - Skip confirmation prompt"
    echo ""
    echo "Example: $0 dev deploy -y"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

# Parse action (optional second arg)
ACTION="deploy"
if [[ $# -gt 0 && "$1" =~ ^(deploy|update|delete|validate|status)$ ]]; then
    ACTION=$1
    shift
fi

# Defaults (overridden by infra or flags)
GITHUB_SOURCE_BRANCH="development"
GITHUB_TARGET_BRANCH="main"
INCLUDE_LAMBDA_POLICY=true
OIDC_PROVIDER_ARN=""
KNOWLEDGE_BASE_ID=""
GITHUB_ORG=""
GITHUB_REPO=""
AUTO_CONFIRM=false

# Parse options
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
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
            AWS_REGION_OVERRIDE="$2"
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
            PROJECT_NAME_OVERRIDE="$2"
            shift 2
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

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration from infra.yaml
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Load from infra (overridable by flags)
PROJECT_NAME=$(get_project_name)
[ -n "${PROJECT_NAME_OVERRIDE:-}" ] && PROJECT_NAME="$PROJECT_NAME_OVERRIDE"
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
[ -n "${AWS_REGION_OVERRIDE:-}" ] && AWS_REGION="$AWS_REGION_OVERRIDE"
AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
[ -z "$GITHUB_ORG" ] && GITHUB_ORG=$(get_github_org 2>/dev/null || echo "")
[ -z "$GITHUB_REPO" ] && GITHUB_REPO=$(get_github_repo 2>/dev/null || echo "")
if [ -z "$GITHUB_ORG" ] || [ -z "$GITHUB_REPO" ]; then
    if resolve_github_org_repo; then
        [ -z "$GITHUB_ORG" ] && GITHUB_ORG="$RESOLVED_GITHUB_ORG"
        [ -z "$GITHUB_REPO" ] && GITHUB_REPO="$RESOLVED_GITHUB_REPO"
        print_info "GitHub org/repo from git remote: ${GITHUB_ORG}/${GITHUB_REPO}"
    fi
fi

# Role stack and template from infra
ROLE_STACK=$(get_role_stack_name "evals" "$ENVIRONMENT")
ROLE_TEMPLATE=$(get_role_template "evals")

# Policy stack names and template paths (not in infra; use project root)
SECRETS_MANAGER_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-secrets-manager-policy"
S3_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-s3-evaluation-policy"
BEDROCK_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-bedrock-evaluation-policy"
LAMBDA_POLICY_STACK="${PROJECT_NAME}-${ENVIRONMENT}-evals-lambda-invoke-policy"
SECRETS_MANAGER_POLICY_TEMPLATE="$PROJECT_ROOT/infra/policies/evals_secrets_manager_policy.yaml"
S3_POLICY_TEMPLATE="$PROJECT_ROOT/infra/policies/evals_s3_policy.yaml"
BEDROCK_POLICY_TEMPLATE="$PROJECT_ROOT/infra/policies/evals_bedrock_policy.yaml"
LAMBDA_POLICY_TEMPLATE="$PROJECT_ROOT/infra/policies/evals_lambda_policy.yaml"

# AWS CLI helper (uses CLI profile and region from config)
aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# Discover OIDC provider in account when not provided
get_oidc_provider_arn() {
    aws_cmd iam list-open-id-connect-providers \
        --query "OpenIDConnectProviderList[?ends_with(Arn, 'token.actions.githubusercontent.com')].Arn" \
        --output text 2>/dev/null
}

# For deploy/update: validate required params and discover OIDC if needed
if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    if [ -z "$GITHUB_ORG" ] || [ -z "$GITHUB_REPO" ]; then
        print_error "GitHub org and repo are required. Set github.github_repo in infra/infra.yaml, run from a repo with origin pointing at GitHub, or use --github-org and --github-repo"
        exit 1
    fi
    if [ -z "$OIDC_PROVIDER_ARN" ]; then
        OIDC_PROVIDER_ARN=$(get_oidc_provider_arn) || true
        if [ -z "$OIDC_PROVIDER_ARN" ] || [ "$OIDC_PROVIDER_ARN" = "None" ]; then
            print_error "GitHub OIDC provider not found in account. Run setup_oidc_provider.sh first or pass --oidc-provider-arn"
            exit 1
        fi
        print_info "Using OIDC provider: $OIDC_PROVIDER_ARN"
    fi
    if [ "$AUTO_CONFIRM" = false ]; then
        print_step "Evals role deployment for $ENVIRONMENT (profile: ${AWS_PROFILE:-default})"
        print_info "Environment: $ENVIRONMENT | Region: $AWS_REGION"
        confirm_deployment "Proceed with $ACTION evals role?" || exit 0
    fi
fi

# Function to validate template
validate_template() {
    local template_file=$1
    local stack_name=$2
    
    print_info "Validating CloudFormation template: $template_file"
    if aws_cmd cloudformation validate-template --template-body "file://$template_file" >/dev/null 2>&1; then
        print_info "Template validation successful: $stack_name"
    else
        print_error "Template validation failed: $template_file"
        exit 1
    fi
}

# Function to check stack status and print errors
check_stack_status() {
    local stack_name=$1
    local stack_status=$(aws_cmd cloudformation describe-stacks \
        --stack-name $stack_name \
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
            local status_reason=$(aws_cmd cloudformation describe-stacks \
                --stack-name $stack_name \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)
            
            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Stack Status Reason: $status_reason"
                echo ""
            fi
            
            # Get recent stack events with errors
            print_error "Recent stack events with errors:"
            echo ""
            aws_cmd cloudformation describe-stack-events \
                --stack-name $stack_name \
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
    aws_cmd cloudformation describe-stacks \
        --stack-name $stack_name \
        --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
        --output text 2>/dev/null
}

# Function to deploy policy stack
deploy_policy_stack() {
    local stack_name=$1
    local template_file=$2
    local param_file=$3
    
    print_info "Deploying policy stack: $stack_name"
    
    # Get current stack status before attempting update
    local initial_status=""
    if aws_cmd cloudformation describe-stacks --stack-name $stack_name >/dev/null 2>&1; then
        initial_status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $stack_name \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        print_info "Current stack status: $initial_status"
        print_warning "Stack $stack_name already exists. Attempting update..."
        
        local update_output=$(aws_cmd cloudformation update-stack \
            --stack-name $stack_name \
            --template-body "file://$template_file" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1)
        local result=$?
        
        # Check output for "No updates are to be performed" regardless of exit code
        # Sometimes AWS CLI returns 0 but includes this message
        if echo "$update_output" | grep -qi "No updates are to be performed"; then
            print_info "No updates needed for stack $stack_name (template and parameters unchanged)"
            return 0
        fi
        
        if [ $result -ne 0 ]; then
            print_error "Stack update failed: $update_output"
            return 1
        fi
        
        # Update was triggered successfully - verify it actually started
        print_info "Update command succeeded (exit code 0). Verifying update was triggered..."
        print_info "Initial stack status: $initial_status"
        
        # Wait a moment for CloudFormation to process the update request
        sleep 3
        
        # Check stack status
        local verify_status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $stack_name \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        
        print_info "Stack status after update command: $verify_status"
        
        # Check the most recent stack event to see if an update was actually initiated
        # Look for UPDATE_IN_PROGRESS event for the stack itself (not resources)
        local event_status=$(aws_cmd cloudformation describe-stack-events \
            --stack-name $stack_name \
            --max-items 1 \
            --query 'StackEvents[0].ResourceStatus' \
            --output text 2>/dev/null)
        
        local event_type=$(aws_cmd cloudformation describe-stack-events \
            --stack-name $stack_name \
            --max-items 1 \
            --query 'StackEvents[0].ResourceType' \
            --output text 2>/dev/null)
        
        local event_time=$(aws_cmd cloudformation describe-stack-events \
            --stack-name $stack_name \
            --max-items 1 \
            --query 'StackEvents[0].Timestamp' \
            --output text 2>/dev/null)
        
        # Determine if update was actually triggered
        local update_triggered=false
        
        if [ "$verify_status" = "UPDATE_IN_PROGRESS" ]; then
            update_triggered=true
            print_info "✓ Update confirmed: Stack transitioned to UPDATE_IN_PROGRESS"
        elif [ "$event_status" = "UPDATE_IN_PROGRESS" ] && [ "$event_type" = "AWS::CloudFormation::Stack" ]; then
            # Most recent event is an UPDATE_IN_PROGRESS for the stack itself
            update_triggered=true
            print_info "✓ Update confirmed: Found UPDATE_IN_PROGRESS event (time: $event_time)"
        elif [ "$verify_status" != "$initial_status" ]; then
            # Status changed but not to UPDATE_IN_PROGRESS - might be transitioning
            print_warning "Stack status changed from $initial_status to $verify_status"
            print_warning "Waiting to see if update starts..."
            sleep 5
            
            verify_status=$(aws_cmd cloudformation describe-stacks \
                --stack-name $stack_name \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            
            if [ "$verify_status" = "UPDATE_IN_PROGRESS" ]; then
                update_triggered=true
                print_info "✓ Update confirmed: Stack is now in UPDATE_IN_PROGRESS state"
            fi
        fi
        
        if [ "$update_triggered" = false ]; then
            # No update was triggered - CloudFormation determined no changes are needed
            print_warning "Stack status unchanged after update command: $verify_status"
            
            if [ -n "$event_time" ]; then
                print_info "Most recent stack event: $event_status ($event_type) at $event_time"
            fi
            
            print_info "No updates needed for stack $stack_name"
            print_info "Template and parameters are identical to the current stack configuration"
            print_info "This is expected behavior when there are no changes to deploy."
            return 0  # Return 0 (success) since this is expected when no changes exist
        fi
    else
        print_info "Creating new stack: $stack_name"
        aws_cmd cloudformation create-stack \
            --stack-name $stack_name \
            --template-body "file://$template_file" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM
    fi
    
    print_info "Waiting for stack $stack_name to complete..."
    
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
        stack_status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $stack_name \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        
        if [ -z "$stack_status" ]; then
            print_error "Stack $stack_name not found"
            return 1
        fi
        
        # Check for terminal success states
        case "$stack_status" in
            CREATE_COMPLETE|UPDATE_COMPLETE)
                print_info "Stack $stack_name completed successfully"
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
                    print_info "Stack $stack_name status: $stack_status (waiting...)"
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
    print_info "Checking stack statuses..."
    echo ""
    
    local stacks=("$SECRETS_MANAGER_POLICY_STACK" "$S3_POLICY_STACK" "$BEDROCK_POLICY_STACK")
    if [ "$INCLUDE_LAMBDA_POLICY" = true ]; then
        stacks+=("$LAMBDA_POLICY_STACK")
    fi
    stacks+=("$ROLE_STACK")
    
    for stack in "${stacks[@]}"; do
        if aws_cmd cloudformation describe-stacks --stack-name $stack >/dev/null 2>&1; then
            local status=$(aws_cmd cloudformation describe-stacks \
                --stack-name $stack \
                --query 'Stacks[0].StackStatus' \
                --output text)
            print_info "$stack: $status"
        else
            print_warning "$stack: Does not exist"
        fi
    done
}

# Function to validate all templates
validate_all_templates() {
    print_step "Validating all CloudFormation templates"
    
    validate_template "$SECRETS_MANAGER_POLICY_TEMPLATE" "$SECRETS_MANAGER_POLICY_STACK"
    validate_template "$S3_POLICY_TEMPLATE" "$S3_POLICY_STACK"
    validate_template "$BEDROCK_POLICY_TEMPLATE" "$BEDROCK_POLICY_STACK"
    if [ "$INCLUDE_LAMBDA_POLICY" = true ]; then
        validate_template "$LAMBDA_POLICY_TEMPLATE" "$LAMBDA_POLICY_STACK"
    fi
    validate_template "$ROLE_TEMPLATE" "$ROLE_STACK"
    
    print_info "All templates validated successfully"
}

# Function to delete stacks
delete_stacks() {
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete evals role and policy stacks" || return 0
    fi

    print_step "Deleting stacks in reverse order..."

    # Delete role first (depends on policies)
    if aws_cmd cloudformation describe-stacks --stack-name $ROLE_STACK >/dev/null 2>&1; then
        print_info "Deleting role stack: $ROLE_STACK"
        aws_cmd cloudformation delete-stack --stack-name $ROLE_STACK
        aws_cmd cloudformation wait stack-delete-complete --stack-name $ROLE_STACK
    fi
    
    # Delete policies
    local policy_stacks=("$LAMBDA_POLICY_STACK" "$BEDROCK_POLICY_STACK" "$S3_POLICY_STACK" "$SECRETS_MANAGER_POLICY_STACK")
    for stack in "${policy_stacks[@]}"; do
        if aws_cmd cloudformation describe-stacks --stack-name $stack >/dev/null 2>&1; then
            print_info "Deleting policy stack: $stack"
            aws_cmd cloudformation delete-stack --stack-name $stack
            aws_cmd cloudformation wait stack-delete-complete --stack-name $stack
        fi
    done
    
    print_info "All stacks deleted"
}

# Function to deploy all stacks
deploy_all_stacks() {
    print_step "Deploying GitHub Actions IAM role and policies for evaluations"
    
    # Create temporary directory for parameter files
    local temp_dir=$(mktemp -d)
    trap "rm -rf $temp_dir" EXIT
    
    # Deploy Secrets Manager Policy
    print_info "Deploying Secrets Manager policy..."
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
    print_info "Deploying S3 evaluation policy..."
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
    print_info "Deploying Bedrock evaluation policy..."
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
        print_info "Deploying Lambda invoke policy..."
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
    print_info "Deploying GitHub Actions role..."
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
    print_step "Deployment Summary"
    echo ""
    print_info "Secrets Manager Policy ARN: $SECRETS_MANAGER_POLICY_ARN"
    print_info "S3 Evaluation Policy ARN: $S3_POLICY_ARN"
    print_info "Bedrock Evaluation Policy ARN: $BEDROCK_POLICY_ARN"
    if [ -n "$LAMBDA_POLICY_ARN" ]; then
        print_info "Lambda Invoke Policy ARN: $LAMBDA_POLICY_ARN"
    fi
    echo ""
    print_info "GitHub Actions Role ARN: $ROLE_ARN"
    echo ""
    print_warning "IMPORTANT: Add this role ARN to your GitHub environment/repository secrets:"
    print_warning "  Secret name: AWS_EVALS_ROLE_ARN"
    print_warning "  Secret value: $ROLE_ARN"
    echo ""
    print_info "Add to environment secrets (e.g. staging) or Repository Settings → Secrets and variables → Actions"
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

