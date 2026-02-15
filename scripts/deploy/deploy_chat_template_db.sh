#!/bin/bash

# Aurora Serverless v2 PostgreSQL Database Deployment Script
# This script deploys the light_db CloudFormation stack for different environments
# The database (chat_template_db) includes chat history and embeddings using pgvector
#
# Configuration:
#   Default settings are loaded from infra/infra.yaml and environment-specific secrets
#   files (e.g., infra/secrets/dev_secrets.yaml). All command-line flags are optional
#   and will override the corresponding settings from the configuration files.
#
# Usage Examples:
#   # Deploy to development environment using all defaults from config files
#   ./scripts/deploy/deploy_chat_template_db.sh dev deploy
#
#   # Deploy with password override (other settings from config)
#   ./scripts/deploy/deploy_chat_template_db.sh dev deploy --master-password MySecurePass123
#
#   # Deploy to staging with custom region override
#   ./scripts/deploy/deploy_chat_template_db.sh staging deploy --master-password MySecurePass123 --region us-west-2
#
#   # Deploy to production with multiple overrides
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
#       set via VPC_ID and SUBNET_IDS environment variables, loaded from infra/infra.yaml
#       (environments.<env>.vpc_id and subnet_ids), or auto-detected from a VPC stack (in that priority order).
#       The script will automatically create a Secrets Manager secret if it doesn't exist.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

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
    echo "Configuration:"
    echo "  Default settings are loaded from infra/infra.yaml and environment-specific secrets"
    echo "  files (e.g., infra/secrets/dev_secrets.yaml). All command-line flags are optional"
    echo "  and will override the corresponding settings from the configuration files."
    echo ""
    echo "Options (all optional - override infra/infra.yaml settings):"
    echo "  --master-password <password>   - Master database password"
    echo "                                   (default: loaded from secrets file if available)"
    echo "  --master-username <username>  - Master database username"
    echo "                                   (default: from infra/infra.yaml or secrets file)"
    echo "  --database-name <name>        - Database name"
    echo "                                   (default: chat_template_db)"
    echo "  --min-capacity <acu>          - Minimum ACU capacity"
    echo "                                   (default: from infra/infra.yaml, typically 0)"
    echo "  --max-capacity <acu>          - Maximum ACU capacity"
    echo "                                   (default: from infra/infra.yaml, typically 1)"
    echo "  --region <region>             - AWS region"
    echo "                                   (default: from infra/infra.yaml for environment)"
    echo "  --vpc-id <vpc-id>             - VPC ID"
    echo "                                   (default: from infra/infra.yaml, then auto-detected from VPC stack)"
    echo "  --subnet-ids <id1,id2,...>    - Subnet IDs (comma-separated)"
    echo "                                   (default: from infra/infra.yaml, then auto-detected from VPC stack)"
    echo "  --public-ip <ip>              - Public IP address to allow (CIDR format, e.g., 1.2.3.4/32)"
    echo "                                   (default: auto-detected if not provided)"
    echo "  --public-ip2 <ip>             - Second public IP address to allow (CIDR format)"
    echo "                                   (optional)"
    echo "  --public-ip3 <ip>             - Third public IP address to allow (CIDR format)"
    echo "                                   (optional)"
    echo "  -y, --yes                      - Skip confirmation prompt (deploy/update/delete)"
    echo ""
    echo "Examples:"
    echo "  # Deploy using all defaults from infra/infra.yaml and secrets file"
    echo "  $0 dev deploy"
    echo ""
    echo "  # Deploy with password override (other settings from config)"
    echo "  $0 dev deploy --master-password mypass123"
    echo ""
    echo "  # Deploy with multiple overrides"
    echo "  $0 dev deploy --master-password mypass123 --min-capacity 0.5 --max-capacity 2"
    echo ""
    echo "  # Deploy with VPC/subnet overrides"
    echo "  $0 dev deploy --master-password mypass123 --vpc-id vpc-12345 --subnet-ids subnet-1,subnet-2"
    echo ""
    echo "  # Validate template"
    echo "  $0 staging validate"
    echo ""
    echo "  # Check stack status"
    echo "  $0 prod status"
    echo ""
    echo "Note: Aurora Serverless v2 can scale to 0 ACU (with PostgreSQL 13.15+)."
    echo "      Auto-pause after 30 minutes requires additional automation (Lambda + EventBridge)."
    echo ""
    echo "Secret Management:"
    echo "  After deploying the DB stack, the script will automatically check if a secret exists."
    echo "  If the secret doesn't exist and no password was provided, you will be prompted for"
    echo "  DB username and password. The secret will be created in AWS Secrets Manager with the name:"
    echo "  <ProjectName>-db-connection-<environment> (e.g. chat-template-db-connection-dev)"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}

