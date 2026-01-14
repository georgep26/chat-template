#!/bin/bash

# Full Application Deployment Script
# This script orchestrates the deployment of all infrastructure components for the RAG chat application
#
# Usage Examples:
#   # Deploy to development environment
#   ./scripts/deploy/deploy_all.sh dev --s3-app-config-uri s3://my-bucket/config/app_config.yml
#
#   # Deploy to staging with custom region
#   ./scripts/deploy/deploy_all.sh staging --s3-app-config-uri s3://my-bucket/config/app_config.yml --region us-west-2
#
#   # Deploy to production with all options
#   ./scripts/deploy/deploy_all.sh prod --s3-app-config-uri s3://my-bucket/config/app_config.yml \
#     --master-password MySecurePass123 --region us-east-1
#
#   # Deploy with local app config file
#   ./scripts/deploy/deploy_all.sh dev --s3-app-config-uri s3://my-bucket/config/app_config.yml \
#     --local-app-config-path config/app_config.yml
#
# Note: This script deploys components in the following order:
#       1. Network (VPC, subnets, security groups) - optional, but included
#       2. S3 Bucket (for knowledge base documents)
#       3. Database (Aurora PostgreSQL) - requires Network
#       4. Knowledge Base (AWS Bedrock) - requires Database and S3
#       5. Lambda Function - requires Database, Knowledge Base, and optionally Network
#       6. Cost Allocation Tags - activates tags for Cost Explorer (optional)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
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
    echo -e "${BLUE}[DEPLOY ALL]${NC} $1"
}

print_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Full Application Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Required Arguments:"
    echo "  --s3-app-config-uri <uri>     - S3 URI for app config file (e.g., s3://bucket/key)"
    echo ""
    echo "Optional Arguments:"
    echo "  --local-app-config-path <path> - Local app config file to upload to S3"
    echo "  --master-password <password>    - Master database password (required if DB doesn't exist)"
    echo "  --master-username <username>    - Master database username (default: postgres)"
    echo "  --region <region>               - AWS region (default: us-east-1)"
    echo "  --skip-network                 - Skip network deployment (use existing VPC)"
    echo "  --skip-s3                       - Skip S3 bucket deployment (use existing bucket)"
    echo "  --skip-db                       - Skip database deployment (use existing DB)"
    echo "  --skip-kb                       - Skip knowledge base deployment (use existing KB)"
    echo "  --skip-lambda                  - Skip Lambda deployment (use existing Lambda)"
    echo "  --skip-cost-tags               - Skip cost allocation tags activation"
    echo "  --vpc-id <vpc-id>               - VPC ID (for Lambda, if not using auto-detection)"
    echo "  --subnet-ids <id1,id2,...>      - Subnet IDs (for Lambda, if not using auto-detection)"
    echo "  --security-group-ids <id1,...>  - Security group IDs (for Lambda, if not using auto-detection)"
    echo ""
    echo "Examples:"
    echo "  $0 dev --s3-app-config-uri s3://my-bucket/config/app_config.yml"
    echo "  $0 staging --s3-app-config-uri s3://my-bucket/config/app_config.yml --region us-west-2"
    echo "  $0 prod --s3-app-config-uri s3://my-bucket/config/app_config.yml --master-password MyPass123"
    echo ""
    echo "Note: The script will deploy all components in order. If a component already exists,"
    echo "      it will be updated (or show 'no updates needed' if already up to date)."
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
S3_APP_CONFIG_URI=""
LOCAL_APP_CONFIG_PATH=""
MASTER_PASSWORD=""
MASTER_USERNAME=""
AWS_REGION="us-east-1"
SKIP_NETWORK=false
SKIP_S3=false
SKIP_DB=false
SKIP_KB=false
SKIP_LAMBDA=false
SKIP_COST_TAGS=false
VPC_ID=""
SUBNET_IDS=""
SECURITY_GROUP_IDS=""

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Change to project root directory
cd "$PROJECT_ROOT"

