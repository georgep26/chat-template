#!/bin/bash

# RAG Lambda Function Deployment Script
# This script builds the Docker image, pushes it to ECR, and deploys the Lambda function
#
# Usage Examples:
#   # Deploy to development environment (default region: us-east-1)
#   # (Requires an S3-hosted app config)
#   ./scripts/deploy/deploy_rag_lambda.sh dev deploy \
#     --s3_app_config_uri s3://my-bucket/config/app_config.yml
#
#   # Deploy to staging with custom memory and timeout
#   ./scripts/deploy/deploy_rag_lambda.sh staging deploy \
#     --s3_app_config_uri s3://my-bucket/config/app_config.yml \
#     --memory-size 2048 --timeout 600
#
#   # Deploy to production with custom ECR repository
#   ./scripts/deploy/deploy_rag_lambda.sh prod deploy \
#     --s3_app_config_uri s3://my-bucket/config/app_config.yml \
#     --ecr-repo my-rag-lambda
#
#   # Deploy without VPC (omit VPC parameters for aurora_data_api backend)
#   ./scripts/deploy/deploy_rag_lambda.sh dev deploy \
#     --s3_app_config_uri s3://my-bucket/config/app_config.yml
#
#   # Optional: overwrite the S3 config with a local file (useful for CI/CD or local dev)
#   ./scripts/deploy/deploy_rag_lambda.sh dev deploy \
#     --s3_app_config_uri s3://my-bucket/config/app_config.yml \
#     --local_app_config_path config/app_config.yml
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_rag_lambda.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_rag_lambda.sh dev status
#
#   # Update existing stack (rebuilds and pushes image)
#   ./scripts/deploy/deploy_rag_lambda.sh dev update \
#     --s3_app_config_uri s3://my-bucket/config/app_config.yml
#
#   # Delete stack (with confirmation prompt)
#   ./scripts/deploy/deploy_rag_lambda.sh dev delete
#
# Note: This script requires:
#       1. Docker to be installed and running
#       2. AWS CLI configured with appropriate credentials
#       3. The database stack to be deployed (for DB secret ARN)
#       4. VPC, subnets, and security groups to be available (optional, only for postgres backend)
#
#       The Lambda execution role will be automatically deployed if it doesn't exist.

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
    echo -e "${BLUE}[RAG LAMBDA]${NC} $1"
}

# Function to show usage
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
    echo "  --ecr-repo <name>                 - ECR repository name (default: rag-lambda-<env>)"
    echo "  --image-tag <tag>                 - Docker image tag (default: latest)"
    echo "  --memory-size <mb>                - Lambda memory size in MB (default: 1024)"
    echo "  --timeout <seconds>               - Lambda timeout in seconds (default: 300)"
    echo "  --vpc-id <vpc-id>                 - VPC ID (auto-detected from VPC stack if not provided)"
    echo "  --subnet-ids <id1,id2,...>        - Subnet IDs (auto-detected from VPC stack if not provided)"
    echo "  --security-group-ids <id1,id2,...> - Security group IDs (auto-detected if not provided)"
    echo "  --db-secret-arn <arn>              - DB secret ARN (auto-detected from DB stack if not provided)"
    echo "  --knowledge-base-id <kb-id>        - Knowledge Base ID (auto-detected from KB stack if not provided)"
    echo "  --lambda-role-arn <arn>            - Lambda execution role ARN (auto-detected from role stack if not provided)"
    echo "  --s3_app_config_uri <uri>          - S3 URI for app config file (e.g., s3://bucket/key) (required for deploy/update)"
    echo "  --local_app_config_path <path>     - Local app config file to upload to --s3_app_config_uri (optional)"
    echo "  --app-config-s3-uri <uri>          - (deprecated) Alias for --s3_app_config_uri"
    echo "  --skip-build                       - Skip Docker build and push (use existing image)"
    echo "  --region <region>                  - AWS region (default: us-east-1)"
    echo ""
    echo "Note: If VPC ID is not provided, Lambda will be deployed without VPC (suitable for aurora_data_api backend)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy --s3_app_config_uri s3://my-bucket/config/app_config.yml  # Deploy without VPC (aurora_data_api backend)"
    echo "  $0 dev deploy --s3_app_config_uri s3://my-bucket/config/app_config.yml --vpc-id vpc-123 --subnet-ids subnet-1,subnet-2 --security-group-ids sg-123  # Deploy with VPC (postgres backend)"
    echo "  $0 dev deploy --s3_app_config_uri s3://my-bucket/config/app_config.yml --local_app_config_path config/app_config.yml  # Upload local config to S3"
    echo "  $0 staging deploy --memory-size 2048 --timeout 600"
    echo "  $0 prod deploy --ecr-repo my-rag-lambda --image-tag v1.0.0"
    echo "  $0 dev build --image-tag test"
    echo ""
    echo "Note: The script will automatically detect VPC, DB, and KB stack outputs if available."
    echo "      If VPC parameters are not provided, Lambda will be deployed without VPC."
    echo "      The Lambda execution role will be automatically deployed if it doesn't exist."
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="chat-template-rag-lambda-${ENVIRONMENT}"
TEMPLATE_FILE="infra/resources/lambda_template.yaml"
ROLE_STACK_NAME="chat-template-lambda-execution-role-${ENVIRONMENT}"
ROLE_TEMPLATE_FILE="infra/roles/lambda_execution_role.yaml"
DB_STACK_NAME="chat-template-light-db-${ENVIRONMENT}"
KB_STACK_NAME="chat-template-knowledge-base-${ENVIRONMENT}"
VPC_STACK_NAME="chat-template-vpc-${ENVIRONMENT}"
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"  # Default AWS region
ECR_REPO_NAME="rag-lambda-${ENVIRONMENT}"
IMAGE_TAG="latest"
LAMBDA_MEMORY_SIZE=1024
LAMBDA_TIMEOUT=300
SKIP_BUILD=false

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