# Load configuration from infra.yaml
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Get configuration values
PROJECT_NAME=$(get_project_name)
AWS_REGION_DEFAULT=$(get_environment_region "$ENVIRONMENT")
AWS_CLI_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
[ "$AWS_CLI_PROFILE" = "null" ] && AWS_CLI_PROFILE=""

# Get resource configuration from infra.yaml
RESOURCE_NAME="chat_db"
STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME" "main")
SECRET_STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT" "secret_stack_name")
SECRET_TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME" "secret")
SECRET_NAME="db-connection"
VPC_STACK_NAME="chat-template-vpc-${ENVIRONMENT}"

# Load default values from infra.yaml config
MIN_CAPACITY_DEFAULT=$(get_resource_config "$RESOURCE_NAME" "min_acu" "$ENVIRONMENT")
MAX_CAPACITY_DEFAULT=$(get_resource_config "$RESOURCE_NAME" "max_acu" "$ENVIRONMENT")
MASTER_USERNAME_DEFAULT=$(get_resource_config "$RESOURCE_NAME" "master_username" "$ENVIRONMENT")
[ "$MIN_CAPACITY_DEFAULT" = "null" ] && MIN_CAPACITY_DEFAULT="0"
[ "$MAX_CAPACITY_DEFAULT" = "null" ] && MAX_CAPACITY_DEFAULT="1"
[ "$MASTER_USERNAME_DEFAULT" = "null" ] && MASTER_USERNAME_DEFAULT="postgres"

# Try to load secrets from secrets file (if available)
MASTER_PASSWORD_DEFAULT=""
MASTER_USERNAME_FROM_SECRETS=""
if get_secret_value "$ENVIRONMENT" "database.master_password" >/dev/null 2>&1; then
    MASTER_PASSWORD_DEFAULT=$(get_secret_value "$ENVIRONMENT" "database.master_password")
    [ "$MASTER_PASSWORD_DEFAULT" = "null" ] && MASTER_PASSWORD_DEFAULT=""
fi
if get_secret_value "$ENVIRONMENT" "database.master_username" >/dev/null 2>&1; then
    MASTER_USERNAME_FROM_SECRETS=$(get_secret_value "$ENVIRONMENT" "database.master_username")
    [ "$MASTER_USERNAME_FROM_SECRETS" = "null" ] && MASTER_USERNAME_FROM_SECRETS=""
    # Secrets file username overrides infra.yaml default
    [ -n "$MASTER_USERNAME_FROM_SECRETS" ] && MASTER_USERNAME_DEFAULT="$MASTER_USERNAME_FROM_SECRETS"
fi

AUTO_CONFIRM=false

