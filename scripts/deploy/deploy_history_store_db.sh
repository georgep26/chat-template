#!/bin/bash

# Aurora Serverless v2 PostgreSQL Database Deployment Script
# This script deploys the light_db CloudFormation stack for different environments

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
    echo -e "${BLUE}[AURORA DB]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Aurora Serverless v2 PostgreSQL Database Deployment Script"
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
    echo "  --master-password <password>   - Master database password (required for deploy/update)"
    echo "  --master-username <username>  - Master database username (default: postgres)"
    echo "  --database-name <name>        - Database name (default: chat_history_store_db)"
    echo "  --min-capacity <acu>          - Minimum ACU (default: 0 for scale-to-zero)"
    echo "  --max-capacity <acu>          - Maximum ACU (default: 1)"
    echo "  --region <region>             - AWS region (default: us-east-1)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy --master-password mypass123"
    echo "  $0 staging validate"
    echo "  $0 prod status"
    echo ""
    echo "Note: Aurora Serverless v2 can scale to 0 ACU (with PostgreSQL 13.15+)."
    echo "      Auto-pause after 30 minutes requires additional automation (Lambda + EventBridge)."
    echo ""
    echo "Secret Management:"
    echo "  After deploying the DB stack, the script will automatically check if a secret exists."
    echo "  If the secret doesn't exist, you will be prompted for DB username and password."
    echo "  The secret will be created in AWS Secrets Manager with the name:"
    echo "  python-template-rag-chat-db-connection-<environment>"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="chat-history-store-light-db-${ENVIRONMENT}"
TEMPLATE_FILE="infra/cloudformation/light_db_template.yaml"
SECRET_STACK_NAME="chat-history-store-db-secret-${ENVIRONMENT}"
SECRET_TEMPLATE_FILE="infra/cloudformation/db_secret_template.yaml"
SECRET_NAME="chat-history-store-db-connection"

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

# Parse additional arguments
VPC_ID="vpc-0b861444c1836203c"  # Hardcoded VPC ID
SUBNET_IDS="subnet-08625da8598758097,subnet-06b3a20eaed18c74b,subnet-0766239ba33841e09,subnet-07bb1e0f18d632d99,subnet-06f6bccd8afb0a296,subnet-036d51a3f336fdc5d"  # Hardcoded subnet IDs
MASTER_PASSWORD=""
MASTER_USERNAME=""
DATABASE_NAME="chat_history_store_db"
MIN_CAPACITY="0"
MAX_CAPACITY="1"
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"  # Default AWS region

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --master-password)
            MASTER_PASSWORD="$2"
            shift 2
            ;;
        --master-username)
            MASTER_USERNAME="$2"
            shift 2
            ;;
        --database-name)
            DATABASE_NAME="$2"
            shift 2
            ;;
        --min-capacity)
            MIN_CAPACITY="$2"
            shift 2
            ;;
        --max-capacity)
            MAX_CAPACITY="$2"
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

print_header "Starting Aurora DB deployment for $ENVIRONMENT environment"

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

# Function to get base parameters for environment
get_base_parameters() {
    case $ENVIRONMENT in
        dev)
            echo "ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME ParameterKey=Environment,ParameterValue=dev ParameterKey=AWSRegion,ParameterValue=$AWS_REGION"
            ;;
        staging)
            echo "ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME ParameterKey=Environment,ParameterValue=staging ParameterKey=AWSRegion,ParameterValue=$AWS_REGION"
            ;;
        prod)
            echo "ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME ParameterKey=Environment,ParameterValue=prod ParameterKey=AWSRegion,ParameterValue=$AWS_REGION"
            ;;
    esac
}

# Function to get all parameters
get_parameters() {
    local base_params=$(get_base_parameters)
    local params="$base_params"
    
    if [ -n "$VPC_ID" ]; then
        params="$params ParameterKey=VpcId,ParameterValue=$VPC_ID"
    fi
    
    if [ -n "$SUBNET_IDS" ]; then
        # Convert comma-separated to space-separated for CloudFormation
        local subnet_list=$(echo "$SUBNET_IDS" | tr ',' ' ')
        params="$params ParameterKey=SubnetIds,ParameterValue=\"$subnet_list\""
    fi
    
    # Use provided username or default to postgres (matching template default)
    local db_username="${MASTER_USERNAME:-postgres}"
    params="$params ParameterKey=MasterUsername,ParameterValue=$db_username"
    
    if [ -n "$MASTER_PASSWORD" ]; then
        params="$params ParameterKey=MasterUserPassword,ParameterValue=$MASTER_PASSWORD"
    fi
    
    if [ -n "$DATABASE_NAME" ]; then
        params="$params ParameterKey=DatabaseName,ParameterValue=$DATABASE_NAME"
    fi
    
    if [ -n "$MIN_CAPACITY" ]; then
        params="$params ParameterKey=MinCapacity,ParameterValue=$MIN_CAPACITY"
    fi
    
    if [ -n "$MAX_CAPACITY" ]; then
        params="$params ParameterKey=MaxCapacity,ParameterValue=$MAX_CAPACITY"
    fi
    
    echo "$params"
}

