#!/bin/bash

# AWS Bedrock Knowledge Base Deployment Script
# This script deploys the knowledge base CloudFormation stack for different environments
# The knowledge base connects to the PostgreSQL database deployed via deploy_chat_template_db.sh
#
# Usage Examples:
#   # Deploy to development environment (default region: us-east-1)
#   ./scripts/deploy/deploy_knowledge_base.sh dev deploy --s3-bucket my-kb-documents-bucket
#
#   # Deploy to staging with custom region and S3 prefix
#   ./scripts/deploy/deploy_knowledge_base.sh staging deploy --s3-bucket my-kb-documents-bucket --s3-prefix staging-docs/ --region us-west-2
#
#   # Deploy to production with custom S3 bucket
#   ./scripts/deploy/deploy_knowledge_base.sh prod deploy --s3-bucket my-kb-documents-bucket
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_knowledge_base.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_knowledge_base.sh dev status
#
#   # Update existing stack
#   ./scripts/deploy/deploy_knowledge_base.sh dev update
#
#   # Delete stack (with confirmation prompt)
#   ./scripts/deploy/deploy_knowledge_base.sh dev delete
#
# Note: This script requires:
#       1. The database stack to be deployed first (via deploy_chat_template_db.sh)
#       2. The database table to be created (run sql/embeddings_table_setup.sql)
#       3. An S3 bucket with documents for the knowledge base
#       It will automatically retrieve DB stack outputs (cluster ID, secret ARN, etc.)

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
    echo -e "${BLUE}[KNOWLEDGE BASE]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "AWS Bedrock Knowledge Base Deployment Script"
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
    echo "  --db-stack-name <name>          - Database stack name (default: chat-template-light-db-<env>)"
    echo "  --embedding-model <model-id>    - Embedding model ID (default: amazon.titan-embed-text-v2)"
    echo "  --table-name <name>             - PostgreSQL table name for embeddings (default: bedrock_integration.bedrock_kb)"
    echo "  --s3-bucket <bucket-name>       - S3 bucket name for knowledge base documents (required)"
    echo "  --s3-prefix <prefix>            - S3 key prefix for documents (default: docs/)"
    echo "  --region <region>               - AWS region (default: us-east-1)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy --s3-bucket my-kb-documents-bucket"
    echo "  $0 staging deploy --s3-bucket my-kb-documents-bucket --s3-prefix staging-docs/"
    echo "  $0 prod status"
    echo ""
    echo "Note: The database stack must be deployed before deploying the knowledge base."
    echo "      The script will automatically retrieve DB stack outputs."
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="chat-template-knowledge-base-${ENVIRONMENT}"
TEMPLATE_FILE="infra/cloudformation/knowledge_base_template.yaml"
DB_STACK_NAME="chat-template-light-db-${ENVIRONMENT}"
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"  # Default AWS region
EMBEDDING_MODEL="amazon.titan-embed-text-v2"
TABLE_NAME="bedrock_integration.bedrock_kb"
S3_BUCKET_NAME=""
S3_INCLUSION_PREFIX="docs/"

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --db-stack-name)
            DB_STACK_NAME="$2"
            shift 2
            ;;
        --embedding-model)
            EMBEDDING_MODEL="$2"
            shift 2
            ;;
        --table-name)
            TABLE_NAME="$2"
            shift 2
            ;;
        --s3-bucket)
            S3_BUCKET_NAME="$2"
            shift 2
            ;;
        --s3-prefix)
            S3_INCLUSION_PREFIX="$2"
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

print_header "Starting Knowledge Base deployment for $ENVIRONMENT environment"

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

