#!/bin/bash

# Aurora Serverless v2 PostgreSQL Database Deployment Script
# This script deploys the light_db CloudFormation stack for different environments
# The database (chat_template_db) includes chat history and embeddings using pgvector
#
# Usage Examples:
#   # Deploy to development environment (default region: us-east-1)
#   ./scripts/deploy/deploy_chat_template_db.sh dev deploy --master-password MySecurePass123
#
#   # Deploy to staging with custom region
#   ./scripts/deploy/deploy_chat_template_db.sh staging deploy --master-password MySecurePass123 --region us-west-2
#
#   # Deploy to production with custom username and capacity
#   ./scripts/deploy/deploy_chat_template_db.sh prod deploy --master-password MySecurePass123 \
#     --master-username admin --min-capacity 0.5 --max-capacity 4
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_chat_template_db.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_chat_template_db.sh dev status
#
#   # Update existing stack
#   ./scripts/deploy/deploy_chat_template_db.sh dev update --master-password NewPassword123
#
#   # Delete stack (with confirmation prompt)
#   ./scripts/deploy/deploy_chat_template_db.sh dev delete
#
# Note: VPC ID and subnet IDs can be provided via --vpc-id and --subnet-ids flags,
#       auto-detected from a VPC stack, or set via VPC_ID and SUBNET_IDS environment variables.
#       The script will automatically create a Secrets Manager secret if it doesn't exist.

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
    echo "  --database-name <name>        - Database name (default: chat_template_db)"
    echo "  --min-capacity <acu>          - Minimum ACU (default: 0 for scale-to-zero)"
    echo "  --max-capacity <acu>          - Maximum ACU (default: 1)"
    echo "  --region <region>             - AWS region (default: us-east-1)"
    echo "  --vpc-id <vpc-id>             - VPC ID (auto-detected from VPC stack if not provided)"
    echo "  --subnet-ids <id1,id2,...>    - Subnet IDs (auto-detected from VPC stack if not provided)"
    echo "  --public-ip <ip>              - Public IP address to allow (CIDR format, e.g., 1.2.3.4/32). Auto-detected if not provided."
    echo "  --public-ip2 <ip>             - Second public IP address to allow (CIDR format, e.g., 1.2.3.4/32). Optional."
    echo "  --public-ip3 <ip>             - Third public IP address to allow (CIDR format, e.g., 1.2.3.4/32). Optional."
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy --master-password mypass123"
    echo "  $0 dev deploy --master-password mypass123 --vpc-id vpc-12345 --subnet-ids subnet-1,subnet-2"
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
    echo "  python-template-chat-template-db-connection-<environment>"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="chat-template-light-db-${ENVIRONMENT}"
TEMPLATE_FILE="infra/resources/light_db_template.yaml"
SECRET_STACK_NAME="chat-template-db-secret-${ENVIRONMENT}"
SECRET_TEMPLATE_FILE="infra/resources/db_secret_template.yaml"
SECRET_NAME="db-connection"
VPC_STACK_NAME="chat-template-vpc-${ENVIRONMENT}"

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

# Parse additional arguments
# VPC ID and subnet IDs can be provided via:
# 1. Command-line arguments (--vpc-id, --subnet-ids)
# 2. Environment variables (VPC_ID, SUBNET_IDS)
# 3. Auto-detected from VPC stack
VPC_ID="${VPC_ID:-}"  # Use environment variable if set, otherwise empty
SUBNET_IDS="${SUBNET_IDS:-}"  # Use environment variable if set, otherwise empty
MASTER_PASSWORD=""
MASTER_USERNAME=""
DATABASE_NAME="chat_template_db"
MIN_CAPACITY="0"
MAX_CAPACITY="1"
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"  # Default AWS region
PUBLIC_IP=""  # Will be auto-detected if not provided
PUBLIC_IP2=""  # Optional second IP
PUBLIC_IP3=""  # Optional third IP

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
        --public-ip)
            PUBLIC_IP="$2"
            shift 2
            ;;
        --public-ip2)
            PUBLIC_IP2="$2"
            shift 2
            ;;
        --public-ip3)
            PUBLIC_IP3="$2"
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