# Function to validate template
validate_template() {
    print_status "Validating CloudFormation template..."
    aws cloudformation validate-template --template-body file://$TEMPLATE_FILE --region $AWS_REGION
    if [ $? -eq 0 ]; then
        print_status "Template validation successful"
    else
        print_error "Template validation failed"
        exit 1
    fi
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

# Function to check if secret exists
check_secret_exists() {
    local secret_name="python-template-${SECRET_NAME}-${ENVIRONMENT}"
    aws secretsmanager describe-secret --secret-id "$secret_name" --region $AWS_REGION >/dev/null 2>&1
}

# Function to get DB stack outputs
get_db_outputs() {
    local db_host=$(aws cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --region $AWS_REGION \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterEndpoint`].OutputValue' \
        --output text 2>/dev/null)
    
    local db_port=$(aws cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --region $AWS_REGION \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterPort`].OutputValue' \
        --output text 2>/dev/null)
    
    local db_name=$(aws cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --region $AWS_REGION \
        --query 'Stacks[0].Outputs[?OutputKey==`DatabaseName`].OutputValue' \
        --output text 2>/dev/null)
    
    echo "$db_host|$db_port|$db_name"
}

# Function to prompt for DB credentials
prompt_db_credentials() {
    # Get the username used for the DB (from parameters or default)
    # Try to get it from the DB stack parameters first
    local db_username=$(aws cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --region $AWS_REGION \
        --query 'Stacks[0].Parameters[?ParameterKey==`MasterUsername`].ParameterValue' \
        --output text 2>/dev/null)
    
    # If not found in stack, use provided or default
    if [ -z "$db_username" ] || [ "$db_username" == "None" ]; then
        db_username="${MASTER_USERNAME:-postgres}"
    fi
    
    # If username not provided via flag, prompt for it
    if [ -z "$MASTER_USERNAME" ]; then
        read -p "Enter database username [$db_username]: " input_username
        MASTER_USERNAME=${input_username:-$db_username}
    else
        # Use the provided username
        MASTER_USERNAME="$MASTER_USERNAME"
    fi
    
    # Password is required - prompt if not provided
    if [ -z "$MASTER_PASSWORD" ]; then
        read -sp "Enter database password: " input_password
        echo
        if [ -z "$input_password" ]; then
            print_error "Password cannot be empty"
            return 1
        fi
        MASTER_PASSWORD="$input_password"
    fi
    return 0
}

# Function to deploy secret
deploy_secret() {
    print_header "Checking for database connection secret..."
    
    # Get DB outputs
    local db_outputs=$(get_db_outputs)
    if [ -z "$db_outputs" ] || [ "$db_outputs" == "None|None|None" ]; then
        print_error "Could not retrieve DB stack outputs. Make sure the DB stack is deployed and in CREATE_COMPLETE or UPDATE_COMPLETE state."
        return 1
    fi
    
    local db_host=$(echo "$db_outputs" | cut -d'|' -f1)
    local db_port=$(echo "$db_outputs" | cut -d'|' -f2)
    local db_name=$(echo "$db_outputs" | cut -d'|' -f3)
    
    # Check if secret exists
    local secret_full_name="python-template-${SECRET_NAME}-${ENVIRONMENT}"
    if check_secret_exists; then
        print_status "Secret $secret_full_name already exists. Skipping secret creation."
        print_status "To update the secret, delete it first or update it manually in AWS Secrets Manager."
        return 0
    fi
    
    print_status "Secret does not exist. Creating secret..."
    
    # Prompt for credentials if not provided
    if ! prompt_db_credentials; then
        return 1
    fi
    
    # Validate secret template exists
    if [ ! -f "$SECRET_TEMPLATE_FILE" ]; then
        print_error "Secret template file not found: $SECRET_TEMPLATE_FILE"
        return 1
    fi
    
    # Get base parameters
    local base_params=$(get_base_parameters)
    
    # Build secret parameters
    local secret_params="$base_params"
    secret_params="$secret_params ParameterKey=SecretName,ParameterValue=$SECRET_NAME"
    secret_params="$secret_params ParameterKey=DBHost,ParameterValue=$db_host"
    secret_params="$secret_params ParameterKey=DBPort,ParameterValue=$db_port"
    secret_params="$secret_params ParameterKey=DBName,ParameterValue=$db_name"
    secret_params="$secret_params ParameterKey=DBUsername,ParameterValue=$MASTER_USERNAME"
    secret_params="$secret_params ParameterKey=DBPassword,ParameterValue=$MASTER_PASSWORD"
    
    print_status "Deploying secret stack: $SECRET_STACK_NAME"
    
    # Check if secret stack exists
    if aws cloudformation describe-stacks --stack-name $SECRET_STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
        print_warning "Secret stack $SECRET_STACK_NAME already exists. Updating..."
        aws cloudformation update-stack \
            --stack-name $SECRET_STACK_NAME \
            --template-body file://$SECRET_TEMPLATE_FILE \
            --parameters $secret_params \
            --region $AWS_REGION
    else
        print_status "Creating new secret stack: $SECRET_STACK_NAME"
        aws cloudformation create-stack \
            --stack-name $SECRET_STACK_NAME \
            --template-body file://$SECRET_TEMPLATE_FILE \
            --parameters $secret_params \
            --region $AWS_REGION
    fi
    
    if [ $? -eq 0 ]; then
        print_status "Secret stack operation initiated successfully"
        print_status "Secret will be available at: $secret_full_name"
    else
        print_error "Secret stack operation failed"
        return 1
    fi
}

# Function to deploy stack
deploy_stack() {
    # Validate required parameters
    if [ -z "$MASTER_PASSWORD" ]; then
        print_error "Master password is required. Use --master-password <password>"
        exit 1
    fi
    
    # Validate subnet count (need at least 2 for Aurora)
    local subnet_count=$(echo "$SUBNET_IDS" | tr ',' '\n' | wc -l | tr -d ' ')
    if [ "$subnet_count" -lt 2 ]; then
        print_error "At least 2 subnets are required for Aurora (preferably in different AZs)"
        exit 1
    fi
    
    print_status "Deploying CloudFormation stack: $STACK_NAME"
    
    # Check if stack exists
    if aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME already exists. Updating..."
        aws cloudformation update-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters $(get_parameters) \
            --capabilities CAPABILITY_NAMED_IAM \
            --region $AWS_REGION
    else
        print_status "Creating new stack: $STACK_NAME"
        aws cloudformation create-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters $(get_parameters) \
            --capabilities CAPABILITY_NAMED_IAM \
            --region $AWS_REGION
    fi
    
    if [ $? -eq 0 ]; then
        print_status "Stack operation initiated successfully"
        print_status "Waiting for stack to be ready before creating secret..."
        
        # Wait for stack to be in a stable state
        print_status "Waiting for stack to reach CREATE_COMPLETE or UPDATE_COMPLETE state..."
        aws cloudformation wait stack-create-complete --stack-name $STACK_NAME --region $AWS_REGION 2>/dev/null || \
        aws cloudformation wait stack-update-complete --stack-name $STACK_NAME --region $AWS_REGION 2>/dev/null || \
        print_warning "Stack operation may still be in progress. Continuing with secret creation..."
        
        # Deploy secret after DB stack is ready
        deploy_secret
        
        print_status "You can monitor the progress in the AWS Console or with:"
        print_status "aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION"
        print_warning "Note: Aurora Serverless v2 can scale to 0 ACU (with PostgreSQL 13.15+)."
        print_warning "      However, auto-pause after 30 minutes requires additional automation."
        print_warning "      The cluster will scale based on load but won't auto-pause without custom automation."
    else
        print_error "Stack operation failed"
        exit 1
    fi
}

# Function to delete stack
delete_stack() {
    print_warning "Deleting CloudFormation stack: $STACK_NAME"
    print_warning "This will delete the Aurora database cluster and all data!"
    read -p "Are you sure you want to delete the stack? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        aws cloudformation delete-stack --stack-name $STACK_NAME --region $AWS_REGION
        if [ $? -eq 0 ]; then
            print_status "Stack deletion initiated"
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

print_status "Aurora DB operation completed successfully"

