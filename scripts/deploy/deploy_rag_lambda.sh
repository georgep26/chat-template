#!/bin/bash

# RAG Lambda Function Deployment Script
# This script builds the Docker image, pushes it to ECR, and deploys the Lambda function.
# Uses the deployer profile for local runs (role created by deploy_deployer_github_action_role.sh).
# In CI (GitHub Actions), the deployer role is assumed via OIDC automatically.
# All base configuration is read from infra/infra.yaml.
#
# Usage Examples:
#   # Deploy to development environment
#   ./scripts/deploy/deploy_rag_lambda.sh dev deploy \
#     --s3_app_config_uri s3://my-bucket/config/dev/app_config.yaml
#
#   # Deploy with auto-confirmation
#   ./scripts/deploy/deploy_rag_lambda.sh dev deploy \
#     --s3_app_config_uri s3://my-bucket/config/dev/app_config.yaml -y
#
#   # Upload local app config to S3 and deploy
#   ./scripts/deploy/deploy_rag_lambda.sh dev deploy \
#     --s3_app_config_uri s3://my-bucket/config/dev/app_config.yaml \
#     --local_app_config_path config/dev/app_config.yaml
#
#   # Build and push Docker image only (no stack deployment)
#   ./scripts/deploy/deploy_rag_lambda.sh dev build
#
#   # Check stack status
#   ./scripts/deploy/deploy_rag_lambda.sh dev status
#
#   # Delete stack
#   ./scripts/deploy/deploy_rag_lambda.sh dev delete
#
# Note: This script requires:
#       1. Docker to be installed and running
#       2. AWS CLI configured with appropriate credentials
#       3. The database stack to be deployed (for DB secret ARN)
#
#       VPC, subnet IDs, memory, timeout, and ECR repo name are read from infra.yaml.
#       The Lambda execution role is auto-detected from the rag_lambda_execution role stack.

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Script Configuration
# =============================================================================

