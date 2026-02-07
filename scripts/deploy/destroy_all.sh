#!/bin/bash

# Destroy All Resources for an Environment
# This script tears down all infrastructure for a given environment by calling
# the delete action on each deploy script in the correct dependency order.
#
# Usage Examples:
#   # Destroy development environment (with confirmation prompts per component)
#   ./scripts/deploy/destroy_all.sh dev
#
#   # Destroy staging with custom region
#   ./scripts/deploy/destroy_all.sh staging --region us-west-2
#
#   # Destroy with single confirmation (auto-confirm each component)
#   ./scripts/deploy/destroy_all.sh dev --force
#
#   # Destroy only Lambda and Knowledge Base (skip DB, S3, Network)
#   ./scripts/deploy/destroy_all.sh dev --skip-db --skip-s3 --skip-network
#
# Destroy order (reverse of deploy, so dependencies are removed first):
#   1. Lambda
#   2. Knowledge Base
#   3. Database
#   4. S3 Bucket
#   5. Network

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

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
    echo -e "${BLUE}[DESTROY ALL]${NC} $1"
}

print_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

show_usage() {
    echo "Destroy All Resources for an Environment"
    echo ""
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Options:"
    echo "  --region <region>     - AWS region (default: us-east-1)"
    echo "  --force, -y           - Skip per-component confirmation prompts (single prompt at start)"
    echo "  --skip-lambda         - Do not destroy Lambda stack"
    echo "  --skip-kb             - Do not destroy Knowledge Base stack"
    echo "  --skip-db             - Do not destroy Database stack"
    echo "  --skip-s3             - Do not destroy S3 bucket stack"
    echo "  --skip-network        - Do not destroy Network stack"
    echo ""
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 staging --region us-west-2"
    echo "  $0 dev --force"
    echo "  $0 prod --skip-network   # Destroy all except VPC/network"
    echo ""
    echo "Destroy order: Lambda → Knowledge Base → Database → S3 → Network"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
AWS_REGION="us-east-1"
FORCE=false
SKIP_LAMBDA=false
SKIP_KB=false
SKIP_DB=false
SKIP_S3=false
SKIP_NETWORK=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

shift 1
while [[ $# -gt 0 ]]; do
    case $1 in
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --force|-y)
            FORCE=true
            shift
            ;;
        --skip-lambda)
            SKIP_LAMBDA=true
            shift
            ;;
        --skip-kb)
            SKIP_KB=true
            shift
            ;;
        --skip-db)
            SKIP_DB=true
            shift
            ;;
        --skip-s3)
            SKIP_S3=true
            shift
            ;;
        --skip-network)
            SKIP_NETWORK=true
            shift
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

print_header "Destroying all resources for environment: $ENVIRONMENT"
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

# Optional skip summary
if [ "$SKIP_LAMBDA" = true ] || [ "$SKIP_KB" = true ] || [ "$SKIP_DB" = true ] || [ "$SKIP_S3" = true ] || [ "$SKIP_NETWORK" = true ]; then
    print_warning "Skipping:"
    [ "$SKIP_LAMBDA" = true ] && print_warning "  - Lambda"
    [ "$SKIP_KB" = true ] && print_warning "  - Knowledge Base"
    [ "$SKIP_DB" = true ] && print_warning "  - Database"
    [ "$SKIP_S3" = true ] && print_warning "  - S3 Bucket"
    [ "$SKIP_NETWORK" = true ] && print_warning "  - Network"
fi

# Global confirmation unless --force
if [ "$FORCE" = false ]; then
    echo ""
    print_warning "This will DELETE all selected resources for '$ENVIRONMENT' in $AWS_REGION."
    print_warning "Each component may prompt for confirmation."
    read -p "Continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Aborted."
        exit 0
    fi
    echo ""
fi

# Run a destroy script: $1 = script name, $2 = human name. Returns exit code of the script.
run_destroy_script() {
    local script_name=$1
    local step_name=$2

    if [ ! -f "$SCRIPT_DIR/$script_name" ]; then
        print_error "Script not found: $SCRIPT_DIR/$script_name"
        exit 1
    fi
    chmod +x "$SCRIPT_DIR/$script_name"

    print_step "Destroying: $step_name"
    local rc=0
    if [ "$FORCE" = true ]; then
        echo "y" | "$SCRIPT_DIR/$script_name" "$ENVIRONMENT" "delete" --region "$AWS_REGION" || rc=$?
    else
        "$SCRIPT_DIR/$script_name" "$ENVIRONMENT" "delete" --region "$AWS_REGION" || rc=$?
    fi
    if [ $rc -eq 0 ]; then
        print_status "$step_name destroyed successfully"
    else
        print_warning "$step_name destroy failed or stack did not exist (continuing)"
    fi
    return $rc
}

DESTROY_STEPS=0
FAILED_STEPS=()

# 1. Lambda
if [ "$SKIP_LAMBDA" = false ]; then
    DESTROY_STEPS=$((DESTROY_STEPS + 1))
    print_header "Step $DESTROY_STEPS: Destroying Lambda"
    run_destroy_script "deploy_rag_lambda.sh" "Lambda" || FAILED_STEPS+=("Lambda")
    echo ""
fi

# 2. Knowledge Base
if [ "$SKIP_KB" = false ]; then
    DESTROY_STEPS=$((DESTROY_STEPS + 1))
    print_header "Step $DESTROY_STEPS: Destroying Knowledge Base"
    run_destroy_script "deploy_knowledge_base.sh" "Knowledge Base" || FAILED_STEPS+=("Knowledge Base")
    echo ""
fi

# 3. Database
if [ "$SKIP_DB" = false ]; then
    DESTROY_STEPS=$((DESTROY_STEPS + 1))
    print_header "Step $DESTROY_STEPS: Destroying Database"
    run_destroy_script "deploy_chat_template_db.sh" "Database" || FAILED_STEPS+=("Database")
    echo ""
fi

# 4. S3 Bucket
if [ "$SKIP_S3" = false ]; then
    DESTROY_STEPS=$((DESTROY_STEPS + 1))
    print_header "Step $DESTROY_STEPS: Destroying S3 Bucket"
    run_destroy_script "deploy_s3_bucket.sh" "S3 Bucket" || FAILED_STEPS+=("S3 Bucket")
    echo ""
fi

# 5. Network
if [ "$SKIP_NETWORK" = false ]; then
    DESTROY_STEPS=$((DESTROY_STEPS + 1))
    print_header "Step $DESTROY_STEPS: Destroying Network"
    run_destroy_script "deploy_network.sh" "Network" || FAILED_STEPS+=("Network")
    echo ""
fi

# Summary
print_header "Destroy Summary"
print_status "Environment: $ENVIRONMENT"
print_status "Region: $AWS_REGION"
print_status "Steps run: $DESTROY_STEPS"

if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    print_status "All requested resources have been destroyed."
    print_status "Done."
    exit 0
else
    print_warning "Some steps reported failures or missing stacks:"
    for step in "${FAILED_STEPS[@]}"; do
        print_warning "  - $step"
    done
    print_status "Done (with errors)."
    exit 1
fi