# Parse additional arguments
# VPC ID and subnet IDs can be provided via:
# 1. Command-line arguments (--vpc-id, --subnet-ids) - highest priority
# 2. Environment variables (VPC_ID, SUBNET_IDS)
# 3. Config file (infra/infra.yaml environments.<env>.vpc_id, subnet_ids)
# 4. Auto-detected from VPC stack - lowest priority
VPC_ID_DEFAULT=$(get_environment_vpc_id "$ENVIRONMENT")
[ "$VPC_ID_DEFAULT" = "null" ] && VPC_ID_DEFAULT=""
VPC_ID="${VPC_ID:-$VPC_ID_DEFAULT}"  # Use environment variable if set, otherwise config default
SUBNET_IDS_DEFAULT=$(get_environment_subnet_ids "$ENVIRONMENT")
[ "$SUBNET_IDS_DEFAULT" = "null" ] && SUBNET_IDS_DEFAULT=""
SUBNET_IDS="${SUBNET_IDS:-$SUBNET_IDS_DEFAULT}"  # Use environment variable if set, otherwise config default
MASTER_PASSWORD="${MASTER_PASSWORD_DEFAULT:-}"  # Default from secrets file if available
MASTER_USERNAME="${MASTER_USERNAME_DEFAULT:-}"  # Default from infra.yaml or secrets file
DATABASE_NAME="chat_template_db"
MIN_CAPACITY="${MIN_CAPACITY_DEFAULT:-0}"
MAX_CAPACITY="${MAX_CAPACITY_DEFAULT:-1}"
AWS_REGION="$AWS_REGION_DEFAULT"  # Default AWS region from config
PUBLIC_IP=""  # Will be auto-detected if not provided
PUBLIC_IP2=""  # Optional second IP
PUBLIC_IP3=""  # Optional third IP

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
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