RESOURCE_NAME="rag_lambda"
RESOURCE_DISPLAY_NAME="RAG Lambda Function"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "RAG Lambda Function Deployment Script"
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
    echo "  build     - Build and push Docker image only (no stack deployment)"
    echo ""
    echo "Options:"
    echo "  --s3_app_config_uri <uri>          - S3 URI for app config (default from infra.yaml)"
    echo "  --local_app_config_path <path>     - Local app config to upload to S3 (optional)"
    echo "  --image-tag <tag>                  - Docker image tag (default: latest)"
    echo "  --skip-build                       - Skip Docker build and push (use existing image)"
    echo "  --db-secret-arn <arn>              - DB secret ARN (auto-detected if not provided)"
    echo "  --knowledge-base-id <kb-id>        - Knowledge Base ID (auto-detected if not provided)"
    echo "  --lambda-role-arn <arn>            - Lambda execution role ARN (auto-detected if not provided)"
    echo "  --vpc-id <vpc-id>                 - VPC ID (default: no VPC unless set in infra.yaml rag_lambda config)"
    echo "  --subnet-ids <id1,id2,...>        - Subnet IDs (required when VPC is used)"
    echo "  --security-group-ids <id1,id2,...> - Security group IDs (auto-detected from DB stack)"
    echo "  -y, --yes                          - Skip confirmation prompt"
    echo ""
    echo "Note: Region, VPC, memory, timeout, ECR repo name, and other base settings"
    echo "      are read from infra/infra.yaml"
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
IMAGE_TAG="latest"
SKIP_BUILD=false
APP_CONFIG_S3_URI=""
LOCAL_APP_CONFIG_PATH=""
DB_SECRET_ARN=""
KNOWLEDGE_BASE_ID=""
LAMBDA_ROLE_ARN=""
VPC_ID_OVERRIDE=""
SUBNET_IDS_OVERRIDE=""
SECURITY_GROUP_IDS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        deploy|update|delete|validate|status|build)
            ACTION="$1"
            shift
            ;;
        --s3_app_config_uri|--app-config-s3-uri)
            APP_CONFIG_S3_URI="$2"
            shift 2
            ;;
        --local_app_config_path)
            LOCAL_APP_CONFIG_PATH="$2"
            shift 2
            ;;
        --image-tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --db-secret-arn)
            DB_SECRET_ARN="$2"
            shift 2
            ;;
        --knowledge-base-id)
            KNOWLEDGE_BASE_ID="$2"
            shift 2
            ;;
        --lambda-role-arn)
            LAMBDA_ROLE_ARN="$2"
            shift 2
            ;;
        --vpc-id)
            VPC_ID_OVERRIDE="$2"
            shift 2
            ;;
        --subnet-ids)
            SUBNET_IDS_OVERRIDE="$2"
            shift 2
            ;;
        --security-group-ids)
            SECURITY_GROUP_IDS="$2"
            shift 2
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
# Use deployer profile for local runs. In CI, the deployer role is assumed via OIDC automatically.
if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
    [ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
    [ -n "$AWS_PROFILE" ] && print_info "Using deployer profile: $AWS_PROFILE"
fi

# Resource configuration from infra.yaml
STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME")

# Lambda configuration from infra.yaml
LAMBDA_MEMORY_SIZE=$(get_resource_config "$RESOURCE_NAME" "memory_size")
LAMBDA_TIMEOUT=$(get_resource_config "$RESOURCE_NAME" "timeout")

# S3 app config URI: use infra.yaml default, allow CLI override
if [ -z "$APP_CONFIG_S3_URI" ]; then
    APP_CONFIG_S3_URI=$(get_resource_config "$RESOURCE_NAME" "s3_app_config_uri" "$ENVIRONMENT")
    [ "$APP_CONFIG_S3_URI" = "null" ] && APP_CONFIG_S3_URI=""
fi

# ECR configuration from infra.yaml
ECR_REPO_NAME=$(get_resource_config "rag_lambda_ecr" "repository_name" "$ENVIRONMENT")
ECR_MAX_IMAGE_COUNT=$(get_resource_config "rag_lambda_ecr" "max_image_count")

# VPC configuration: default is no VPC. Only deploy in a VPC if configured under
# resources.rag_lambda.config in infra.yaml, or overridden via --vpc-id / --subnet-ids.
VPC_ID=""
SUBNET_IDS=""
if [ -n "$VPC_ID_OVERRIDE" ]; then
    VPC_ID="$VPC_ID_OVERRIDE"
    SUBNET_IDS="${SUBNET_IDS_OVERRIDE:-}"
else
    local_vpc=$(get_resource_config "$RESOURCE_NAME" "vpc_id" "$ENVIRONMENT")
    [ "$local_vpc" != "null" ] && [ -n "$local_vpc" ] && VPC_ID="$local_vpc"
    local_subnets=$(get_resource_config "$RESOURCE_NAME" "subnet_ids" "$ENVIRONMENT")
    [ "$local_subnets" != "null" ] && [ -n "$local_subnets" ] && SUBNET_IDS="$local_subnets"
fi

# Related stack names (for auto-detection of dependent resources)
DB_STACK_NAME=$(get_resource_stack_name "chat_db" "$ENVIRONMENT")
DB_SECRET_STACK_NAME=$(get_resource_stack_name "chat_db" "$ENVIRONMENT" "secret_stack_name")
KB_STACK_NAME=$(get_resource_stack_name "rag_knowledge_base" "$ENVIRONMENT")
ROLE_STACK_NAME=$(get_role_stack_name "rag_lambda_execution" "$ENVIRONMENT")
ROLE_TEMPLATE_FILE=$(get_role_template "rag_lambda_execution")

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

# Require S3 app config URI for deploy/update
if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    if [ -z "$APP_CONFIG_S3_URI" ]; then
        print_error "S3 app config URI is required for $ACTION."
        print_error "Set resources.rag_lambda.config.s3_app_config_uri in infra/infra.yaml or pass --s3_app_config_uri"
        exit 1
    fi
    print_info "App config S3 URI: $APP_CONFIG_S3_URI"
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
# Template Validation
# =============================================================================

do_validate_template() {
    print_step "Validating CloudFormation template..."

    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi

    # Test AWS credentials before validation
    if [ -n "$AWS_PROFILE" ]; then
        print_info "Testing AWS credentials with profile: $AWS_PROFILE"
        local caller_identity
        if caller_identity=$(aws_cmd sts get-caller-identity 2>&1); then
            local assumed_arn
            assumed_arn=$(echo "$caller_identity" | grep -o '"Arn"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
            print_info "Authenticated as: $assumed_arn"
        else
            print_error "Failed to get AWS credentials using profile '$AWS_PROFILE'"
            echo "$caller_identity" | sed 's/^/  /'
            exit 1
        fi
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
            echo ""

            local status_reason=$(aws_cmd cloudformation describe-stacks \
                --stack-name "$STACK_NAME" \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)

            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Status Reason: $status_reason"
                echo ""
            fi

            print_error "Recent stack events with errors:"
            echo ""
            aws_cmd cloudformation describe-stack-events \
                --stack-name "$STACK_NAME" \
                --max-items 20 \
                --query 'StackEvents[?contains(ResourceStatus, `FAILED`) || contains(ResourceStatus, `ROLLBACK`)].{Time:Timestamp,Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}' \
                --output table 2>/dev/null || true

            echo ""
            print_error "For more details, check the AWS Console or run:"
            print_error "aws cloudformation describe-stack-events --stack-name $STACK_NAME --region $AWS_REGION"
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
# Dependency Auto-Detection
# =============================================================================

# Get DB secret ARN from the secret stack or SecretsManager
get_db_secret_arn() {
    print_info "Auto-detecting DB secret ARN..." >&2

    # Try the secret stack first (from infra.yaml: chat_db.secret_stack_name)
    if [ -n "$DB_SECRET_STACK_NAME" ] && aws_cmd cloudformation describe-stacks --stack-name "$DB_SECRET_STACK_NAME" >/dev/null 2>&1; then
        local secret_arn=$(aws_cmd cloudformation describe-stacks \
            --stack-name "$DB_SECRET_STACK_NAME" \
            --query 'Stacks[0].Outputs[?OutputKey==`SecretArn`].OutputValue' \
            --output text 2>/dev/null)
        if [ -n "$secret_arn" ] && [ "$secret_arn" != "None" ]; then
            print_info "Found DB Secret ARN from secret stack: $DB_SECRET_STACK_NAME" >&2
            echo "$secret_arn"
            return 0
        fi
    fi

    # Fallback: search SecretsManager directly with common naming patterns
    local secret_names=(
        "${PROJECT_NAME}-db-connection-${ENVIRONMENT}"
        "${PROJECT_NAME}-chat-template-db-connection-${ENVIRONMENT}"
    )
    for name in "${secret_names[@]}"; do
        if aws_cmd secretsmanager describe-secret --secret-id "$name" >/dev/null 2>&1; then
            local secret_arn=$(aws_cmd secretsmanager describe-secret --secret-id "$name" \
                --query 'ARN' --output text 2>/dev/null)
            if [ -n "$secret_arn" ] && [ "$secret_arn" != "None" ]; then
                print_info "Found DB Secret ARN from SecretsManager: $name" >&2
                echo "$secret_arn"
                return 0
            fi
        fi
    done

    print_warning "Could not auto-detect DB secret ARN" >&2
    return 1
}

# Get Knowledge Base ID from the KB stack
get_kb_id() {
    print_info "Auto-detecting Knowledge Base ID from: $KB_STACK_NAME" >&2

    if ! aws_cmd cloudformation describe-stacks --stack-name "$KB_STACK_NAME" >/dev/null 2>&1; then
        print_warning "KB stack $KB_STACK_NAME does not exist" >&2
        return 1
    fi

    local kb_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$KB_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' \
        --output text 2>/dev/null)

    if [ -z "$kb_id" ] || [ "$kb_id" == "None" ]; then
        print_warning "Could not retrieve Knowledge Base ID from stack $KB_STACK_NAME" >&2
        return 1
    fi

    echo "$kb_id"
}

# Get Lambda execution role ARN from the role stack
get_lambda_role_arn() {
    print_info "Auto-detecting Lambda execution role ARN from: $ROLE_STACK_NAME" >&2

    if ! aws_cmd cloudformation describe-stacks --stack-name "$ROLE_STACK_NAME" >/dev/null 2>&1; then
        print_warning "Lambda role stack $ROLE_STACK_NAME does not exist" >&2
        return 1
    fi

    local role_arn=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$ROLE_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`RoleArn`].OutputValue' \
        --output text 2>/dev/null)

    if [ -z "$role_arn" ] || [ "$role_arn" == "None" ]; then
        print_warning "Could not retrieve role ARN from stack $ROLE_STACK_NAME" >&2
        return 1
    fi

    echo "$role_arn"
}

# Auto-detect security group IDs from the DB stack
get_db_security_group_id() {
    if ! aws_cmd cloudformation describe-stacks --stack-name "$DB_STACK_NAME" >/dev/null 2>&1; then
        return 1
    fi

    local sg_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$DB_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`SecurityGroupId`].OutputValue' \
        --output text 2>/dev/null)

    if [ -n "$sg_id" ] && [ "$sg_id" != "None" ]; then
        echo "$sg_id"
        return 0
    fi

    return 1
}

# =============================================================================
# Lambda Execution Role Deployment
# =============================================================================

deploy_lambda_role_stack() {
    print_info "Deploying Lambda execution role stack: $ROLE_STACK_NAME"

    if [ ! -f "$ROLE_TEMPLATE_FILE" ]; then
        print_error "Role template file not found: $ROLE_TEMPLATE_FILE"
        return 1
    fi

    local role_param_file=$(mktemp)
    trap "rm -f $role_param_file" EXIT

    cat > "$role_param_file" << EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"}
]
EOF

    # Check if role stack already exists
    if aws_cmd cloudformation describe-stacks --stack-name "$ROLE_STACK_NAME" >/dev/null 2>&1; then
        print_info "Lambda role stack $ROLE_STACK_NAME already exists. Updating..."

        local update_output
        update_output=$(aws_cmd cloudformation update-stack \
            --stack-name "$ROLE_STACK_NAME" \
            --template-body "file://$ROLE_TEMPLATE_FILE" \
            --parameters "file://$role_param_file" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1) || {
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_info "Lambda role stack is already up to date"
                return 0
            else
                print_error "Lambda role stack update failed: $update_output"
                return 1
            fi
        }

        print_info "Waiting for role stack update to complete..."
        if aws_cmd cloudformation wait stack-update-complete --stack-name "$ROLE_STACK_NAME" 2>/dev/null; then
            print_complete "Lambda role stack updated successfully"
            return 0
        else
            local stack_status=$(aws_cmd cloudformation describe-stacks \
                --stack-name "$ROLE_STACK_NAME" \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            if [ "$stack_status" == "UPDATE_COMPLETE" ]; then
                print_complete "Lambda role stack updated successfully"
                return 0
            else
                print_error "Lambda role stack update failed. Status: $stack_status"
                return 1
            fi
        fi
    fi

    print_info "Creating Lambda execution role stack..."
    if aws_cmd cloudformation create-stack \
        --stack-name "$ROLE_STACK_NAME" \
        --template-body "file://$ROLE_TEMPLATE_FILE" \
        --parameters "file://$role_param_file" \
        --capabilities CAPABILITY_NAMED_IAM >/dev/null 2>&1; then
        print_info "Waiting for role stack to be ready..."

        if aws_cmd cloudformation wait stack-create-complete --stack-name "$ROLE_STACK_NAME" 2>/dev/null; then
            print_complete "Lambda role stack created successfully"
            return 0
        else
            local stack_status=$(aws_cmd cloudformation describe-stacks \
                --stack-name "$ROLE_STACK_NAME" \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)

            if [ "$stack_status" == "CREATE_COMPLETE" ]; then
                print_complete "Lambda role stack created successfully"
                return 0
            else
                print_error "Lambda role stack creation failed. Status: $stack_status"
                return 1
            fi
        fi
    else
        print_error "Failed to create Lambda role stack"
        return 1
    fi
}

# =============================================================================
# ECR Repository Management
# =============================================================================

# Apply ECR lifecycle policy (keep last N images)
apply_ecr_lifecycle_policy() {
    local repo_name=$1
    local keep_count=${2:-$ECR_MAX_IMAGE_COUNT}
    local policy_file
    policy_file=$(mktemp)
    trap "rm -f $policy_file" RETURN
    cat > "$policy_file" << EOF
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Keep only ${keep_count} most recent images",
      "selection": {
        "tagStatus": "any",
        "countType": "imageCountMoreThan",
        "countNumber": ${keep_count}
      },
      "action": {
        "type": "expire"
      }
    }
  ]
}
EOF
    if aws_cmd ecr put-lifecycle-policy \
        --repository-name "$repo_name" \
        --lifecycle-policy-text "file://$policy_file" >/dev/null 2>&1; then
        print_info "ECR lifecycle policy applied: keep ${keep_count} most recent images" >&2
    else
        print_warning "Failed to apply ECR lifecycle policy (repo may still work)" >&2
    fi
}