shift 1  # Remove environment from arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --s3-app-config-uri)
            S3_APP_CONFIG_URI="$2"
            shift 2
            ;;
        --local-app-config-path)
            LOCAL_APP_CONFIG_PATH="$2"
            shift 2
            ;;
        --master-password)
            MASTER_PASSWORD="$2"
            shift 2
            ;;
        --master-username)
            MASTER_USERNAME="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --skip-network)
            SKIP_NETWORK=true
            shift
            ;;
        --skip-s3)
            SKIP_S3=true
            shift
            ;;
        --skip-db)
            SKIP_DB=true
            shift
            ;;
        --skip-kb)
            SKIP_KB=true
            shift
            ;;
        --skip-lambda)
            SKIP_LAMBDA=true
            shift
            ;;
        --skip-cost-tags)
            SKIP_COST_TAGS=true
            shift
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
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

print_header "Starting full application deployment for $ENVIRONMENT environment"
print_status "Region: $AWS_REGION"

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

# Validate required parameters
if [ "$SKIP_LAMBDA" = false ] && [ -z "$S3_APP_CONFIG_URI" ]; then
    print_error "--s3-app-config-uri is required when deploying Lambda"
    show_usage
    exit 1
fi

# Function to run a deployment script and handle errors
run_deployment_script() {
    local script_name=$1
    local script_args=("${@:2}")
    local step_name=$1
    
    print_step "Deploying: $step_name"
    
    if [ ! -f "$SCRIPT_DIR/$script_name" ]; then
        print_error "Script not found: $SCRIPT_DIR/$script_name"
        exit 1
    fi
    
    # Make script executable
    chmod +x "$SCRIPT_DIR/$script_name"
    
    # Run the script with all arguments
    if "$SCRIPT_DIR/$script_name" "${script_args[@]}"; then
        print_status "$step_name deployment completed successfully"
        return 0
    else
        print_error "$step_name deployment failed"
        exit 1
    fi
}

# Track deployment steps
DEPLOYMENT_STEPS=0
FAILED_STEPS=()

# Step 1: Deploy Network (VPC, subnets, security groups)
if [ "$SKIP_NETWORK" = false ]; then
    ((DEPLOYMENT_STEPS++))
    print_header "Step $DEPLOYMENT_STEPS: Deploying Network Infrastructure"
    if run_deployment_script "deploy_network.sh" "$ENVIRONMENT" "deploy" --region "$AWS_REGION"; then
        print_status "Network deployment completed"
    else
        FAILED_STEPS+=("Network")
        exit 1
    fi
    echo ""
else
    print_warning "Skipping network deployment (--skip-network flag set)"
fi

# Step 2: Deploy S3 Bucket
if [ "$SKIP_S3" = false ]; then
    ((DEPLOYMENT_STEPS++))
    print_header "Step $DEPLOYMENT_STEPS: Deploying S3 Bucket"
    if run_deployment_script "deploy_s3_bucket.sh" "$ENVIRONMENT" "deploy" --region "$AWS_REGION"; then
        print_status "S3 bucket deployment completed"
    else
        FAILED_STEPS+=("S3 Bucket")
        exit 1
    fi
    echo ""
else
    print_warning "Skipping S3 bucket deployment (--skip-s3 flag set)"
fi

# Step 3: Deploy Database
if [ "$SKIP_DB" = false ]; then
    ((DEPLOYMENT_STEPS++))
    print_header "Step $DEPLOYMENT_STEPS: Deploying Database"
    
    # Build database deployment arguments
    local db_args=("$ENVIRONMENT" "deploy" --region "$AWS_REGION")
    
    if [ -n "$MASTER_PASSWORD" ]; then
        db_args+=(--master-password "$MASTER_PASSWORD")
    fi
    
    if [ -n "$MASTER_USERNAME" ]; then
        db_args+=(--master-username "$MASTER_USERNAME")
    fi
    
    if run_deployment_script "deploy_chat_template_db.sh" "${db_args[@]}"; then
        print_status "Database deployment completed"
    else
        FAILED_STEPS+=("Database")
        exit 1
    fi
    echo ""
else
    print_warning "Skipping database deployment (--skip-db flag set)"
