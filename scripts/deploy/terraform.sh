#!/bin/bash

# Terraform Deployment Script
# This script deploys the Terraform infrastructure for different environments

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
    echo -e "${BLUE}[TERRAFORM]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Terraform Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Actions:"
    echo "  deploy    - Deploy the infrastructure (default)"
    echo "  plan      - Show deployment plan"
    echo "  destroy   - Destroy the infrastructure"
    echo "  init      - Initialize Terraform"
    echo "  validate  - Validate Terraform configuration"
    echo "  output    - Show Terraform outputs"
    echo ""
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 staging plan"
    echo "  $0 prod destroy"
    echo "  $0 dev output"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
ACTION=${2:-deploy}

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
TERRAFORM_DIR="$PROJECT_ROOT/infra/terraform"

# Change to terraform directory
cd "$TERRAFORM_DIR"

print_header "Starting Terraform deployment for $ENVIRONMENT environment"

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

# Set Terraform workspace
WORKSPACE_NAME="python-template-${ENVIRONMENT}"

# Function to initialize Terraform
init_terraform() {
    print_status "Initializing Terraform..."
    terraform init
    
    # Create workspace if it doesn't exist
    if ! terraform workspace list | grep -q "$WORKSPACE_NAME"; then
        print_status "Creating Terraform workspace: $WORKSPACE_NAME"
        terraform workspace new "$WORKSPACE_NAME"
    else
        print_status "Selecting Terraform workspace: $WORKSPACE_NAME"
        terraform workspace select "$WORKSPACE_NAME"
    fi
}

# Function to validate Terraform configuration
validate_terraform() {
    print_status "Validating Terraform configuration..."
    terraform validate
    if [ $? -eq 0 ]; then
        print_status "Terraform configuration is valid"
    else
        print_error "Terraform configuration validation failed"
        exit 1
    fi
}

# Function to show Terraform plan
show_plan() {
    print_status "Creating Terraform plan for $ENVIRONMENT environment..."
    terraform plan \
        -var="environment=$ENVIRONMENT" \
        -var="project_name=python-template" \
        -var="aws_region=us-east-1" \
        -out="terraform-${ENVIRONMENT}.plan"
    
    if [ $? -eq 0 ]; then
        print_status "Plan created successfully: terraform-${ENVIRONMENT}.plan"
    else
        print_error "Plan creation failed"
        exit 1
    fi
}

# Function to deploy infrastructure
deploy_infrastructure() {
    print_status "Deploying Terraform infrastructure for $ENVIRONMENT environment..."
    
    # Check if plan file exists
    if [ -f "terraform-${ENVIRONMENT}.plan" ]; then
        print_status "Using existing plan file: terraform-${ENVIRONMENT}.plan"
        terraform apply "terraform-${ENVIRONMENT}.plan"
    else
        print_status "Creating and applying plan..."
        terraform apply \
            -var="environment=$ENVIRONMENT" \
            -var="project_name=python-template" \
            -var="aws_region=us-east-1" \
            -auto-approve
    fi
    
    if [ $? -eq 0 ]; then
        print_status "Infrastructure deployed successfully"
    else
        print_error "Infrastructure deployment failed"
        exit 1
    fi
}

# Function to show outputs
show_outputs() {
    print_status "Terraform outputs for $ENVIRONMENT environment:"
    terraform output
}

# Function to destroy infrastructure
destroy_infrastructure() {
    print_warning "Destroying Terraform infrastructure for $ENVIRONMENT environment..."
    read -p "Are you sure you want to destroy the infrastructure? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        terraform destroy \
            -var="environment=$ENVIRONMENT" \
            -var="project_name=python-template" \
            -var="aws_region=us-east-1" \
            -auto-approve
        
        if [ $? -eq 0 ]; then
            print_status "Infrastructure destroyed successfully"
        else
            print_error "Infrastructure destruction failed"
            exit 1
        fi
    else
        print_status "Infrastructure destruction cancelled"
    fi
}

# Main execution
case $ACTION in
    init)
        init_terraform
        ;;
    validate)
        validate_terraform
        ;;
    plan)
        init_terraform
        validate_terraform
        show_plan
        ;;
    deploy)
        init_terraform
        validate_terraform
        deploy_infrastructure
        show_outputs
        ;;
    output)
        show_outputs
        ;;
    destroy)
        init_terraform
        destroy_infrastructure
        ;;
    *)
        print_error "Invalid action: $ACTION"
        show_usage
        exit 1
        ;;
esac

print_status "Terraform operation completed successfully"