# Create ECR repository if it doesn't exist
ensure_ecr_repo() {
    local repo_name=$1
    print_info "Checking ECR repository: $repo_name" >&2

    if aws_cmd ecr describe-repositories --repository-names "$repo_name" >/dev/null 2>&1; then
        print_info "ECR repository $repo_name already exists" >&2
    else
        print_info "Creating ECR repository: $repo_name" >&2
        aws_cmd ecr create-repository \
            --repository-name "$repo_name" \
            --image-scanning-configuration scanOnPush=true \
            --encryption-configuration encryptionType=AES256 >/dev/null 2>&1
        print_info "ECR repository created successfully" >&2
    fi

    apply_ecr_lifecycle_policy "$repo_name" "$ECR_MAX_IMAGE_COUNT"
}

# =============================================================================
# Docker Build and Push
# =============================================================================

build_and_push_image() {
    if [ "$SKIP_BUILD" = true ]; then
        print_info "Skipping Docker build (--skip-build flag set)" >&2
        return 0
    fi

    local account_id=$(aws_cmd sts get-caller-identity --query 'Account' --output text 2>/dev/null)
    if [ -z "$account_id" ]; then
        print_error "Failed to get AWS account ID" >&2
        exit 1
    fi

    local ecr_uri="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"
    local image_uri="${ecr_uri}:${IMAGE_TAG}"

    print_info "Building Docker image: rag-lambda" >&2
    print_info "Dockerfile: src/rag_lambda/Dockerfile" >&2
    print_info "Platform: linux/amd64 (x86_64)" >&2

    # Build the image for x86_64 architecture (Lambda default)
    docker build --platform linux/amd64 -f src/rag_lambda/Dockerfile -t rag-lambda:latest .

    if [ $? -ne 0 ]; then
        print_error "Docker build failed" >&2
        exit 1
    fi

    print_info "Docker image built successfully" >&2

    # Ensure ECR repository exists
    ensure_ecr_repo "$ECR_REPO_NAME"

    # Login to ECR
    print_info "Logging in to ECR..." >&2
    aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "$ecr_uri" >/dev/null 2>&1

    # Tag and push
    print_info "Tagging image: $image_uri" >&2
    docker tag rag-lambda:latest "$image_uri"

    print_info "Pushing image to ECR: $image_uri" >&2
    docker push "$image_uri" 1>&2

    if [ $? -ne 0 ]; then
        print_error "Docker push failed" >&2
        exit 1
    fi

    print_info "Image pushed successfully: $image_uri" >&2
    echo "$image_uri"
}