# Parse additional arguments
VPC_ID=""
SUBNET_IDS=""
SECURITY_GROUP_IDS=""
DB_SECRET_ARN=""
KNOWLEDGE_BASE_ID=""
LAMBDA_ROLE_ARN=""
APP_CONFIG_S3_URI=""
LOCAL_APP_CONFIG_PATH=""

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --ecr-repo)
            ECR_REPO_NAME="$2"
            shift 2
            ;;
        --image-tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --memory-size)
            LAMBDA_MEMORY_SIZE="$2"
            shift 2
            ;;
        --timeout)
            LAMBDA_TIMEOUT="$2"
            shift 2
            ;;
        --vpc-id)
            VPC_ID="$2"
            shift 2
            ;;
        --subnet-ids)
            SUBNET_IDS="$2"
            shift 2
            ;;
        --security-group-ids)
            SECURITY_GROUP_IDS="$2"
            shift 2
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
        --app-config-s3-uri)
            APP_CONFIG_S3_URI="$2"
            shift 2
            ;;
        --s3_app_config_uri)
            APP_CONFIG_S3_URI="$2"
            shift 2
            ;;
        --local_app_config_path)
            LOCAL_APP_CONFIG_PATH="$2"
            shift 2
            ;;
        --skip-build)
            SKIP_BUILD=true
            shift
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        *)
            # If it's not a recognized option, it might be the action
            if [[ "$ACTION" == "deploy" && "$1" != "deploy" && "$1" != "update" && "$1" != "delete" && "$1" != "validate" && "$1" != "status" && "$1" != "build" ]]; then
                ACTION="$1"
            fi
            shift
            ;;
    esac
done

print_header "Starting RAG Lambda deployment for $ENVIRONMENT environment"

# Require an S3 app config URI for deploy/update so Lambda always uses S3 for APP_CONFIG_PATH
if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    if [ -z "$APP_CONFIG_S3_URI" ]; then
        print_error "--s3_app_config_uri is required for $ACTION"
        show_usage
        exit 1
    fi
fi

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

# Check if template file exists
if [ ! -f "$TEMPLATE_FILE" ]; then
    print_error "Template file not found: $TEMPLATE_FILE"
    exit 1
fi

# Function to get AWS account ID
get_aws_account_id() {
    aws sts get-caller-identity --region "$AWS_REGION" --query 'Account' --output text 2>/dev/null
}

