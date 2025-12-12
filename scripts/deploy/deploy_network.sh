#!/bin/bash

# VPC Network Deployment Script
# This script deploys the VPC, subnets, security groups, and VPC endpoints for the RAG chat application
#
# Usage Examples:
#   # Deploy to development environment (default region: us-east-1)
#   ./scripts/deploy/deploy_network.sh dev deploy
#
#   # Deploy to staging with custom VPC CIDR
#   ./scripts/deploy/deploy_network.sh staging deploy --vpc-cidr 10.1.0.0/16
#
#   # Deploy without NAT Gateway (use only VPC endpoints)
#   ./scripts/deploy/deploy_network.sh dev deploy --no-nat-gateway
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_network.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_network.sh dev status
#
#   # Update existing stack
#   ./scripts/deploy/deploy_network.sh dev update
#
#   # Delete stack (with confirmation prompt)
#   ./scripts/deploy/deploy_network.sh dev delete
#
# Note: This script creates:
#       - VPC with public and private subnets (2 AZs)
#       - Internet Gateway
#       - NAT Gateway (optional, for outbound internet access)
#       - Security Groups for Lambda and Database
#       - VPC Endpoints for Bedrock, Secrets Manager, and S3
#       - Route tables and associations

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
    echo -e "${BLUE}[NETWORK]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "VPC Network Deployment Script"
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
    echo "  --vpc-cidr <cidr>          - VPC CIDR block (default: 10.0.0.0/16)"
    echo "  --no-nat-gateway           - Disable NAT Gateway (use only VPC endpoints)"
    echo "  --region <region>          - AWS region (default: us-east-1)"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy"
    echo "  $0 staging deploy --vpc-cidr 10.1.0.0/16"
    echo "  $0 dev deploy --no-nat-gateway"
    echo "  $0 prod validate"
    echo ""
    echo "Note: The script creates a complete network setup with:"
    echo "      - VPC with public and private subnets (2 availability zones)"
    echo "      - Security groups for Lambda and Database"
    echo "      - VPC endpoints for Bedrock, Secrets Manager, and S3"
    echo "      - Optional NAT Gateway for outbound internet access"
    echo ""
    echo "Cost Estimate:"
    echo "  - VPC: Free"
    echo "  - Subnets: Free"
    echo "  - Internet Gateway: Free"
    echo "  - NAT Gateway: ~\$32/month + data transfer (if enabled)"
    echo "  - VPC Endpoints (Interface): ~\$7/month each + data transfer"
    echo "  - VPC Endpoint (S3 Gateway): Free"
    echo "  Total (with NAT): ~\$46/month + data transfer"
    echo "  Total (without NAT): ~\$14/month + data transfer"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="chat-template-vpc-${ENVIRONMENT}"
TEMPLATE_FILE="infra/cloudformation/vpc_template.yaml"
PROJECT_NAME="chat-template"
AWS_REGION="us-east-1"  # Default AWS region
VPC_CIDR="10.0.0.0/16"
ENABLE_NAT_GATEWAY="true"

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --vpc-cidr)
            VPC_CIDR="$2"
            shift 2
            ;;
        --no-nat-gateway)
            ENABLE_NAT_GATEWAY="false"
            shift
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

print_header "Starting network deployment for $ENVIRONMENT environment"

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
        printf '  {\n    "ParameterKey": "VpcCidr",\n    "ParameterValue": "%s"\n  }' "$VPC_CIDR"
        echo ","
        printf '  {\n    "ParameterKey": "EnableNatGateway",\n    "ParameterValue": "%s"\n  }' "$ENABLE_NAT_GATEWAY"
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
        
        # Display key outputs
        print_status "Network resources created:"
        echo ""
        
        local vpc_id=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' \
            --output text 2>/dev/null)
        
        local private_subnets=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`PrivateSubnetIds`].OutputValue' \
            --output text 2>/dev/null)
        
        local lambda_sg=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`LambdaSecurityGroupId`].OutputValue' \
            --output text 2>/dev/null)
        
        local db_sg=$(aws cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --region $AWS_REGION \
            --query 'Stacks[0].Outputs[?OutputKey==`DBSecurityGroupId`].OutputValue' \
            --output text 2>/dev/null)
        
        if [ -n "$vpc_id" ] && [ "$vpc_id" != "None" ]; then
            print_status "VPC ID: $vpc_id"
        fi
        
        if [ -n "$private_subnets" ] && [ "$private_subnets" != "None" ]; then
            print_status "Private Subnet IDs: $private_subnets"
        fi
        
        if [ -n "$lambda_sg" ] && [ "$lambda_sg" != "None" ]; then
            print_status "Lambda Security Group ID: $lambda_sg"
        fi
        
        if [ -n "$db_sg" ] && [ "$db_sg" != "None" ]; then
            print_status "Database Security Group ID: $db_sg"
        fi
        
        echo ""
        print_status "You can now deploy the database and Lambda using these network resources."
        print_status "The deployment scripts will auto-detect these values from the VPC stack."
        print_status ""
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
    print_warning "  - VPC and all subnets"
    print_warning "  - Internet Gateway"
    print_warning "  - NAT Gateway (if enabled)"
    print_warning "  - VPC Endpoints"
    print_warning "  - Security Groups"
    print_warning "  - Route Tables"
    print_warning ""
    print_warning "WARNING: This will affect all resources using this VPC!"
    print_warning "Make sure to delete dependent resources (DB, Lambda) first."
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

print_status "Network operation completed successfully"