# Function to get public IP address
get_public_ip() {
    print_status "Detecting your public IP address..." >&2
    
    # Try multiple services in case one is unavailable
    local public_ip=""
    
    # Try ipify.org first (simple and reliable)
    public_ip=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null)
    
    # If that fails, try ifconfig.me
    if [ -z "$public_ip" ] || ! echo "$public_ip" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$'; then
        public_ip=$(curl -s --max-time 5 https://ifconfig.me 2>/dev/null)
    fi
    
    # If that fails, try icanhazip.com
    if [ -z "$public_ip" ] || ! echo "$public_ip" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$'; then
        public_ip=$(curl -s --max-time 5 https://icanhazip.com 2>/dev/null)
    fi
    
    # Validate IP format
    if [ -z "$public_ip" ] || ! echo "$public_ip" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$'; then
        print_error "Failed to detect public IP address. Please provide it manually using --public-ip option." >&2
        return 1
    fi
    
    # Format as CIDR (/32 for single IP)
    # Only output the IP to stdout (for command substitution)
    echo "${public_ip}/32"
}

# Function to build parameters array for AWS CLI
build_parameters_array() {
    local params=()
    
    # Base parameters
    local base_params=$(get_base_parameters)
    # Split base params and add to array
    while IFS= read -r param; do
        [ -n "$param" ] && params+=("$param")
    done <<< "$(echo "$base_params" | tr ' ' '\n')"
    
    if [ -n "$VPC_ID" ]; then
        params+=("ParameterKey=VpcId,ParameterValue=$VPC_ID")
    fi
    
    if [ -n "$SUBNET_IDS" ]; then
        # For CloudFormation List parameters, pass comma-separated values
        # AWS CLI expects a single string value for List parameters
        params+=("ParameterKey=SubnetIds,ParameterValue=$SUBNET_IDS")
    fi
    
    # Use provided username or default to postgres (matching template default)
    local db_username="${MASTER_USERNAME:-postgres}"
    params+=("ParameterKey=MasterUsername,ParameterValue=$db_username")
    
    if [ -n "$MASTER_PASSWORD" ]; then
        params+=("ParameterKey=MasterUserPassword,ParameterValue=$MASTER_PASSWORD")
    fi
    
    if [ -n "$DATABASE_NAME" ]; then
        params+=("ParameterKey=DatabaseName,ParameterValue=$DATABASE_NAME")
    fi
    
    if [ -n "$MIN_CAPACITY" ]; then
        params+=("ParameterKey=MinCapacity,ParameterValue=$MIN_CAPACITY")
    fi
    
    if [ -n "$MAX_CAPACITY" ]; then
        params+=("ParameterKey=MaxCapacity,ParameterValue=$MAX_CAPACITY")
    fi
    
    # Get public IP and add to parameters
    local public_ip_cidr=""
    if [ -n "$PUBLIC_IP" ]; then
        # If provided manually, validate and format
        if echo "$PUBLIC_IP" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            # If already in CIDR format, use as-is, otherwise add /32
            if echo "$PUBLIC_IP" | grep -q '/'; then
                public_ip_cidr="$PUBLIC_IP"
            else
                public_ip_cidr="${PUBLIC_IP}/32"
            fi
        else
            print_error "Invalid IP address format: $PUBLIC_IP"
            return 1
        fi
    else
        # Auto-detect public IP
        public_ip_cidr=$(get_public_ip)
        if [ $? -ne 0 ] || [ -z "$public_ip_cidr" ]; then
            print_error "Failed to get public IP address"
            return 1
        fi
    fi
    
    print_status "Using public IP: ${public_ip_cidr}"
    params+=("ParameterKey=AllowedPublicIP,ParameterValue=$public_ip_cidr")
    
    # Add second IP if provided
    if [ -n "$PUBLIC_IP2" ]; then
        local public_ip2_cidr=""
        if echo "$PUBLIC_IP2" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            if echo "$PUBLIC_IP2" | grep -q '/'; then
                public_ip2_cidr="$PUBLIC_IP2"
            else
                public_ip2_cidr="${PUBLIC_IP2}/32"
            fi
            print_status "Using second public IP: ${public_ip2_cidr}"
            params+=("ParameterKey=AllowedPublicIP2,ParameterValue=$public_ip2_cidr")
        else
            print_error "Invalid IP address format for IP2: $PUBLIC_IP2"
            return 1
        fi
    fi
    
    # Add third IP if provided
    if [ -n "$PUBLIC_IP3" ]; then
        local public_ip3_cidr=""
        if echo "$PUBLIC_IP3" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            if echo "$PUBLIC_IP3" | grep -q '/'; then
                public_ip3_cidr="$PUBLIC_IP3"
            else
                public_ip3_cidr="${PUBLIC_IP3}/32"
            fi
            print_status "Using third public IP: ${public_ip3_cidr}"
            params+=("ParameterKey=AllowedPublicIP3,ParameterValue=$public_ip3_cidr")
        else
            print_error "Invalid IP address format for IP3: $PUBLIC_IP3"
            return 1
        fi
    fi
    
    # Print array elements, one per line (for use with array expansion)
    printf '%s\n' "${params[@]}"
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
        --query 'Stacks[0].Outputs[?OutputKey==`PrivateSubnetIds` || OutputKey==`SubnetIds`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -z "$vpc_id" ] || [ "$vpc_id" == "None" ]; then
        print_warning "Could not retrieve VPC ID from stack $VPC_STACK_NAME" >&2
        return 1
    fi
    
    echo "$vpc_id|$subnet_ids"
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

# Function to ensure credentials are provided (prompt if needed)
ensure_credentials() {
    # Use provided username or default to postgres
    if [ -z "$MASTER_USERNAME" ]; then
        read -p "Enter database username [postgres]: " input_username
        MASTER_USERNAME=${input_username:-postgres}
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

# Function to prompt for DB credentials (used when creating secret)
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
    
    # Create a temporary parameters file for secret stack
    local secret_param_file=$(mktemp)
    trap "rm -f $secret_param_file" EXIT
    
    # Build secret parameters JSON file (secret template doesn't need AWSRegion)
    {
        echo "["
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        echo ","
        printf '  {\n    "ParameterKey": "SecretName",\n    "ParameterValue": "%s"\n  }' "$SECRET_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "DBHost",\n    "ParameterValue": "%s"\n  }' "$db_host"
        echo ","
        printf '  {\n    "ParameterKey": "DBPort",\n    "ParameterValue": "%s"\n  }' "$db_port"
        echo ","
        printf '  {\n    "ParameterKey": "DBName",\n    "ParameterValue": "%s"\n  }' "$db_name"
        echo ","
        printf '  {\n    "ParameterKey": "DBUsername",\n    "ParameterValue": "%s"\n  }' "$MASTER_USERNAME"
        echo ","
        printf '  {\n    "ParameterKey": "DBPassword",\n    "ParameterValue": "%s"\n  }' "$MASTER_PASSWORD"
        echo ""
        echo "]"
    } > "$secret_param_file"
    
    print_status "Deploying secret stack: $SECRET_STACK_NAME"
    
    # Check if secret stack exists
    if aws cloudformation describe-stacks --stack-name $SECRET_STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
        print_warning "Secret stack $SECRET_STACK_NAME already exists. Updating..."
        aws cloudformation update-stack \
            --stack-name $SECRET_STACK_NAME \
            --template-body file://$SECRET_TEMPLATE_FILE \
            --parameters file://$secret_param_file \
            --region $AWS_REGION
    else
        print_status "Creating new secret stack: $SECRET_STACK_NAME"
        aws cloudformation create-stack \
            --stack-name $SECRET_STACK_NAME \
            --template-body file://$SECRET_TEMPLATE_FILE \
            --parameters file://$secret_param_file \
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
    # Credentials should already be provided at this point (checked before validation)
    
    # Auto-detect VPC and subnet IDs if not provided
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
    
    # Validate that VPC ID and subnet IDs are provided
    if [ -z "$VPC_ID" ]; then
        print_error "VPC ID is required. Provide --vpc-id, set VPC_ID environment variable, or deploy VPC stack first."
        exit 1
    fi
    
    if [ -z "$SUBNET_IDS" ]; then
        print_error "Subnet IDs are required. Provide --subnet-ids, set SUBNET_IDS environment variable, or deploy VPC stack first."
        exit 1
    fi
    
    # Validate subnet count (need at least 2 for Aurora)
    local subnet_count=$(echo "$SUBNET_IDS" | tr ',' '\n' | wc -l | tr -d ' ')
    if [ "$subnet_count" -lt 2 ]; then
        print_error "At least 2 subnets are required for Aurora (preferably in different AZs)"
        exit 1
    fi
    
    print_status "Deploying CloudFormation stack: $STACK_NAME"
    
    # Get public IP first (before building JSON to avoid status messages in JSON)
    local public_ip_cidr=""
    if [ -n "$PUBLIC_IP" ]; then
        # If provided manually, validate and format
        if echo "$PUBLIC_IP" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            # If already in CIDR format, use as-is, otherwise add /32
            if echo "$PUBLIC_IP" | grep -q '/'; then
                public_ip_cidr="$PUBLIC_IP"
            else
                public_ip_cidr="${PUBLIC_IP}/32"
            fi
        else
            print_error "Invalid IP address format: $PUBLIC_IP"
            exit 1
        fi
    else
        # Auto-detect public IP
        public_ip_cidr=$(get_public_ip)
        if [ $? -ne 0 ] || [ -z "$public_ip_cidr" ]; then
            print_error "Failed to get public IP address"
            exit 1
        fi
    fi
    
    print_status "Using public IP: ${public_ip_cidr}"
    
    # Process second IP if provided
    local public_ip2_cidr=""
    if [ -n "$PUBLIC_IP2" ]; then
        if echo "$PUBLIC_IP2" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            if echo "$PUBLIC_IP2" | grep -q '/'; then
                public_ip2_cidr="$PUBLIC_IP2"
            else
                public_ip2_cidr="${PUBLIC_IP2}/32"
            fi
            print_status "Using second public IP: ${public_ip2_cidr}"
        else
            print_error "Invalid IP address format for IP2: $PUBLIC_IP2"
            exit 1
        fi
    fi
    
    # Process third IP if provided
    local public_ip3_cidr=""
    if [ -n "$PUBLIC_IP3" ]; then
        if echo "$PUBLIC_IP3" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            if echo "$PUBLIC_IP3" | grep -q '/'; then
                public_ip3_cidr="$PUBLIC_IP3"
            else
                public_ip3_cidr="${PUBLIC_IP3}/32"
            fi
            print_status "Using third public IP: ${public_ip3_cidr}"
        else
            print_error "Invalid IP address format for IP3: $PUBLIC_IP3"
            exit 1
        fi
    fi
    
    # Create a temporary parameters file to avoid issues with comma-separated List values
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    # Build parameters JSON file
    {
        echo "["
        
        # Base parameters (ProjectName, Environment, AWSRegion)
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        echo ","
        printf '  {\n    "ParameterKey": "AWSRegion",\n    "ParameterValue": "%s"\n  }' "$AWS_REGION"
        echo ","
        printf '  {\n    "ParameterKey": "VpcId",\n    "ParameterValue": "%s"\n  }' "$VPC_ID"
        echo ","
        # For List parameters, pass as comma-separated string
        printf '  {\n    "ParameterKey": "SubnetIds",\n    "ParameterValue": "%s"\n  }' "$SUBNET_IDS"
        echo ","
        local db_username="${MASTER_USERNAME:-postgres}"
        printf '  {\n    "ParameterKey": "MasterUsername",\n    "ParameterValue": "%s"\n  }' "$db_username"
        echo ","
        printf '  {\n    "ParameterKey": "MasterUserPassword",\n    "ParameterValue": "%s"\n  }' "$MASTER_PASSWORD"
        echo ","
        printf '  {\n    "ParameterKey": "DatabaseName",\n    "ParameterValue": "%s"\n  }' "$DATABASE_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "MinCapacity",\n    "ParameterValue": "%s"\n  }' "$MIN_CAPACITY"
        echo ","
        printf '  {\n    "ParameterKey": "MaxCapacity",\n    "ParameterValue": "%s"\n  }' "$MAX_CAPACITY"
        echo ","
        printf '  {\n    "ParameterKey": "AllowedPublicIP",\n    "ParameterValue": "%s"\n  }' "$public_ip_cidr"
        if [ -n "$public_ip2_cidr" ]; then
            echo ","
            printf '  {\n    "ParameterKey": "AllowedPublicIP2",\n    "ParameterValue": "%s"\n  }' "$public_ip2_cidr"
        fi
        if [ -n "$public_ip3_cidr" ]; then
            echo ","
            printf '  {\n    "ParameterKey": "AllowedPublicIP3",\n    "ParameterValue": "%s"\n  }' "$public_ip3_cidr"
        fi
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
            print_status "Waiting for stack to be ready before creating secret..."
            
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
        
        # Deploy secret after DB stack is ready
        deploy_secret
        
        print_status "You can monitor the progress in the AWS Console or with:"
        print_status "aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION"
        print_warning "Note: Aurora Serverless v2 can scale to 0 ACU (with PostgreSQL 13.15+)."
        print_warning "      However, auto-pause after 30 minutes requires additional automation."
        print_warning "      The cluster will scale based on load but won't auto-pause without custom automation."
    else
        print_error "Stack operation failed to initiate"
        exit 1
    fi
}

# Function to delete stack
delete_stack() {
    print_warning "Deleting CloudFormation stacks"
    print_warning "This will delete:"
    print_warning "  - Aurora database cluster and all data"
    print_warning "  - Database connection secret"
    read -p "Are you sure you want to delete these resources? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Delete secret stack first (if it exists)
        if aws cloudformation describe-stacks --stack-name $SECRET_STACK_NAME --region $AWS_REGION >/dev/null 2>&1; then
            print_status "Deleting secret stack: $SECRET_STACK_NAME"
            aws cloudformation delete-stack --stack-name $SECRET_STACK_NAME --region $AWS_REGION
            if [ $? -eq 0 ]; then
                print_status "Secret stack deletion initiated"
            else
                print_error "Failed to initiate secret stack deletion"
                exit 1
            fi
        else
            print_status "Secret stack $SECRET_STACK_NAME does not exist, skipping"
        fi
        
        # Delete DB stack
        print_status "Deleting DB stack: $STACK_NAME"
        aws cloudformation delete-stack --stack-name $STACK_NAME --region $AWS_REGION
        if [ $? -eq 0 ]; then
            print_status "DB stack deletion initiated"
            print_status "Both stacks are being deleted. This may take several minutes."
        else
            print_error "Failed to initiate DB stack deletion"
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
        # Ensure credentials are provided before validation
        if ! ensure_credentials; then
            exit 1
        fi
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