# =============================================================================
# Deploy Stack
# =============================================================================

deploy_stack() {
    # Show deploy summary
    print_resource_summary "$RESOURCE_NAME" "$ENVIRONMENT" "$ACTION"
    print_info "Memory Size: ${LAMBDA_MEMORY_SIZE}MB"
    print_info "Timeout: ${LAMBDA_TIMEOUT}s"
    print_info "ECR Repo: $ECR_REPO_NAME"
    print_info "Image Tag: $IMAGE_TAG"
    [ -n "$VPC_ID" ] && print_info "VPC: $VPC_ID"

    # Confirm deployment
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment || exit 0
    fi

    print_step "Deploying CloudFormation stack: $STACK_NAME"

    # Get or build image URI
    local image_uri=""
    if [ "$SKIP_BUILD" = true ]; then
        if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
            image_uri=$(aws_cmd cloudformation describe-stacks \
                --stack-name "$STACK_NAME" \
                --query 'Stacks[0].Parameters[?ParameterKey==`LambdaImageUri`].ParameterValue' \
                --output text 2>/dev/null)
        fi
        if [ -z "$image_uri" ] || [ "$image_uri" == "None" ]; then
            print_error "Cannot skip build: no existing image URI found. Remove --skip-build flag."
            exit 1
        fi
        print_info "Using existing image URI: $image_uri"
    else
        image_uri=$(build_and_push_image)
    fi

    # Upload local app config to S3 if provided
    if [ -n "$LOCAL_APP_CONFIG_PATH" ]; then
        if [ ! -f "$LOCAL_APP_CONFIG_PATH" ]; then
            print_error "Local app config file not found: $LOCAL_APP_CONFIG_PATH"
            exit 1
        fi
        if [[ "$APP_CONFIG_S3_URI" != s3://* ]]; then
            print_error "Invalid --s3_app_config_uri: $APP_CONFIG_S3_URI (expected s3://bucket/key)"
            exit 1
        fi
        print_info "Uploading local app config to S3: $LOCAL_APP_CONFIG_PATH -> $APP_CONFIG_S3_URI"
        aws_cmd s3 cp "$LOCAL_APP_CONFIG_PATH" "$APP_CONFIG_S3_URI" >/dev/null
        print_complete "Uploaded app config to S3"
    fi

    # -------------------------------------------------------------------------
    # VPC configuration
    # Default: no VPC. Only used when vpc_id is set under resources.rag_lambda.config
    # in infra.yaml, or overridden via --vpc-id / --subnet-ids.
    # -------------------------------------------------------------------------
    if [ -z "$VPC_ID" ]; then
        print_info "No VPC configured. Deploying Lambda without VPC (default)."
        SUBNET_IDS=""
        SECURITY_GROUP_IDS=""
    else
        print_info "VPC configured: $VPC_ID"
        if [ -z "$SUBNET_IDS" ]; then
            print_error "VPC ID is set but subnet_ids not found."
            print_error "Add subnet_ids to resources.rag_lambda.config in infra/infra.yaml or pass --subnet-ids"
            exit 1
        fi
        print_info "Subnet IDs: $SUBNET_IDS"

        # Auto-detect security groups from DB stack if not provided
        if [ -z "$SECURITY_GROUP_IDS" ]; then
            print_info "Auto-detecting security group IDs from DB stack..."
            local sg_id=$(get_db_security_group_id)
            if [ -n "$sg_id" ]; then
                SECURITY_GROUP_IDS="$sg_id"
                print_info "Auto-detected Security Group ID: $SECURITY_GROUP_IDS"
            fi
        fi

        if [ -z "$SECURITY_GROUP_IDS" ]; then
            print_error "Security Group IDs required when VPC is configured."
            print_error "Provide --security-group-ids or ensure DB stack has SecurityGroupId output."
            exit 1
        fi

        print_info "Deploying Lambda with VPC configuration."
    fi

    # -------------------------------------------------------------------------
    # Auto-detect dependent resource values
    # -------------------------------------------------------------------------

    # DB Secret ARN
    if [ -z "$DB_SECRET_ARN" ]; then
        local secret_arn=$(get_db_secret_arn)
        if [ $? -eq 0 ] && [ -n "$secret_arn" ]; then
            DB_SECRET_ARN="$secret_arn"
            print_info "DB Secret ARN: $DB_SECRET_ARN"
        fi
    fi
    if [ -z "$DB_SECRET_ARN" ]; then
        print_warning "DB Secret ARN not provided. OK for aurora_data_api backend (secret ARN should be in config file)."
    fi

    # Knowledge Base ID
    if [ -z "$KNOWLEDGE_BASE_ID" ]; then
        local kb_id=$(get_kb_id)
        if [ $? -eq 0 ] && [ -n "$kb_id" ]; then
            KNOWLEDGE_BASE_ID="$kb_id"
            print_info "Knowledge Base ID: $KNOWLEDGE_BASE_ID"
        fi
    fi

    # Lambda execution role: always ensure the role stack is up to date
    print_info "Ensuring Lambda execution role stack is up to date..."
    if ! deploy_lambda_role_stack; then
        print_error "Failed to deploy/update Lambda execution role. Cannot proceed."
        exit 1
    fi

    if [ -z "$LAMBDA_ROLE_ARN" ]; then
        local role_arn=$(get_lambda_role_arn)
        if [ $? -eq 0 ] && [ -n "$role_arn" ]; then
            LAMBDA_ROLE_ARN="$role_arn"
            print_info "Lambda Role ARN: $LAMBDA_ROLE_ARN"
        else
            print_error "Failed to retrieve Lambda execution role ARN after deployment."
            exit 1
        fi
    fi

    # Extract S3 bucket name from URI
    local config_bucket=""
    if [[ "$APP_CONFIG_S3_URI" =~ ^s3://([^/]+) ]]; then
        config_bucket="${BASH_REMATCH[1]}"
        print_info "Config S3 bucket: $config_bucket"
    else
        print_error "Invalid --s3_app_config_uri: $APP_CONFIG_S3_URI (expected s3://bucket/key)"
        exit 1
    fi

    # -------------------------------------------------------------------------
    # Create parameters file
    # -------------------------------------------------------------------------
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT

    cat > "$param_file" << EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "AWSRegion", "ParameterValue": "$AWS_REGION"},
  {"ParameterKey": "LambdaImageUri", "ParameterValue": "$image_uri"},
  {"ParameterKey": "LambdaMemorySize", "ParameterValue": "$LAMBDA_MEMORY_SIZE"},
  {"ParameterKey": "LambdaTimeout", "ParameterValue": "$LAMBDA_TIMEOUT"},
  {"ParameterKey": "VpcId", "ParameterValue": "${VPC_ID:-}"},
  {"ParameterKey": "SubnetIds", "ParameterValue": "${SUBNET_IDS:-}"},
  {"ParameterKey": "SecurityGroupIds", "ParameterValue": "${SECURITY_GROUP_IDS:-}"},
  {"ParameterKey": "DBSecretArn", "ParameterValue": "${DB_SECRET_ARN:-}"},
  {"ParameterKey": "KnowledgeBaseId", "ParameterValue": "${KNOWLEDGE_BASE_ID:-}"},
  {"ParameterKey": "LambdaExecutionRoleArn", "ParameterValue": "$LAMBDA_ROLE_ARN"},
  {"ParameterKey": "AppConfigPath", "ParameterValue": "$APP_CONFIG_S3_URI"},
  {"ParameterKey": "ConfigS3Bucket", "ParameterValue": "$config_bucket"}
]
EOF

    # Validate parameters file is valid JSON
    if ! python3 -m json.tool "$param_file" >/dev/null 2>&1; then
        print_error "Generated parameters file is not valid JSON"
        exit 1
    fi

    # -------------------------------------------------------------------------
    # Create or update stack
    # -------------------------------------------------------------------------
    local stack_exists=false
    local no_updates=false

    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        stack_exists=true
        print_info "Stack $STACK_NAME already exists. Updating..."

        local update_output
        update_output=$(aws_cmd cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_IAM 2>&1) || {
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
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_IAM || {
            print_error "Stack creation failed"
            exit 1
        }
    fi

    # -------------------------------------------------------------------------
    # Wait for stack operation to complete
    # -------------------------------------------------------------------------
    if [ "$no_updates" = false ]; then
        print_info "Waiting for stack operation to complete..."

        local wait_cmd="stack-create-complete"
        [ "$stack_exists" = true ] && wait_cmd="stack-update-complete"

        # Background progress monitoring
        local progress_pid=""
        (
            local wait_start_time=$(date +%s)
            while true; do
                sleep 30
                local current_time=$(date +%s)
                local elapsed=$((current_time - wait_start_time))
                local stack_status=$(aws_cmd cloudformation describe-stacks \
                    --stack-name "$STACK_NAME" \
                    --query 'Stacks[0].StackStatus' \
                    --output text 2>/dev/null)
                if [ -n "$stack_status" ] && [ "$stack_status" != "None" ]; then
                    print_info "Stack status: $stack_status (elapsed: ${elapsed}s)" >&2
                fi
            done
        ) &
        progress_pid=$!

        cleanup_progress() {
            if [ -n "$progress_pid" ] && kill -0 $progress_pid 2>/dev/null; then
                kill -TERM $progress_pid 2>/dev/null
                sleep 1
                kill -0 $progress_pid 2>/dev/null && kill -9 $progress_pid 2>/dev/null
                wait $progress_pid 2>/dev/null || true
            fi
        }
        trap cleanup_progress EXIT INT TERM

        if aws_cmd cloudformation wait "$wait_cmd" --stack-name "$STACK_NAME" 2>/dev/null; then
            cleanup_progress
            trap - EXIT INT TERM
            print_complete "Stack operation completed successfully"
        else
            cleanup_progress
            trap - EXIT INT TERM

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

    # -------------------------------------------------------------------------
    # Post-deploy: update Lambda function code and display outputs
    # -------------------------------------------------------------------------
    local lambda_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`LambdaFunctionName`].OutputValue' \
        --output text 2>/dev/null)

    if [ -n "$lambda_name" ] && [ "$lambda_name" != "None" ]; then
        print_info "Lambda Function Name: $lambda_name"

        # Update Lambda to use the latest image (ensures function picks up new image even if tag unchanged)
        if [ "$SKIP_BUILD" = false ]; then
            print_info "Updating Lambda function to use latest image: $image_uri"
            if aws_cmd lambda update-function-code \
                --function-name "$lambda_name" \
                --image-uri "$image_uri" >/dev/null 2>&1; then
                print_info "Lambda function code updated. Waiting for completion..."

                local max_wait=60
                local update_wait_start=$(date +%s)
                while true; do
                    local update_status=$(aws_cmd lambda get-function \
                        --function-name "$lambda_name" \
                        --query 'Configuration.LastUpdateStatus' \
                        --output text 2>/dev/null)

                    if [ "$update_status" == "Successful" ]; then
                        print_complete "Lambda function update completed"
                        break
                    elif [ "$update_status" == "Failed" ]; then
                        print_warning "Lambda function update failed. Check Lambda console."
                        break
                    fi

                    local elapsed=$(( $(date +%s) - update_wait_start ))
                    if [ $elapsed -ge $max_wait ]; then
                        print_warning "Lambda update taking longer than expected. Status: $update_status"
                        break
                    fi
                    sleep 5
                done
            else
                print_warning "Failed to update Lambda function code directly."
                print_warning "Try: aws lambda update-function-code --function-name $lambda_name --image-uri $image_uri --region $AWS_REGION"
            fi
        fi
    fi

    print_complete "$RESOURCE_DISPLAY_NAME deployment finished"
}

# =============================================================================
# Delete Stack
# =============================================================================

delete_stack() {
    print_warning "This will delete:"
    print_warning "  - RAG Lambda function"
    print_warning "  - Associated CloudFormation resources"
    echo ""

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
        do_validate_template
        ;;
    status)
        show_status
        ;;
    build)
        build_and_push_image
        print_complete "Docker image build and push completed"
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
