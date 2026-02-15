#!/bin/bash

# Deploy Configs Script
# Uploads all config files from config/<env>/ to the environment's S3 bucket
# under the config/ prefix. Uses the same bucket as app config (s3_bucket resource).
#
# Usage:
#   ./scripts/deploy/deploy_configs.sh <environment>
#
# Examples:
#   ./scripts/deploy/deploy_configs.sh dev
#   ./scripts/deploy/deploy_configs.sh staging
#   ./scripts/deploy/deploy_configs.sh prod

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "Deploy Configs Script"
    echo ""
    echo "Uploads all config files from config/<env>/ to the S3 bucket for that"
    echo "environment under the config/ folder."
    echo ""
    echo "Usage: $0 <environment>"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 prod"
}

# =============================================================================
# Argument Parsing
# =============================================================================

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1

if [ "$ENVIRONMENT" = "-h" ] || [ "$ENVIRONMENT" = "--help" ]; then
    show_usage
    exit 0
fi

# =============================================================================
# Configuration
# =============================================================================

validate_environment "$ENVIRONMENT" || exit 1
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

AWS_REGION=$(get_environment_region "$ENVIRONMENT")
if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
    [ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
fi

PROJECT_ROOT=$(get_project_root)
CONFIG_DIR="$PROJECT_ROOT/config/$ENVIRONMENT"
S3_PREFIX="config/"

# Resolve bucket name: prefer stack output (actual deployed bucket), else infra config
RESOURCE_NAME="s3_bucket"
STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT" 2>/dev/null) || true
BUCKET_NAME=""
if [ -n "$STACK_NAME" ]; then
    if [ -n "$AWS_PROFILE" ]; then
        BUCKET_NAME=$(aws --profile "$AWS_PROFILE" --region "$AWS_REGION" cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
            --output text 2>/dev/null) || true
    else
        BUCKET_NAME=$(aws --region "$AWS_REGION" cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
            --output text 2>/dev/null) || true
    fi
fi
if [ -z "$BUCKET_NAME" ] || [ "$BUCKET_NAME" = "None" ]; then
    BUCKET_NAME=$(get_resource_config "$RESOURCE_NAME" "bucket_name" "$ENVIRONMENT")
fi

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# =============================================================================
# Deploy
# =============================================================================

echo ""
echo -e "\033[0;36m════════════════════════════════════════════════════════════════\033[0m"
echo -e "\033[0;36m  Deploy Configs → S3\033[0m"
echo -e "\033[0;36m════════════════════════════════════════════════════════════════\033[0m"
echo ""

if [ ! -d "$CONFIG_DIR" ]; then
    print_error "Config directory not found: $CONFIG_DIR"
    exit 1
fi

# Check bucket exists and we can list it
if ! aws_cmd s3api head-bucket --bucket "$BUCKET_NAME" >/dev/null 2>&1; then
    print_error "Bucket does not exist or is not accessible: $BUCKET_NAME"
    print_info "Deploy the S3 bucket first: ./scripts/deploy/deploy_s3_bucket.sh $ENVIRONMENT deploy"
    exit 1
fi

print_step "Syncing config/$ENVIRONMENT/ to s3://$BUCKET_NAME/$S3_PREFIX"
aws_cmd s3 sync "$CONFIG_DIR" "s3://$BUCKET_NAME/$S3_PREFIX" --delete
print_complete "Configs deployed to s3://$BUCKET_NAME/$S3_PREFIX"
echo ""
