#!/bin/bash

# S3 Bucket Deployment Script for Knowledge Base Documents
# This script deploys the S3 bucket CloudFormation stack for storing knowledge base documents
#
# Usage Examples:
#   # Deploy to development environment (default region: us-east-1)
#   ./scripts/deploy/deploy_s3_bucket.sh dev deploy
#
#   # Deploy to staging with custom bucket name
#   ./scripts/deploy/deploy_s3_bucket.sh staging deploy --bucket-name my-custom-kb-bucket
#
#   # Deploy to production with custom lifecycle rules
#   ./scripts/deploy/deploy_s3_bucket.sh prod deploy --transition-ia 60 --transition-glacier 180
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_s3_bucket.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_s3_bucket.sh dev status
#
#   # Update existing stack
#   ./scripts/deploy/deploy_s3_bucket.sh dev update
#
#   # Delete stack (with confirmation prompt)
#   ./scripts/deploy/deploy_s3_bucket.sh dev delete
#
# Note: S3 bucket names must be globally unique across all AWS accounts.
#       If you don't specify a bucket name, the script will use: chat-template-s3-bucket-<env>

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
    echo -e "${BLUE}[S3 BUCKET]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "S3 Bucket Deployment Script for Knowledge Base Documents"
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
    echo "  --bucket-name <name>             - Custom S3 bucket name (must be globally unique)"
    echo "  --enable-versioning <true|false> - Enable versioning (default: Enabled)"
    echo "  --enable-lifecycle <true|false>  - Enable lifecycle rules (default: false)"
    echo "  --transition-ia <days>          - Days before transitioning to IA storage (default: 30)"
    echo "  --transition-glacier <days>      - Days before transitioning to Glacier (default: 90, 0 to disable)"
    echo "  --region <region>                - AWS region (default: us-east-1)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy"
    echo "  $0 staging deploy --bucket-name my-kb-bucket"
    echo "  $0 prod deploy --transition-ia 60 --transition-glacier 180"
    echo ""
    echo "Note: S3 bucket names must be globally unique. If not specified,"
    echo "      the script will use: chat-template-s3-bucket-<environment>"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="chat-template-s3-bucket-${ENVIRONMENT}"
TEMPLATE_FILE="infra/resources/s3_bucket_template.yaml"
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"  # Default AWS region
BUCKET_NAME="chat-template-s3-bucket-${ENVIRONMENT}"
ENABLE_VERSIONING="Enabled"
ENABLE_LIFECYCLE_RULES="false"
TRANSITION_TO_IA=30
TRANSITION_TO_GLACIER=90

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --bucket-name)
            BUCKET_NAME="$2"
            shift 2
            ;;
        --enable-versioning)
            if [ "$2" = "true" ] || [ "$2" = "Enabled" ]; then
                ENABLE_VERSIONING="Enabled"
            else
                ENABLE_VERSIONING="Suspended"
            fi
            shift 2
            ;;
        --enable-lifecycle)
            ENABLE_LIFECYCLE_RULES="$2"
            shift 2
            ;;
        --transition-ia)
            TRANSITION_TO_IA="$2"
            shift 2
            ;;
        --transition-glacier)
            TRANSITION_TO_GLACIER="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
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

print_header "Starting S3 bucket deployment for $ENVIRONMENT environment"

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