# Function to get VPC stack outputs
get_vpc_stack_outputs() {
    print_status "Retrieving VPC stack outputs from: $VPC_STACK_NAME" >&2
    
    if ! aws cloudformation describe-stacks --stack-name "$VPC_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_warning "VPC stack $VPC_STACK_NAME does not exist in region $AWS_REGION" >&2
        return 1
    fi
    
    local vpc_id=$(aws cloudformation describe-stacks \
        --stack-name "$VPC_STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' \
        --output text 2>/dev/null)
    
    local subnet_ids=$(aws cloudformation describe-stacks \
        --stack-name "$VPC_STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`PrivateSubnetIds`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -z "$vpc_id" ] || [ "$vpc_id" == "None" ]; then
        print_warning "Could not retrieve VPC ID from stack $VPC_STACK_NAME" >&2
        return 1
    fi
    
    echo "$vpc_id|$subnet_ids"
}

# Function to get DB stack outputs
get_db_stack_outputs() {
    print_status "Retrieving DB stack outputs from: $DB_STACK_NAME" >&2
    
    if ! aws cloudformation describe-stacks --stack-name "$DB_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_warning "DB stack $DB_STACK_NAME does not exist in region $AWS_REGION" >&2
        return 1
    fi
    
    local secret_stack_name="chat-template-db-secret-${ENVIRONMENT}"
    local secret_arn=""
    
    if aws cloudformation describe-stacks --stack-name "$secret_stack_name" --region "$AWS_REGION" >/dev/null 2>&1; then
        secret_arn=$(aws cloudformation describe-stacks \
            --stack-name "$secret_stack_name" \
            --region "$AWS_REGION" \
            --query 'Stacks[0].Outputs[?OutputKey==`SecretArn`].OutputValue' \
            --output text 2>/dev/null)
    fi

    if [ -z "$secret_arn" ] || [ "$secret_arn" == "None" ]; then
        local secret_name1="${PROJECT_NAME}-db-connection-${ENVIRONMENT}"
        local secret_name2="${PROJECT_NAME}-chat-template-db-connection-${ENVIRONMENT}"
        local secret_name3="python-template-db-connection-${ENVIRONMENT}"
        local secret_name4="python-template-chat-template-db-connection-${ENVIRONMENT}"
        for name in "$secret_name1" "$secret_name2" "$secret_name3" "$secret_name4"; do
            if aws secretsmanager describe-secret --secret-id "$name" --region "$AWS_REGION" >/dev/null 2>&1; then
                secret_arn=$(aws secretsmanager describe-secret --secret-id "$name" --region "$AWS_REGION" \
                    --query 'ARN' --output text 2>/dev/null)
                break
            fi
        done
    fi
    
    if [ -z "$secret_arn" ] || [ "$secret_arn" == "None" ]; then
        print_warning "Could not retrieve secret ARN from stack $DB_STACK_NAME" >&2
        return 1
    fi
    
    echo "$secret_arn"
}

# Function to get KB stack outputs
get_kb_stack_outputs() {
    print_status "Retrieving KB stack outputs from: $KB_STACK_NAME" >&2
    
    if ! aws cloudformation describe-stacks --stack-name "$KB_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_warning "KB stack $KB_STACK_NAME does not exist in region $AWS_REGION" >&2
        return 1
    fi
    
    local kb_id=$(aws cloudformation describe-stacks \
        --stack-name "$KB_STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -z "$kb_id" ] || [ "$kb_id" == "None" ]; then
        print_warning "Could not retrieve Knowledge Base ID from stack $KB_STACK_NAME" >&2
        return 1
    fi
    
    echo "$kb_id"
}

# Function to deploy Lambda execution role stack
deploy_lambda_role_stack() {
    print_status "Deploying Lambda execution role stack: $ROLE_STACK_NAME"
    
    # Check if template file exists
    if [ ! -f "$ROLE_TEMPLATE_FILE" ]; then
        print_error "Role template file not found: $ROLE_TEMPLATE_FILE"
        return 1
    fi
    
    # Check if role stack already exists
    if aws cloudformation describe-stacks --stack-name "$ROLE_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_status "Lambda role stack $ROLE_STACK_NAME already exists"
        return 0
    fi
    
    # Create a temporary parameters file for the role stack
    local role_param_file=$(mktemp)
    trap "rm -f $role_param_file" EXIT
    
    # Build parameters JSON file for role stack
    {
        echo "["
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        echo ","
        printf '  {\n    "ParameterKey": "AWSRegion",\n    "ParameterValue": "%s"\n  }' "$AWS_REGION"
        echo ""
        echo "]"
    } > "$role_param_file"
    
    # Create the role stack
    print_status "Creating Lambda execution role stack..."
    if aws cloudformation create-stack \
        --stack-name "$ROLE_STACK_NAME" \
        --template-body file://$ROLE_TEMPLATE_FILE \
        --parameters file://$role_param_file \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$AWS_REGION" >/dev/null 2>&1; then
        print_status "Lambda role stack creation initiated"
        print_status "Waiting for role stack to be ready..."
        
        # Wait for stack to be created
        aws cloudformation wait stack-create-complete --stack-name "$ROLE_STACK_NAME" --region "$AWS_REGION" 2>/dev/null
        local wait_result=$?
        
        if [ $wait_result -eq 0 ]; then
            print_status "Lambda role stack created successfully"
            return 0
        else
            # Check if stack is actually in a good state
            local stack_status=$(aws cloudformation describe-stacks \
                --stack-name "$ROLE_STACK_NAME" \
                --region "$AWS_REGION" \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            
            if [ "$stack_status" == "CREATE_COMPLETE" ]; then
                print_status "Lambda role stack created successfully"
                return 0
            else
                print_error "Lambda role stack creation failed or timed out. Status: $stack_status"
                return 1
            fi
        fi
    else
        print_error "Failed to create Lambda role stack"
        return 1
    fi
}

# Function to get Lambda role ARN
get_lambda_role_arn() {
    print_status "Retrieving Lambda execution role ARN from: $ROLE_STACK_NAME" >&2
    
    if ! aws cloudformation describe-stacks --stack-name "$ROLE_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_warning "Lambda role stack $ROLE_STACK_NAME does not exist in region $AWS_REGION" >&2
        return 1
    fi
    
    local role_arn=$(aws cloudformation describe-stacks \
        --stack-name "$ROLE_STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`RoleArn`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -z "$role_arn" ] || [ "$role_arn" == "None" ]; then
        print_warning "Could not retrieve role ARN from stack $ROLE_STACK_NAME" >&2
        return 1
    fi
    
    echo "$role_arn"
}

# Function to create ECR repository if it doesn't exist
ensure_ecr_repo() {
    local repo_name=$1
    print_status "Checking ECR repository: $repo_name" >&2
    
    if aws ecr describe-repositories --repository-names "$repo_name" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_status "ECR repository $repo_name already exists" >&2
    else
        print_status "Creating ECR repository: $repo_name" >&2
        aws ecr create-repository \
            --repository-name "$repo_name" \
            --region "$AWS_REGION" \
            --image-scanning-configuration scanOnPush=true \
            --encryption-configuration encryptionType=AES256 >/dev/null 2>&1
        print_status "ECR repository created successfully" >&2
    fi
}

# Function to build and push Docker image
build_and_push_image() {
    if [ "$SKIP_BUILD" = true ]; then
        print_status "Skipping Docker build (--skip-build flag set)" >&2
        return 0
    fi
    
    local account_id=$(get_aws_account_id)
    if [ -z "$account_id" ]; then
        print_error "Failed to get AWS account ID" >&2
        exit 1
    fi
    
    local ecr_uri="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"
    local image_uri="${ecr_uri}:${IMAGE_TAG}"
    
    print_status "Building Docker image: rag-lambda" >&2
    print_status "Dockerfile: src/rag_lambda/Dockerfile" >&2
    print_status "Platform: linux/amd64 (x86_64)" >&2
    
    # Build the image for x86_64 architecture (Lambda default)
    # Use --platform to ensure correct architecture even when building on ARM Macs
    docker build --platform linux/amd64 -f src/rag_lambda/Dockerfile -t rag-lambda:latest .
    
    if [ $? -ne 0 ]; then
        print_error "Docker build failed" >&2
        exit 1
    fi
    
    print_status "Docker image built successfully" >&2
    
    # Ensure ECR repository exists
    ensure_ecr_repo "$ECR_REPO_NAME"
    
    # Login to ECR
    print_status "Logging in to ECR..." >&2
    aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ecr_uri" >/dev/null 2>&1
    
    # Tag the image
    print_status "Tagging image: $image_uri" >&2
    docker tag rag-lambda:latest "$image_uri"
    
    # Push the image (redirect stdout to stderr to prevent it from being captured)
    print_status "Pushing image to ECR: $image_uri" >&2
    docker push "$image_uri" 1>&2
    
    if [ $? -ne 0 ]; then
        print_error "Docker push failed" >&2
        exit 1
    fi
    
    print_status "Image pushed successfully: $image_uri" >&2
    echo "$image_uri"
}

# Function to validate template
validate_template() {
    print_status "Validating CloudFormation template..."
    if aws cloudformation validate-template --template-body file://$TEMPLATE_FILE --region $AWS_REGION >/dev/null 2>&1; then
        print_status "Template validation successful"
    else
        print_error "Template validation failed"
        exit 1
    fi
}

# Function to check stack status and detect errors
check_stack_status() {
    local stack_status=$(aws cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --region $AWS_REGION \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null)
    
    if [ -z "$stack_status" ]; then
        return 1
    fi
    
    # Check for failure/rollback states
    case "$stack_status" in
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED)
            print_error "Stack is in failed state: $stack_status"
            echo ""
            
            # Get stack status reason
            local status_reason=$(aws cloudformation describe-stacks \
                --stack-name $STACK_NAME \
                --region $AWS_REGION \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)
            
            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Status Reason: $status_reason"
                echo ""
            fi
            
            # Get recent stack events with errors
            print_error "Recent stack events with errors:"
            echo ""
            aws cloudformation describe-stack-events \
                --stack-name $STACK_NAME \
                --region $AWS_REGION \
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
            # Other states (IN_PROGRESS, etc.) - not an error yet
            return 0
            ;;
    esac
}

# Function to show stack status
show_status() {
    print_status "Checking stack status: $STACK_NAME"
    if aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
        aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_status "Stack outputs:"
        aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

# Function to deploy stack
deploy_stack() {
    print_status "Deploying CloudFormation stack: $STACK_NAME"
    
    # Get or build image URI
    local image_uri=""
    if [ "$SKIP_BUILD" = true ]; then
        # Get existing image URI from stack if it exists
        if aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
            image_uri=$(aws cloudformation describe-stacks \
                --stack-name $STACK_NAME \
                --region $AWS_REGION \
                --query 'Stacks[0].Parameters[?ParameterKey==`LambdaImageUri`].ParameterValue' \
                --output text 2>/dev/null)
        fi
        
        if [ -z "$image_uri" ] || [ "$image_uri" == "None" ]; then
            print_error "Cannot skip build: no existing image URI found. Please build the image first or remove --skip-build flag."
            exit 1
        fi
        print_status "Using existing image URI: $image_uri"
    else
        image_uri=$(build_and_push_image)
    fi

    # If a local app config path is provided, upload it to the required S3 URI.
    # This allows CI/CD or local deploys to keep the "real" config out of GitHub.
    if [ -n "$LOCAL_APP_CONFIG_PATH" ]; then
        if [ ! -f "$LOCAL_APP_CONFIG_PATH" ]; then
            print_error "Local app config file not found: $LOCAL_APP_CONFIG_PATH"
            exit 1
        fi
        if [[ "$APP_CONFIG_S3_URI" != s3://* ]]; then
            print_error "Invalid --s3_app_config_uri: $APP_CONFIG_S3_URI (expected s3://bucket/key)"
            exit 1
        fi
        print_status "Uploading local app config to S3 (overwrite): $LOCAL_APP_CONFIG_PATH -> $APP_CONFIG_S3_URI"
        aws s3 cp "$LOCAL_APP_CONFIG_PATH" "$APP_CONFIG_S3_URI" --region "$AWS_REGION" >/dev/null
        print_status "Uploaded app config to S3 successfully"
    fi
    
    # Handle VPC configuration (optional - only needed for postgres backend)
    # Auto-detect parameters if not provided
    if [ -z "$VPC_ID" ] || [ -z "$SUBNET_IDS" ]; then
        print_status "Auto-detecting VPC and subnet IDs..."
        local vpc_outputs=$(get_vpc_stack_outputs)
        if [ $? -eq 0 ] && [ -n "$vpc_outputs" ]; then
            VPC_ID=$(echo "$vpc_outputs" | cut -d'|' -f1)
            SUBNET_IDS=$(echo "$vpc_outputs" | cut -d'|' -f2)
            print_status "Auto-detected VPC ID: $VPC_ID"
            print_status "Auto-detected Subnet IDs: $SUBNET_IDS"
        fi
    fi
    
    if [ -z "$VPC_ID" ]; then
        print_status "VPC ID not provided. Deploying Lambda without VPC (suitable for aurora_data_api backend)."
        print_status "To deploy with VPC (for postgres backend), provide --vpc-id, --subnet-ids, and --security-group-ids."
        VPC_ID=""
        SUBNET_IDS=""
        SECURITY_GROUP_IDS=""
    elif [ -z "$SUBNET_IDS" ]; then
        print_error "Subnet IDs are required when VPC ID is provided. Provide --subnet-ids or deploy VPC stack first."
        exit 1
    else
    # Convert subnet IDs to CloudFormation list format
    local subnet_ids_list=$(echo "$SUBNET_IDS" | tr ',' ' ')
    
    if [ -z "$SECURITY_GROUP_IDS" ]; then
        # Try to get security group from RDS stack
        print_status "Auto-detecting security group IDs..."
        if aws cloudformation describe-stacks --stack-name "$DB_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
            local sg_id=$(aws cloudformation describe-stacks \
                --stack-name "$DB_STACK_NAME" \
                --region "$AWS_REGION" \
                    --query 'Stacks[0].Outputs[?OutputKey==`SecurityGroupId`].OutputValue' \
                --output text 2>/dev/null)
            if [ -n "$sg_id" ] && [ "$sg_id" != "None" ]; then
                SECURITY_GROUP_IDS="$sg_id"
                print_status "Auto-detected Security Group ID: $SECURITY_GROUP_IDS"
            fi
        fi
    fi
    
    if [ -z "$SECURITY_GROUP_IDS" ]; then
            print_error "Security Group IDs are required when VPC is configured. Provide --security-group-ids or ensure DB stack has security group output."
        exit 1
    fi
    
    # Convert security group IDs to CloudFormation list format
    local sg_ids_list=$(echo "$SECURITY_GROUP_IDS" | tr ',' ' ')
        print_status "Deploying Lambda with VPC configuration (for postgres backend)."
    fi
    
    if [ -z "$DB_SECRET_ARN" ]; then
        print_status "Auto-detecting DB secret ARN..."
        local secret_arn=$(get_db_stack_outputs)
        if [ $? -eq 0 ] && [ -n "$secret_arn" ]; then
            DB_SECRET_ARN="$secret_arn"
            print_status "Auto-detected DB Secret ARN: $DB_SECRET_ARN"
        fi
    fi
    
    # DB Secret ARN is optional for aurora_data_api backend (secret ARN is in config file)
    if [ -z "$DB_SECRET_ARN" ]; then
        print_warning "DB Secret ARN not provided. This is OK for aurora_data_api backend (secret ARN should be in config file)."
        print_warning "If using postgres backend, provide --db-secret-arn or deploy DB stack first."
    fi
    
    if [ -z "$KNOWLEDGE_BASE_ID" ]; then
        print_status "Auto-detecting Knowledge Base ID..."
        local kb_id=$(get_kb_stack_outputs)
        if [ $? -eq 0 ] && [ -n "$kb_id" ]; then
            KNOWLEDGE_BASE_ID="$kb_id"
            print_status "Auto-detected Knowledge Base ID: $KNOWLEDGE_BASE_ID"
        fi
    fi
    
    if [ -z "$LAMBDA_ROLE_ARN" ]; then
        print_status "Auto-detecting Lambda execution role ARN..."
        local role_arn=$(get_lambda_role_arn)
        if [ $? -eq 0 ] && [ -n "$role_arn" ]; then
            LAMBDA_ROLE_ARN="$role_arn"
            print_status "Auto-detected Lambda Role ARN: $LAMBDA_ROLE_ARN"
        fi
    fi
    
    if [ -z "$LAMBDA_ROLE_ARN" ]; then
        print_warning "Lambda execution role stack does not exist. Deploying it now..."
        if ! deploy_lambda_role_stack; then
            print_error "Failed to deploy Lambda execution role stack. Cannot proceed with Lambda deployment."
            exit 1
        fi
        
        # Get the role ARN after deployment
        local role_arn=$(get_lambda_role_arn)
        if [ $? -eq 0 ] && [ -n "$role_arn" ]; then
            LAMBDA_ROLE_ARN="$role_arn"
            print_status "Retrieved Lambda Role ARN: $LAMBDA_ROLE_ARN"
        else
            print_error "Failed to retrieve Lambda execution role ARN after deployment."
            exit 1
        fi
    fi
    
    # Extract S3 bucket name from the required AppConfigPath (before JSON generation)
    local config_bucket=""
    if [[ "$APP_CONFIG_S3_URI" =~ ^s3://([^/]+) ]]; then
        config_bucket="${BASH_REMATCH[1]}"
        print_status "Extracted S3 bucket from URI: $config_bucket"
    else
        print_error "Invalid --s3_app_config_uri: $APP_CONFIG_S3_URI (expected s3://bucket/key)"
        exit 1
    fi
    
    # Create a temporary parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    # Build parameters JSON file
    {
        echo "["
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        echo ","
        printf '  {\n    "ParameterKey": "AWSRegion",\n    "ParameterValue": "%s"\n  }' "$AWS_REGION"
        echo ","
        printf '  {\n    "ParameterKey": "LambdaImageUri",\n    "ParameterValue": "%s"\n  }' "$image_uri"
        echo ","
        printf '  {\n    "ParameterKey": "LambdaMemorySize",\n    "ParameterValue": "%s"\n  }' "$LAMBDA_MEMORY_SIZE"
        echo ","
        printf '  {\n    "ParameterKey": "LambdaTimeout",\n    "ParameterValue": "%s"\n  }' "$LAMBDA_TIMEOUT"
        echo ","
        printf '  {\n    "ParameterKey": "VpcId",\n    "ParameterValue": "%s"\n  }' "${VPC_ID:-}"
        echo ","
        printf '  {\n    "ParameterKey": "SubnetIds",\n    "ParameterValue": "%s"\n  }' "${SUBNET_IDS:-}"
        echo ","
        printf '  {\n    "ParameterKey": "SecurityGroupIds",\n    "ParameterValue": "%s"\n  }' "${SECURITY_GROUP_IDS:-}"
        echo ","
        printf '  {\n    "ParameterKey": "DBSecretArn",\n    "ParameterValue": "%s"\n  }' "${DB_SECRET_ARN:-}"
        echo ","
        printf '  {\n    "ParameterKey": "KnowledgeBaseId",\n    "ParameterValue": "%s"\n  }' "$KNOWLEDGE_BASE_ID"
        echo ","
        printf '  {\n    "ParameterKey": "LambdaExecutionRoleArn",\n    "ParameterValue": "%s"\n  }' "$LAMBDA_ROLE_ARN"
        echo ","
        printf '  {\n    "ParameterKey": "AppConfigPath",\n    "ParameterValue": "%s"\n  }' "$APP_CONFIG_S3_URI"
        echo ","
        printf '  {\n    "ParameterKey": "ConfigS3Bucket",\n    "ParameterValue": "%s"\n  }' "$config_bucket"
        
        echo ""
        echo "]"
    } > "$param_file"
    
    # Validate parameters file is valid JSON
    if ! python3 -m json.tool "$param_file" >/dev/null 2>&1; then
        print_error "Generated parameters file is not valid JSON. Contents:"
        cat "$param_file"
        exit 1
    fi
    
    # Check if stack exists
    local stack_operation_result=0
    local no_updates=false
    
    if aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME already exists. Updating..."
        local update_output=$(aws cloudformation update-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters file://$param_file \
            --capabilities CAPABILITY_IAM \
            --region $AWS_REGION 2>&1)
        stack_operation_result=$?
        
        # Check if the error is "No updates are to be performed"
        if [ $stack_operation_result -ne 0 ]; then
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_status "No updates needed for stack $STACK_NAME. Stack is already up to date."
                no_updates=true
                stack_operation_result=0  # Treat as success
            else
                print_error "Stack update failed:"
                echo "$update_output"
            fi
        else
            print_status "Stack update initiated successfully"
        fi
    else
        print_status "Creating new stack: $STACK_NAME"
        local create_output=$(aws cloudformation create-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters file://$param_file \
            --capabilities CAPABILITY_IAM \
            --region $AWS_REGION 2>&1)
        stack_operation_result=$?
        
        if [ $stack_operation_result -ne 0 ]; then
            print_error "Stack creation failed:"
            echo "$create_output"
        else
            print_status "Stack creation initiated successfully"
        fi
    fi
    
    if [ $stack_operation_result -eq 0 ]; then
        if [ "$no_updates" = false ]; then
            print_status "Stack operation initiated successfully"
            print_status "Waiting for stack to be ready (this may take several minutes)..."
            
            # Determine which wait command to use
            local is_update=false
            local initial_status=$(aws cloudformation describe-stacks \
                --stack-name $STACK_NAME \
                --region $AWS_REGION \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            
            if [[ "$initial_status" == *"UPDATE"* ]]; then
                is_update=true
            fi
            
            # Show periodic progress updates while waiting
            local wait_start_time=$(date +%s)
            local last_status_time=$wait_start_time
            local status_check_interval=30  # Check status every 30 seconds
            
            # Function to show progress in background
            (
                while true; do
                    sleep $status_check_interval
                    local current_time=$(date +%s)
                    local elapsed=$((current_time - wait_start_time))
                    local stack_status=$(aws cloudformation describe-stacks \
                        --stack-name $STACK_NAME \
                        --region $AWS_REGION \
                        --query 'Stacks[0].StackStatus' \
                        --output text 2>/dev/null)
                    
                    if [ -n "$stack_status" ] && [ "$stack_status" != "None" ]; then
                        print_status "Stack status: $stack_status (elapsed: ${elapsed}s)" >&2
                    fi
                done
            ) &
            local progress_pid=$!
            
            # Set up cleanup trap to ensure background process is killed
            cleanup_progress() {
                if [ -n "$progress_pid" ] && kill -0 $progress_pid 2>/dev/null; then
                    kill -TERM $progress_pid 2>/dev/null
                    sleep 1
                    if kill -0 $progress_pid 2>/dev/null; then
                        kill -9 $progress_pid 2>/dev/null
                    fi
                    wait $progress_pid 2>/dev/null || true
                fi
            }
            trap cleanup_progress EXIT INT TERM
            
            # Wait for stack operation to complete
            local wait_result=0
            if [ "$is_update" = true ]; then
                print_status "Waiting for stack update to complete..."
                aws cloudformation wait stack-update-complete --stack-name $STACK_NAME --region $AWS_REGION 2>&1
                wait_result=$?
            else
                print_status "Waiting for stack creation to complete..."
                aws cloudformation wait stack-create-complete --stack-name $STACK_NAME --region $AWS_REGION 2>&1
                wait_result=$?
            fi
            
            # Stop progress monitoring immediately
            cleanup_progress
            trap - EXIT INT TERM  # Remove trap after cleanup
            
            # If wait failed, check if it's due to rollback
            if [ $wait_result -ne 0 ]; then
                local current_status=$(aws cloudformation describe-stacks \
                    --stack-name $STACK_NAME \
                    --region $AWS_REGION \
                    --query 'Stacks[0].StackStatus' \
                    --output text 2>/dev/null)
                
                if [[ "$current_status" == *"ROLLBACK"* ]] || [[ "$current_status" == *"FAILED"* ]]; then
                    print_error "Stack deployment failed with status: $current_status"
                    check_stack_status
                    exit 1
                fi
            fi
            
            # Check stack status after wait
            if ! check_stack_status; then
                print_error "Stack deployment failed. See errors above."
                exit 1
            fi
            
            if [ $wait_result -eq 0 ]; then
                print_status "Stack operation completed successfully"
            else
                # Wait command may have failed, but check if stack is actually in a good state
                local final_status=$(aws cloudformation describe-stacks \
                    --stack-name $STACK_NAME \
                    --region $AWS_REGION \
                    --query 'Stacks[0].StackStatus' \
                    --output text 2>/dev/null)
                
                if [[ "$final_status" == "CREATE_COMPLETE" ]] || [[ "$final_status" == "UPDATE_COMPLETE" ]]; then
                    print_status "Stack operation completed successfully (status: $final_status)"
                elif ! check_stack_status; then
                    print_error "Stack deployment failed. See errors above."
                    exit 1
                else
                    print_warning "Stack operation completed with status: $final_status"
                fi
            fi
        else
            # No updates needed, but verify stack is in good state
            if ! check_stack_status; then
                print_error "Stack is in a failed state. See errors above."
                exit 1
            fi
            print_status "Stack is up to date and ready."
        fi
        
        # Get and display Lambda function name
        local lambda_name=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`LambdaFunctionName`].OutputValue' \
            --output text 2>/dev/null)
        
        if [ -n "$lambda_name" ] && [ "$lambda_name" != "None" ]; then
            print_status "Lambda Function Name: $lambda_name"
            
            # Explicitly update Lambda function to use the latest image
            # This ensures the function picks up the new image even if the tag hasn't changed
            print_status "Updating Lambda function to use latest image: $image_uri"
            if aws lambda update-function-code \
                --function-name "$lambda_name" \
                --image-uri "$image_uri" \
                --region "$AWS_REGION" >/dev/null 2>&1; then
                print_status "Lambda function code updated successfully"
                
                # Wait for the function update to complete
                print_status "Waiting for Lambda function update to complete..."
                local update_wait_start=$(date +%s)
                local max_wait=60  # Wait up to 60 seconds
                
                while true; do
                    local update_status=$(aws lambda get-function \
                        --function-name "$lambda_name" \
                        --region "$AWS_REGION" \
                        --query 'Configuration.LastUpdateStatus' \
                        --output text 2>/dev/null)
                    
                    if [ "$update_status" == "Successful" ]; then
                        print_status "Lambda function update completed successfully"
                        break
                    elif [ "$update_status" == "Failed" ]; then
                        print_warning "Lambda function update failed. Check Lambda console for details."
                        break
                    fi
                    
                    local current_time=$(date +%s)
                    local elapsed=$((current_time - update_wait_start))
                    if [ $elapsed -ge $max_wait ]; then
                        print_warning "Lambda function update is taking longer than expected. Status: $update_status"
                        print_warning "The update may still complete. Check Lambda console for current status."
                        break
                    fi
                    
                    sleep 5
                done
            else
                print_warning "Failed to update Lambda function code. The function may still be using an older image."
                print_warning "Try running: aws lambda update-function-code --function-name $lambda_name --image-uri $image_uri --region $AWS_REGION"
            fi
            
            print_status "You can invoke the function or set up API Gateway to expose it."
        fi
        
        print_status "You can monitor the progress in the AWS Console or with:"
        print_status "aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION"
    else
        print_error "Stack operation failed to initiate"
        exit 1
    fi
}

# Function to delete stack
delete_stack() {
    print_warning "Deleting CloudFormation stack: $STACK_NAME"
    print_warning "This will delete:"
    print_warning "  - Lambda function"
    print_warning "  - CloudWatch log group"
    print_warning "Note: This does NOT delete the ECR image or the Lambda execution role"
    read -p "Are you sure you want to delete these resources? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        aws cloudformation delete-stack --stack-name $STACK_NAME --region $AWS_REGION
        if [ $? -eq 0 ]; then
            print_status "Stack deletion initiated"
            print_status "This may take several minutes to complete."
        else
            print_error "Failed to initiate stack deletion"
            exit 1
        fi
    else
        print_status "Stack deletion cancelled"
    fi
}

# Main execution
case $ACTION in
    validate)
        validate_template
        ;;
    status)
        show_status
        ;;
    build)
        build_and_push_image
        print_status "Docker image build and push completed"
        ;;
    deploy|update)
        validate_template
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

print_status "RAG Lambda operation completed successfully"