# Function to get DB stack outputs
get_db_stack_outputs() {
    print_status "Retrieving database stack outputs from: $DB_STACK_NAME"
    
    # Check if DB stack exists
    if ! aws cloudformation describe-stacks --stack-name "$DB_STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
        print_error "Database stack $DB_STACK_NAME does not exist in region $AWS_REGION"
        print_error "Please deploy the database stack first using deploy_chat_template_db.sh"
        return 1
    fi
    
    # Get DB cluster identifier
    local db_cluster_id=$(aws cloudformation describe-stacks \
        --stack-name "$DB_STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterIdentifier`].OutputValue' \
        --output text 2>/dev/null)
    
    # Get database name
    local db_name=$(aws cloudformation describe-stacks \
        --stack-name "$DB_STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`DatabaseName`].OutputValue' \
        --output text 2>/dev/null)
    
    # Get secret ARN from secret stack
    local secret_stack_name="chat-template-db-secret-${ENVIRONMENT}"
    local secret_arn=""
    
    if aws cloudformation describe-stacks --stack-name "$secret_stack_name" --region "$AWS_REGION" >/dev/null 2>&1; then
        secret_arn=$(aws cloudformation describe-stacks \
            --stack-name "$secret_stack_name" \
            --region "$AWS_REGION" \
            --query 'Stacks[0].Outputs[?OutputKey==`SecretArn`].OutputValue' \
            --output text 2>/dev/null)
    else
        # Try to get secret ARN directly from Secrets Manager
        # Try both naming conventions
        local secret_name1="${PROJECT_NAME}-chat-template-db-connection-${ENVIRONMENT}"
        local secret_name2="python-template-chat-template-db-connection-${ENVIRONMENT}"
        
        if aws secretsmanager describe-secret --secret-id "$secret_name1" --region "$AWS_REGION" >/dev/null 2>&1; then
            secret_arn=$(aws secretsmanager describe-secret --secret-id "$secret_name1" --region "$AWS_REGION" \
                --query 'ARN' --output text 2>/dev/null)
        elif aws secretsmanager describe-secret --secret-id "$secret_name2" --region "$AWS_REGION" >/dev/null 2>&1; then
            secret_arn=$(aws secretsmanager describe-secret --secret-id "$secret_name2" --region "$AWS_REGION" \
                --query 'ARN' --output text 2>/dev/null)
        fi
    fi
    
    if [ -z "$db_cluster_id" ] || [ "$db_cluster_id" == "None" ]; then
        print_error "Could not retrieve DB cluster identifier from stack $DB_STACK_NAME"
        return 1
    fi
    
    if [ -z "$db_name" ] || [ "$db_name" == "None" ]; then
        print_error "Could not retrieve database name from stack $DB_STACK_NAME"
        return 1
    fi
    
    if [ -z "$secret_arn" ] || [ "$secret_arn" == "None" ]; then
        print_error "Could not retrieve secret ARN. Make sure the database secret stack is deployed."
        return 1
    fi
    
    echo "$db_cluster_id|$db_name|$secret_arn"
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
    
    # Get DB stack outputs
    local db_outputs=$(get_db_stack_outputs)
    if [ $? -ne 0 ] || [ -z "$db_outputs" ]; then
        print_error "Failed to retrieve database stack outputs"
        exit 1
    fi
    
    local db_cluster_id=$(echo "$db_outputs" | cut -d'|' -f1)
    local db_name=$(echo "$db_outputs" | cut -d'|' -f2)
    local secret_arn=$(echo "$db_outputs" | cut -d'|' -f3)
    
    print_status "Using DB Cluster ID: $db_cluster_id"
    print_status "Using Database Name: $db_name"
    print_status "Using Secret ARN: $secret_arn"
    
    # Validate required S3 bucket parameter
    if [ -z "$S3_BUCKET_NAME" ]; then
        print_error "S3 bucket name is required. Use --s3-bucket <bucket-name>"
        exit 1
    fi
    
    print_status "Using S3 Bucket: $S3_BUCKET_NAME"
    print_status "Using S3 Prefix: $S3_INCLUSION_PREFIX"
    
    # Create a temporary parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    # Build parameters JSON file matching knowledge_base_template.yaml parameters
    {
        echo "["
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        echo ","
        printf '  {\n    "ParameterKey": "DBClusterIdentifier",\n    "ParameterValue": "%s"\n  }' "$db_cluster_id"
        echo ","
        printf '  {\n    "ParameterKey": "DatabaseName",\n    "ParameterValue": "%s"\n  }' "$db_name"
        echo ","
        printf '  {\n    "ParameterKey": "DBSecretArn",\n    "ParameterValue": "%s"\n  }' "$secret_arn"
        echo ","
        printf '  {\n    "ParameterKey": "TableName",\n    "ParameterValue": "%s"\n  }' "$TABLE_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "EmbeddingModelId",\n    "ParameterValue": "%s"\n  }' "$EMBEDDING_MODEL"
        echo ","
        printf '  {\n    "ParameterKey": "S3BucketName",\n    "ParameterValue": "%s"\n  }' "$S3_BUCKET_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "S3InclusionPrefix",\n    "ParameterValue": "%s"\n  }' "$S3_INCLUSION_PREFIX"
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
            --capabilities CAPABILITY_NAMED_IAM \
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
            --capabilities CAPABILITY_NAMED_IAM \
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
        
        # Get and display Knowledge Base ID
        local kb_id=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' \
            --output text 2>/dev/null)
        
        if [ -n "$kb_id" ] && [ "$kb_id" != "None" ]; then
            print_status "Knowledge Base ID: $kb_id"
            print_status "You can use this ID in your application configuration."
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
    print_warning "  - AWS Bedrock Knowledge Base"
    print_warning "  - Knowledge Base Data Source"
    print_warning "  - IAM roles and policies"
    print_warning "Note: This does NOT delete the Aurora PostgreSQL database or S3 bucket"
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

print_status "Knowledge Base operation completed successfully"

