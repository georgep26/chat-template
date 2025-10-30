#!/bin/bash

# CloudFormation Deployment Script
# This script deploys the CloudFormation stack for different environments

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
    echo -e "${BLUE}[CLOUDFORMATION]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "CloudFormation Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action]"
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
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 staging deploy"
    echo "  $0 prod validate"
    echo "  $0 dev status"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}
STACK_NAME="python-template-${ENVIRONMENT}"
TEMPLATE_FILE="infra/cloudformation/template.yaml"
PARAMETERS_FILE="infra/cloudformation/parameters.yaml"

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

print_header "Starting CloudFormation deployment for $ENVIRONMENT environment"

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

# Function to get parameters for environment
get_parameters() {
    case $ENVIRONMENT in
        dev)
            echo "ParameterKey=ProjectName,ParameterValue=python-template ParameterKey=Environment,ParameterValue=dev ParameterKey=AWSRegion,ParameterValue=us-east-1"
            ;;
        staging)
            echo "ParameterKey=ProjectName,ParameterValue=python-template ParameterKey=Environment,ParameterValue=staging ParameterKey=AWSRegion,ParameterValue=us-east-1"
            ;;
        prod)
            echo "ParameterKey=ProjectName,ParameterValue=python-template ParameterKey=Environment,ParameterValue=prod ParameterKey=AWSRegion,ParameterValue=us-east-1"
            ;;
    esac
}

# Function to validate template
validate_template() {
    print_status "Validating CloudFormation template..."
    aws cloudformation validate-template --template-body file://$TEMPLATE_FILE
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
    if aws cloudformation describe-stacks --stack-name $STACK_NAME >/dev/null 2>&1; then
        aws cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_status "Stack outputs:"
        aws cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

# Function to deploy stack
deploy_stack() {
    print_status "Deploying CloudFormation stack: $STACK_NAME"
    
    # Check if stack exists
    if aws cloudformation describe-stacks --stack-name $STACK_NAME >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME already exists. Updating..."
        aws cloudformation update-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters $(get_parameters) \
            --capabilities CAPABILITY_NAMED_IAM
    else
        print_status "Creating new stack: $STACK_NAME"
        aws cloudformation create-stack \
            --stack-name $STACK_NAME \
            --template-body file://$TEMPLATE_FILE \
            --parameters $(get_parameters) \
            --capabilities CAPABILITY_NAMED_IAM
    fi
    
    if [ $? -eq 0 ]; then
        print_status "Stack operation initiated successfully"
        print_status "You can monitor the progress in the AWS Console or with:"
        print_status "aws cloudformation describe-stacks --stack-name $STACK_NAME"
    else
        print_error "Stack operation failed"
        exit 1
    fi
}

# Function to delete stack
delete_stack() {
    print_warning "Deleting CloudFormation stack: $STACK_NAME"
    read -p "Are you sure you want to delete the stack? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        aws cloudformation delete-stack --stack-name $STACK_NAME
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

print_status "CloudFormation operation completed successfully"