# AWS CLI helper function that uses the CLI profile
aws_cmd() {
    if [ -n "$AWS_CLI_PROFILE" ]; then
        aws --profile "$AWS_CLI_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# Write db_cluster_arn and db_credentials_secret_arn to infra.yaml under environments.<env>
write_db_outputs_to_infra_yaml() {
    ensure_config_loaded || return 1
    local account_id
    account_id=$(yq -r ".environments.${ENVIRONMENT}.account_id" "$INFRA_CONFIG_PATH" 2>/dev/null)
    if [ -z "$account_id" ] || [ "$account_id" = "null" ]; then
        print_warning "Could not read account_id for $ENVIRONMENT; skipping infra.yaml write"
        return 1
    fi
    # DB cluster ARN from main stack
    local cluster_id
    cluster_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterIdentifier`].OutputValue' \
        --output text 2>/dev/null)
    if [ -n "$cluster_id" ] && [ "$cluster_id" != "None" ]; then
        local db_cluster_arn="arn:aws:rds:${AWS_REGION}:${account_id}:cluster:${cluster_id}"
        yq -i ".environments.${ENVIRONMENT}.db_cluster_arn = \"${db_cluster_arn}\"" "$INFRA_CONFIG_PATH"
        print_complete "Wrote db_cluster_arn to infra.yaml (environments.$ENVIRONMENT)"
    fi
    # Secret ARN from secret stack (if it exists)
    if aws_cmd cloudformation describe-stacks --stack-name "$SECRET_STACK_NAME" --query 'Stacks[0].StackId' --output text >/dev/null 2>&1; then
        local secret_arn
        secret_arn=$(aws_cmd cloudformation describe-stacks \
            --stack-name "$SECRET_STACK_NAME" \
            --query 'Stacks[0].Outputs[?OutputKey==`SecretArn`].OutputValue' \
            --output text 2>/dev/null)
        if [ -n "$secret_arn" ] && [ "$secret_arn" != "None" ]; then
            yq -i ".environments.${ENVIRONMENT}.db_credentials_secret_arn = \"${secret_arn}\"" "$INFRA_CONFIG_PATH"
            print_complete "Wrote db_credentials_secret_arn to infra.yaml (environments.$ENVIRONMENT)"
        fi
    fi
    return 0
}

print_step "Starting Aurora DB deployment for $ENVIRONMENT environment"
if [ -n "$AWS_CLI_PROFILE" ]; then
    print_info "Using AWS CLI profile: $AWS_CLI_PROFILE"
fi

# Display configuration (showing final values after command-line overrides)
print_info "Configuration:"
print_info "  - Min Capacity (ACU): $MIN_CAPACITY"
print_info "  - Max Capacity (ACU): $MAX_CAPACITY"
print_info "  - Master Username: ${MASTER_USERNAME:-<will use default from config>}"
if [ -n "$MASTER_PASSWORD" ]; then
    print_info "  - Master Password: <provided>"
elif [ -n "$MASTER_PASSWORD_DEFAULT" ]; then
    print_info "  - Master Password: <loaded from secrets file>"
else
    print_info "  - Master Password: <will prompt if needed>"
fi
if [ -n "$VPC_ID" ]; then
    if [ "$VPC_ID" = "$VPC_ID_DEFAULT" ] && [ -n "$VPC_ID_DEFAULT" ]; then
        print_info "  - VPC ID: $VPC_ID (from config)"
    else
        print_info "  - VPC ID: $VPC_ID"
    fi
elif [ -n "$VPC_ID_DEFAULT" ]; then
    print_info "  - VPC ID: $VPC_ID_DEFAULT (from config, will use if not overridden)"
else
    print_info "  - VPC ID: <will auto-detect from VPC stack if available>"
fi
if [ -n "$SUBNET_IDS" ]; then
    if [ "$SUBNET_IDS" = "$SUBNET_IDS_DEFAULT" ] && [ -n "$SUBNET_IDS_DEFAULT" ]; then
        print_info "  - Subnet IDs: $SUBNET_IDS (from config)"
    else
        print_info "  - Subnet IDs: $SUBNET_IDS"
    fi
elif [ -n "$SUBNET_IDS_DEFAULT" ]; then
    print_info "  - Subnet IDs: $SUBNET_IDS_DEFAULT (from config, will use if not overridden)"
else
    print_info "  - Subnet IDs: <will auto-detect from VPC stack if available>"
fi

if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    # Show deployment summary before confirmation
    print_resource_summary "$RESOURCE_NAME" "$ENVIRONMENT" "$ACTION"
    print_info "Environment: $ENVIRONMENT | Region: $AWS_REGION | Stack: $STACK_NAME"
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Proceed with $ACTION?" || exit 0
    fi
fi

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
    print_info "Detecting your public IP address..." >&2
    
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
    
    print_info "Using public IP: ${public_ip_cidr}"
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
            print_info "Using second public IP: ${public_ip2_cidr}"
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
            print_info "Using third public IP: ${public_ip3_cidr}"
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
    print_info "Retrieving VPC stack outputs from: $VPC_STACK_NAME" >&2
    
    if ! aws_cmd cloudformation describe-stacks --stack-name "$VPC_STACK_NAME" >/dev/null 2>&1; then
        print_warning "VPC stack $VPC_STACK_NAME does not exist in region $AWS_REGION" >&2
        return 1
    fi
    
    local vpc_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$VPC_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' \
        --output text 2>/dev/null)
    
    local subnet_ids=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$VPC_STACK_NAME" \
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
    print_info "Validating CloudFormation template..."
    if aws_cmd cloudformation validate-template --template-body file://$TEMPLATE_FILE >/dev/null 2>&1; then
        print_info "Template validation successful"
    else
        print_error "Template validation failed"
        exit 1
    fi
}

# Function to check stack status and detect errors
check_stack_status() {
    local stack_status=$(aws_cmd cloudformation describe-stacks \
        --stack-name $STACK_NAME \
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
            local status_reason=$(aws_cmd cloudformation describe-stacks \
                --stack-name $STACK_NAME \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)
            
            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Status Reason: $status_reason"
                echo ""
            fi
            
            # Get recent stack events with errors
            print_error "Recent stack events with errors:"
            echo ""
            aws_cmd cloudformation describe-stack-events \
                --stack-name $STACK_NAME \
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
    print_info "Checking stack status: $STACK_NAME"
    if aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME >/dev/null 2>&1; then
        aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_info "Stack outputs:"
        aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

# Function to check if secret exists
check_secret_exists() {
    local secret_name="${PROJECT_NAME}-${SECRET_NAME}-${ENVIRONMENT}"
    aws_cmd secretsmanager describe-secret --secret-id "$secret_name" >/dev/null 2>&1
}

# Function to get DB stack outputs
get_db_outputs() {
    local db_host=$(aws_cmd cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterEndpoint`].OutputValue' \
        --output text 2>/dev/null)
    
    local db_port=$(aws_cmd cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterPort`].OutputValue' \
        --output text 2>/dev/null)
    
    local db_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --query 'Stacks[0].Outputs[?OutputKey==`DatabaseName`].OutputValue' \
        --output text 2>/dev/null)
    
    echo "$db_host|$db_port|$db_name"
}

# Function to ensure credentials are provided (prompt if needed)
ensure_credentials() {
    # Use provided username or default from config/secrets
    local default_username="${MASTER_USERNAME_DEFAULT:-postgres}"
    if [ -z "$MASTER_USERNAME" ]; then
        read -p "Enter database username [$default_username]: " input_username
        MASTER_USERNAME=${input_username:-$default_username}
    fi
    
    # Password is required - prompt if not provided (even if default was loaded)
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
    local db_username=$(aws_cmd cloudformation describe-stacks \
        --stack-name $STACK_NAME \
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
    
    # Check if secret exists (name matches template: ProjectName-SecretName-Environment)
    local secret_full_name="${PROJECT_NAME}-${SECRET_NAME}-${ENVIRONMENT}"
    if check_secret_exists; then
        print_info "Secret $secret_full_name already exists. Skipping secret creation."
        print_info "To update the secret, delete it first or update it manually in AWS Secrets Manager."
        return 0
    fi
    
    print_info "Secret does not exist. Creating secret..."
    
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
    
    print_info "Deploying secret stack: $SECRET_STACK_NAME"
    
    # Check if secret stack exists
    if aws_cmd cloudformation describe-stacks --stack-name $SECRET_STACK_NAME >/dev/null 2>&1; then
        print_warning "Secret stack $SECRET_STACK_NAME already exists. Updating..."
        aws_cmd cloudformation update-stack \
            --stack-name $SECRET_STACK_NAME \
            --template-body file://$SECRET_TEMPLATE_FILE \
            --parameters file://$secret_param_file
    else
        print_info "Creating new secret stack: $SECRET_STACK_NAME"
        aws_cmd cloudformation create-stack \
            --stack-name $SECRET_STACK_NAME \
            --template-body file://$SECRET_TEMPLATE_FILE \
            --parameters file://$secret_param_file
    fi
    
    if [ $? -ne 0 ]; then
        print_error "Secret stack operation failed"
        return 1
    fi

    print_info "Secret stack operation initiated successfully"
    print_info "Waiting for secret stack to reach CREATE_COMPLETE or UPDATE_COMPLETE..."
    if aws_cmd cloudformation describe-stacks --stack-name $SECRET_STACK_NAME --query 'Stacks[0].StackStatus' --output text 2>/dev/null | grep -q "CREATE_IN_PROGRESS"; then
        aws_cmd cloudformation wait stack-create-complete --stack-name $SECRET_STACK_NAME
    elif aws_cmd cloudformation describe-stacks --stack-name $SECRET_STACK_NAME --query 'Stacks[0].StackStatus' --output text 2>/dev/null | grep -q "UPDATE_IN_PROGRESS"; then
        aws_cmd cloudformation wait stack-update-complete --stack-name $SECRET_STACK_NAME
    fi
    print_info "Secret will be available at: $secret_full_name"
}

# Function to deploy stack
deploy_stack() {
    # Credentials should already be provided at this point (checked before validation)
    
    # Auto-detect VPC and subnet IDs if not provided
    if [ -z "$VPC_ID" ] || [ -z "$SUBNET_IDS" ]; then
        print_info "Auto-detecting VPC and subnet IDs..."
        local vpc_outputs=$(get_vpc_stack_outputs)
        if [ $? -eq 0 ] && [ -n "$vpc_outputs" ]; then
            VPC_ID=$(echo "$vpc_outputs" | cut -d'|' -f1)
            SUBNET_IDS=$(echo "$vpc_outputs" | cut -d'|' -f2)
            print_info "Auto-detected VPC ID: $VPC_ID"
            print_info "Auto-detected Subnet IDs: $SUBNET_IDS"
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
    
    print_info "Deploying CloudFormation stack: $STACK_NAME"
    
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
    
    print_info "Using public IP: ${public_ip_cidr}"
    
    # Process second IP if provided
    local public_ip2_cidr=""
    if [ -n "$PUBLIC_IP2" ]; then
        if echo "$PUBLIC_IP2" | grep -qE '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(/[0-9]{1,2})?$'; then
            if echo "$PUBLIC_IP2" | grep -q '/'; then
                public_ip2_cidr="$PUBLIC_IP2"
            else
                public_ip2_cidr="${PUBLIC_IP2}/32"
            fi
            print_info "Using second public IP: ${public_ip2_cidr}"
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
            print_info "Using third public IP: ${public_ip3_cidr}"
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
    
    if aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME already exists. Updating..."
        local update_output=$(aws_cmd cloudformation update-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters file://$param_file \
            --capabilities CAPABILITY_NAMED_IAM 2>&1)
        stack_operation_result=$?
        
        # Check if the error is "No updates are to be performed"
        if [ $stack_operation_result -ne 0 ]; then
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_info "No updates needed for stack $STACK_NAME. Stack is already up to date."
                no_updates=true
                stack_operation_result=0  # Treat as success
            else
                print_error "Stack update failed:"
                echo "$update_output"
            fi
        fi
    else
        print_info "Creating new stack: $STACK_NAME"
        aws_cmd cloudformation create-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters file://$param_file \
            --capabilities CAPABILITY_NAMED_IAM
        stack_operation_result=$?
    fi
    
    if [ $stack_operation_result -eq 0 ]; then
        if [ "$no_updates" = false ]; then
            print_info "Stack operation initiated successfully"
            print_info "Waiting for stack to be ready before creating secret..."
            
            # Wait for stack to be in a stable state
            print_info "Waiting for stack to reach CREATE_COMPLETE or UPDATE_COMPLETE state..."
            
            # Try waiting for create first, then update
            local wait_result=0
            aws_cmd cloudformation wait stack-create-complete --stack-name $STACK_NAME 2>/dev/null
            wait_result=$?
            
            if [ $wait_result -ne 0 ]; then
                # If create wait failed, try update wait
                aws_cmd cloudformation wait stack-update-complete --stack-name $STACK_NAME 2>/dev/null
                wait_result=$?
            fi
            
            # Check stack status after wait
            if ! check_stack_status; then
                print_error "Stack deployment failed. See errors above."
                exit 1
            fi
            
            if [ $wait_result -eq 0 ]; then
                print_info "Stack operation completed successfully"
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
            print_info "Stack is up to date and ready."
        fi
        
        # Deploy secret after DB stack is ready (when we did create/update)
        if [ "$no_updates" = false ]; then
            deploy_secret
        fi
        
        # Write db_cluster_arn and db_credentials_secret_arn to infra.yaml
        write_db_outputs_to_infra_yaml || true
        
        print_info "You can monitor the progress in the AWS Console or with:"
        if [ -n "$AWS_CLI_PROFILE" ]; then
            print_info "aws --profile $AWS_CLI_PROFILE cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION"
        else
            print_info "aws cloudformation describe-stacks --stack-name $STACK_NAME --region $AWS_REGION"
        fi
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
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete Aurora DB and secret stacks ($STACK_NAME, $SECRET_STACK_NAME)" || exit 0
    fi
    # Delete secret stack first (if it exists)
    if aws_cmd cloudformation describe-stacks --stack-name $SECRET_STACK_NAME >/dev/null 2>&1; then
        print_info "Deleting secret stack: $SECRET_STACK_NAME"
        aws_cmd cloudformation delete-stack --stack-name $SECRET_STACK_NAME
        if [ $? -eq 0 ]; then
            print_info "Secret stack deletion initiated"
        else
            print_error "Failed to initiate secret stack deletion"
            exit 1
        fi
    else
        print_info "Secret stack $SECRET_STACK_NAME does not exist, skipping"
    fi

    # Delete DB stack
    print_info "Deleting DB stack: $STACK_NAME"
    aws_cmd cloudformation delete-stack --stack-name $STACK_NAME
    if [ $? -eq 0 ]; then
        print_info "DB stack deletion initiated"
        print_info "Both stacks are being deleted. This may take several minutes."
    else
        print_error "Failed to initiate DB stack deletion"
        exit 1
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

print_complete "Aurora DB operation completed successfully"