fi

# Step 4: Deploy Knowledge Base
if [ "$SKIP_KB" = false ]; then
    ((DEPLOYMENT_STEPS++))
    print_header "Step $DEPLOYMENT_STEPS: Deploying Knowledge Base"
    if run_deployment_script "deploy_knowledge_base.sh" "$ENVIRONMENT" "deploy" --region "$AWS_REGION"; then
        print_status "Knowledge base deployment completed"
    else
        FAILED_STEPS+=("Knowledge Base")
        exit 1
    fi
    echo ""
else
    print_warning "Skipping knowledge base deployment (--skip-kb flag set)"
fi

# Step 5: Deploy Lambda Function
if [ "$SKIP_LAMBDA" = false ]; then
    ((DEPLOYMENT_STEPS++))
    print_header "Step $DEPLOYMENT_STEPS: Deploying Lambda Function"
    
    # Build Lambda deployment arguments
    local lambda_args=("$ENVIRONMENT" "deploy" --s3_app_config_uri "$S3_APP_CONFIG_URI" --region "$AWS_REGION")
    
    if [ -n "$LOCAL_APP_CONFIG_PATH" ]; then
        lambda_args+=(--local_app_config_path "$LOCAL_APP_CONFIG_PATH")
    fi
    
    if [ -n "$VPC_ID" ]; then
        lambda_args+=(--vpc-id "$VPC_ID")
    fi
    
    if [ -n "$SUBNET_IDS" ]; then
        lambda_args+=(--subnet-ids "$SUBNET_IDS")
    fi
    
    if [ -n "$SECURITY_GROUP_IDS" ]; then
        lambda_args+=(--security-group-ids "$SECURITY_GROUP_IDS")
    fi
    
    if run_deployment_script "deploy_rag_lambda.sh" "${lambda_args[@]}"; then
        print_status "Lambda deployment completed"
    else
        FAILED_STEPS+=("Lambda")
        exit 1
    fi
    echo ""
else
    print_warning "Skipping Lambda deployment (--skip-lambda flag set)"
fi

# Step 6: Activate Cost Allocation Tags
if [ "$SKIP_COST_TAGS" = false ]; then
    ((DEPLOYMENT_STEPS++))
    print_header "Step $DEPLOYMENT_STEPS: Activating Cost Allocation Tags"
    
    # Make script executable
    if [ ! -f "$SCRIPT_DIR/deploy_cost_analysis_tags.sh" ]; then
        print_warning "Cost tags script not found, skipping activation"
    else
        chmod +x "$SCRIPT_DIR/deploy_cost_analysis_tags.sh"
        
        # Run cost tags activation (don't fail deployment if this fails)
        if "$SCRIPT_DIR/deploy_cost_analysis_tags.sh" "activate" --region "$AWS_REGION" 2>&1; then
            print_status "Cost allocation tags activation completed"
        else
            print_warning "Cost allocation tags activation failed or tags not found yet"
            print_warning "Tags will be activated automatically when resources are tagged"
            print_warning "You can manually activate them later using: ./scripts/deploy/deploy_cost_analysis_tags.sh activate"
            # Don't fail the deployment if cost tags fail - it's not critical
        fi
    fi
    echo ""
else
    print_warning "Skipping cost allocation tags activation (--skip-cost-tags flag set)"
fi

# Summary
print_header "Deployment Summary"
print_status "Environment: $ENVIRONMENT"
print_status "Region: $AWS_REGION"
print_status "Total steps completed: $DEPLOYMENT_STEPS"

if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    print_status "All deployment steps completed successfully!"
    print_status ""
    print_status "Your application is now deployed and ready to use."
    print_status ""
    print_status "Next steps:"
    print_status "  1. Verify Lambda function is working"
    print_status "  2. Set up API Gateway if needed"
    print_status "  3. Test the application"
else
    print_error "Some deployment steps failed:"
    for step in "${FAILED_STEPS[@]}"; do
        print_error "  - $step"
    done
    exit 1
fi

print_status "Full application deployment completed successfully"