# Function to validate template
validate_template() {
    print_status "Validating CloudFormation template..."
    local template_path="${PROJECT_ROOT}/${TEMPLATE_FILE}"
    if [ ! -f "$template_path" ]; then
        print_error "Template file not found: $template_path"
        exit 1
    fi
    local validate_out
    local validate_rc=0
    validate_out=$(aws cloudformation validate-template \
        --template-body "file://${template_path}" \
        --region "$AWS_REGION" 2>&1) || validate_rc=$?
    if [ $validate_rc -eq 0 ]; then
        print_status "Template validation successful"
    else
        print_error "Template validation failed"
        echo "$validate_out" | sed 's/^/  /'
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
    
    # Create a temporary parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    # Build parameters JSON file
    {
        echo "["
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        if [ -n "$BUCKET_NAME" ]; then
            echo ","
            printf '  {\n    "ParameterKey": "BucketName",\n    "ParameterValue": "%s"\n  }' "$BUCKET_NAME"
        fi
        echo ","
        printf '  {\n    "ParameterKey": "EnableVersioning",\n    "ParameterValue": "%s"\n  }' "$ENABLE_VERSIONING"
        echo ","
        printf '  {\n    "ParameterKey": "EnableLifecycleRules",\n    "ParameterValue": "%s"\n  }' "$ENABLE_LIFECYCLE_RULES"
        echo ","
        printf '  {\n    "ParameterKey": "TransitionToIA",\n    "ParameterValue": "%s"\n  }' "$TRANSITION_TO_IA"
        echo ","
        printf '  {\n    "ParameterKey": "TransitionToGlacier",\n    "ParameterValue": "%s"\n  }' "$TRANSITION_TO_GLACIER"
        echo ""
        echo "]"
    } > "$param_file"
    
    # Check if stack exists
    local stack_operation_result=0
    local no_updates=false
    
    if aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME already exists. Updating..."
        local update_output=$(aws cloudformation update-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters file://$param_file \
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
        fi
    else
        print_status "Creating new stack: $STACK_NAME"
        aws cloudformation create-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters file://$param_file \
            --region $AWS_REGION
        stack_operation_result=$?
    fi
    
    if [ $stack_operation_result -eq 0 ]; then
        if [ "$no_updates" = false ]; then
            print_status "Stack operation initiated successfully"
            print_status "Waiting for stack to be ready..."
            
            # Wait for stack to be in a stable state
            print_status "Waiting for stack to reach CREATE_COMPLETE or UPDATE_COMPLETE state..."
            
            # Try waiting for create first, then update
            local wait_result=0
            aws cloudformation wait stack-create-complete --stack-name $STACK_NAME --region $AWS_REGION 2>/dev/null
            wait_result=$?
            
            if [ $wait_result -ne 0 ]; then
                # If create wait failed, try update wait
                aws cloudformation wait stack-update-complete --stack-name $STACK_NAME --region $AWS_REGION 2>/dev/null
                wait_result=$?
            fi
            
            # Check stack status after wait
            if ! check_stack_status; then
                print_error "Stack deployment failed. See errors above."
                exit 1
            fi
            
            if [ $wait_result -eq 0 ]; then
                print_status "Stack operation completed successfully"
            else
                # Wait command timed out or failed, but check if stack is actually in a good state
                if ! check_stack_status; then
                    print_error "Stack deployment failed. See errors above."
                    exit 1
                fi
                print_warning "Stack operation may still be in progress, but current status is valid."
            fi
        else
            # No updates needed, but verify stack is in good state
            if ! check_stack_status; then
                print_error "Stack is in a failed state. See errors above."
                exit 1
            fi
            print_status "Stack is up to date and ready."
        fi
        
        # Get and display bucket name
        local bucket_name=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
            --output text 2>/dev/null)
        
        if [ -n "$bucket_name" ] && [ "$bucket_name" != "None" ]; then
            print_status "S3 Bucket Name: $bucket_name"
            
            # Create kb_sources folder in the bucket
            print_status "Creating kb_sources folder in bucket..."
            if aws s3api put-object \
                --bucket "$bucket_name" \
                --key "kb_sources/" \
                --region "$AWS_REGION" \
                --content-length 0 >/dev/null 2>&1; then
                print_status "Successfully created kb_sources folder"
            else
                # Check if folder already exists (this is fine)
                if aws s3 ls "s3://$bucket_name/kb_sources/" --region "$AWS_REGION" >/dev/null 2>&1; then
                    print_status "kb_sources folder already exists"
                else
                    print_warning "Failed to create kb_sources folder, but continuing..."
                fi
            fi
            
            print_status "You can upload documents to: s3://$bucket_name/kb_sources/"
            print_status "The knowledge base will automatically ingest documents from this bucket."
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
    print_warning "  - S3 Bucket (and all its contents)"
    print_warning "  - Bucket policies"
    print_warning ""
    print_warning "WARNING: This will permanently delete all objects in the bucket!"
    read -p "Are you sure you want to delete these resources? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # First, empty the bucket if it exists
        local bucket_name=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
            --output text 2>/dev/null)
        
        if [ -n "$bucket_name" ] && [ "$bucket_name" != "None" ]; then
            print_warning "Emptying bucket: $bucket_name"
            aws s3 rm "s3://$bucket_name" --recursive --region $AWS_REGION 2>/dev/null || true
        fi
        
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

print_status "S3 bucket operation completed successfully"

