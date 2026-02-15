#!/bin/bash

# Full Application Deployment Script
# This script orchestrates the deployment of all infrastructure and application components.
# Resources are deployed in the order defined in infra.yaml. Only enabled resources are deployed.
#
# This script is intended for regular deployments (after initial setup via setup_all.sh).
# It supports skip flags to selectively deploy subsets of resources, which is especially
# useful for CI/CD where the deployer role only has permissions for application resources.
#
# Role Strategy:
#   Infrastructure resources (network, S3, database) use the CLI admin role.
#   Application resources (knowledge base, ECR, Lambda) use the deployer role.
#   In GitHub Actions, the deployer role is assumed via OIDC, so infrastructure
#   steps should be skipped (use --only-app or --skip-network --skip-s3 --skip-db).
#
# Usage Examples:
#   # Deploy everything to dev (all enabled resources)
#   ./scripts/deploy/deploy_all.sh dev
#
#   # Deploy only application resources (KB, ECR, Lambda) - typical CI pattern
#   ./scripts/deploy/deploy_all.sh dev --only-app -y
#
#   # Deploy only infrastructure resources (network, S3, DB)
#   ./scripts/deploy/deploy_all.sh dev --only-infra -y
#
#   # Skip specific steps
#   ./scripts/deploy/deploy_all.sh dev --skip-network --skip-cost-tags -y
#
#   # Pass through overrides for sub-scripts (e.g., DB password for CI)
#   ./scripts/deploy/deploy_all.sh dev -y --master-password "$DB_PASS"

set -e

# Get script directory and source utilities (use DEPLOY_SCRIPT_DIR so sourced utils don't overwrite it)
DEPLOY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DEPLOY_SCRIPT_DIR/../utils/common.sh"
source "$DEPLOY_SCRIPT_DIR/../utils/config_parser.sh"
source "$DEPLOY_SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Usage
# =============================================================================

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
    echo "Skip Flags (override infra.yaml enabled status):"
    echo "  --skip-network       Skip network deployment"
    echo "  --skip-s3            Skip S3 bucket deployment"
    echo "  --skip-db            Skip database deployment"
    echo "  --skip-kb            Skip knowledge base deployment"
    echo "  --skip-ecr           Skip ECR repo deployment"
    echo "  --skip-lambda        Skip Lambda deployment"
    echo "  --skip-cost-tags     Skip cost allocation tags activation"
    echo ""
    echo "Convenience Flags:"
    echo "  --only-infra         Only deploy infrastructure (network, S3, DB)"
    echo "  --only-app           Only deploy application (KB, ECR, Lambda)"
    echo ""
    echo "General Options:"
    echo "  -y, --yes            Skip all confirmation prompts"
    echo "  --config <path>      Path to infra.yaml (default: infra/infra.yaml)"
    echo "  -h, --help           Show this help message"
    echo ""
    echo "Pass-Through Options (forwarded to sub-scripts, override infra.yaml):"
    echo "  --region <region>                  AWS region"
    echo "  --vpc-id <id>                      VPC ID (for DB, Lambda)"
    echo "  --subnet-ids <id1,id2,...>         Subnet IDs (for DB, Lambda)"
    echo "  --security-group-ids <id1,...>     Security group IDs (for Lambda)"
    echo "  --master-username <user>           DB master username"
    echo "  --master-password <pass>           DB master password"
    echo "  --public-ip <ip>                   Public IP for DB access"
    echo "  --s3-app-config-uri <uri>          S3 URI for Lambda app config"
    echo "  --local-app-config-path <path>     Local app config to upload to S3"
    echo "  --image-tag <tag>                  Docker image tag for Lambda"
    echo ""
    echo "Note: Resources are only deployed if enabled in infra.yaml AND not skipped."
    echo "      Defaults are loaded from infra/infra.yaml."
    echo ""
    echo "Examples:"
    echo "  $0 dev                              # Deploy all enabled resources"
    echo "  $0 dev -y                           # Deploy all, skip confirmations"
    echo "  $0 dev --only-app -y                # Deploy only app resources (CI pattern)"
    echo "  $0 dev --skip-network --skip-db -y  # Skip network and DB"
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
shift

# Default values
AUTO_CONFIRM=false
CONFIG_PATH=""

# Skip flags
SKIP_NETWORK=false
SKIP_S3=false
SKIP_DB=false
SKIP_KB=false
SKIP_ECR=false
SKIP_LAMBDA=false
SKIP_COST_TAGS=false

# Pass-through overrides
REGION_OVERRIDE=""
VPC_ID_OVERRIDE=""
SUBNET_IDS_OVERRIDE=""
SECURITY_GROUP_IDS_OVERRIDE=""
MASTER_USERNAME_OVERRIDE=""
MASTER_PASSWORD_OVERRIDE=""
PUBLIC_IP_OVERRIDE=""
S3_APP_CONFIG_URI_OVERRIDE=""
LOCAL_APP_CONFIG_PATH_OVERRIDE=""
IMAGE_TAG_OVERRIDE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --config)
            CONFIG_PATH="$2"
            shift 2
            ;;
        # Skip flags
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
        --skip-ecr)
            SKIP_ECR=true
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
        # Convenience flags
        --only-infra)
            SKIP_KB=true
            SKIP_ECR=true
            SKIP_LAMBDA=true
            SKIP_COST_TAGS=true
            shift
            ;;
        --only-app)
            SKIP_NETWORK=true
            SKIP_S3=true
            SKIP_DB=true
            SKIP_COST_TAGS=true
            shift
            ;;
        # Pass-through overrides
        --region)
            REGION_OVERRIDE="$2"
            shift 2
            ;;
        --vpc-id)
            VPC_ID_OVERRIDE="$2"
            shift 2
            ;;
        --subnet-ids)
            SUBNET_IDS_OVERRIDE="$2"
            shift 2
            ;;
        --security-group-ids)
            SECURITY_GROUP_IDS_OVERRIDE="$2"
            shift 2
            ;;
        --master-username)
            MASTER_USERNAME_OVERRIDE="$2"
            shift 2
            ;;
        --master-password)
            MASTER_PASSWORD_OVERRIDE="$2"
            shift 2
            ;;
        --public-ip)
            PUBLIC_IP_OVERRIDE="$2"
            shift 2
            ;;
        --s3-app-config-uri)
            S3_APP_CONFIG_URI_OVERRIDE="$2"
            shift 2
            ;;
        --local-app-config-path|--local_app_config_path)
            LOCAL_APP_CONFIG_PATH_OVERRIDE="$2"
            shift 2
            ;;
        --image-tag)
            IMAGE_TAG_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# =============================================================================
# Configuration Loading
# =============================================================================

print_header "Full Application Deployment"

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration
print_step "Loading configuration for $ENVIRONMENT environment"
if [ -n "$CONFIG_PATH" ]; then
    load_infra_config "$CONFIG_PATH" || exit 1
else
    load_infra_config || exit 1
fi
validate_config "$ENVIRONMENT" || exit 1

# Get values from config
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

# =============================================================================
# Resource Lookup Functions (bash 3.x compatible -- no associative arrays)
# =============================================================================

# Deployment order (matches infra.yaml resource order)
DEPLOY_ORDER="network s3_bucket chat_db rag_knowledge_base rag_lambda_ecr rag_lambda"

get_resource_script() {
    case "$1" in
        network)            echo "deploy_network.sh" ;;
        s3_bucket)          echo "deploy_s3_bucket.sh" ;;
        chat_db)            echo "deploy_chat_template_db.sh" ;;
        rag_knowledge_base) echo "deploy_knowledge_base.sh" ;;
        rag_lambda_ecr)     echo "deploy_ecr_repo.sh" ;;
        rag_lambda)         echo "deploy_rag_lambda.sh" ;;
    esac
}

get_resource_display_name() {
    case "$1" in
        network)            echo "Network (VPC)" ;;
        s3_bucket)          echo "S3 Bucket" ;;
        chat_db)            echo "Database" ;;
        rag_knowledge_base) echo "Knowledge Base" ;;
        rag_lambda_ecr)     echo "ECR Repository" ;;
        rag_lambda)         echo "RAG Lambda" ;;
    esac
}

get_resource_category() {
    case "$1" in
        network|s3_bucket|chat_db) echo "infra" ;;
        rag_knowledge_base|rag_lambda_ecr|rag_lambda) echo "app" ;;
    esac
}

is_resource_skipped() {
    case "$1" in
        network)            [ "$SKIP_NETWORK" = true ] ;;
        s3_bucket)          [ "$SKIP_S3" = true ] ;;
        chat_db)            [ "$SKIP_DB" = true ] ;;
        rag_knowledge_base) [ "$SKIP_KB" = true ] ;;
        rag_lambda_ecr)     [ "$SKIP_ECR" = true ] ;;
        rag_lambda)         [ "$SKIP_LAMBDA" = true ] ;;
        *)                  return 1 ;;
    esac
}

# Determine resource status: "deploy", "skip-flag", or "skip-disabled"
get_resource_status() {
    local resource=$1
    if is_resource_skipped "$resource"; then
        echo "skip-flag"
    elif ! is_resource_enabled "$resource"; then
        echo "skip-disabled"
    else
        echo "deploy"
    fi
}

# =============================================================================
# Deploy Summary
# =============================================================================

echo ""
WIDTH=64
draw_box_top $WIDTH
draw_box_row_centered "DEPLOYMENT PLAN" $WIDTH
draw_box_separator $WIDTH
draw_box_row "Project:      $PROJECT_NAME" $WIDTH
draw_box_row "Environment:  $ENVIRONMENT" $WIDTH
draw_box_row "Region:       $AWS_REGION" $WIDTH
[ -n "$AWS_PROFILE" ] && draw_box_row "Profile:      $AWS_PROFILE" $WIDTH
draw_box_separator $WIDTH
draw_box_row "Infrastructure (CLI admin role):" $WIDTH
for resource in network s3_bucket chat_db; do
    status=$(get_resource_status "$resource")
    name=$(get_resource_display_name "$resource")
    case "$status" in
        deploy)        draw_box_row "  [DEPLOY]   $name" $WIDTH ;;
        skip-flag)     draw_box_row "  [SKIP]     $name (skipped by flag)" $WIDTH ;;
        skip-disabled) draw_box_row "  [DISABLED] $name (disabled in config)" $WIDTH ;;
    esac
done
draw_box_row "" $WIDTH
draw_box_row "Application (deployer role):" $WIDTH
for resource in rag_knowledge_base rag_lambda_ecr rag_lambda; do
    status=$(get_resource_status "$resource")
    name=$(get_resource_display_name "$resource")
    case "$status" in
        deploy)        draw_box_row "  [DEPLOY]   $name" $WIDTH ;;
        skip-flag)     draw_box_row "  [SKIP]     $name (skipped by flag)" $WIDTH ;;
        skip-disabled) draw_box_row "  [DISABLED] $name (disabled in config)" $WIDTH ;;
    esac
done
if [ "$SKIP_COST_TAGS" = false ]; then
    draw_box_row "" $WIDTH
    draw_box_row "  [DEPLOY]   Cost Allocation Tags" $WIDTH
fi
draw_box_bottom $WIDTH
echo ""

# Show pass-through overrides if any
HAS_OVERRIDES=false
[ -n "$REGION_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$VPC_ID_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$SUBNET_IDS_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$SECURITY_GROUP_IDS_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$MASTER_USERNAME_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$MASTER_PASSWORD_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$PUBLIC_IP_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$S3_APP_CONFIG_URI_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$LOCAL_APP_CONFIG_PATH_OVERRIDE" ] && HAS_OVERRIDES=true
[ -n "$IMAGE_TAG_OVERRIDE" ] && HAS_OVERRIDES=true

if [ "$HAS_OVERRIDES" = true ]; then
    print_info "Override parameters:"
    [ -n "$REGION_OVERRIDE" ] && print_info "  Region: $REGION_OVERRIDE"
    [ -n "$VPC_ID_OVERRIDE" ] && print_info "  VPC ID: $VPC_ID_OVERRIDE"
    [ -n "$SUBNET_IDS_OVERRIDE" ] && print_info "  Subnet IDs: $SUBNET_IDS_OVERRIDE"
    [ -n "$SECURITY_GROUP_IDS_OVERRIDE" ] && print_info "  Security Group IDs: $SECURITY_GROUP_IDS_OVERRIDE"
    [ -n "$MASTER_USERNAME_OVERRIDE" ] && print_info "  Master Username: $MASTER_USERNAME_OVERRIDE"
    [ -n "$MASTER_PASSWORD_OVERRIDE" ] && print_info "  Master Password: ********"
    [ -n "$PUBLIC_IP_OVERRIDE" ] && print_info "  Public IP: $PUBLIC_IP_OVERRIDE"
    [ -n "$S3_APP_CONFIG_URI_OVERRIDE" ] && print_info "  S3 App Config URI: $S3_APP_CONFIG_URI_OVERRIDE"
    [ -n "$LOCAL_APP_CONFIG_PATH_OVERRIDE" ] && print_info "  Local App Config: $LOCAL_APP_CONFIG_PATH_OVERRIDE"
    [ -n "$IMAGE_TAG_OVERRIDE" ] && print_info "  Image Tag: $IMAGE_TAG_OVERRIDE"
    echo ""
fi

# Confirm deployment
if [ "$AUTO_CONFIRM" = false ]; then
    confirm_deployment "Do you want to deploy the above components?" || exit 0
fi

# =============================================================================
# Build Sub-Script Arguments
# =============================================================================

# Build argument arrays for each sub-script type.
# Each sub-script receives: <environment> deploy -y [overrides...]
# IMPORTANT: Use if/then/fi (not [ -n ] && ...) to avoid set -e killing the script
# when the test is false (returns 1) as the last command in the function.

build_common_args() {
    BUILT_ARGS=("$ENVIRONMENT" "deploy" "-y")
    if [ -n "$REGION_OVERRIDE" ]; then BUILT_ARGS+=(--region "$REGION_OVERRIDE"); fi
}

build_network_args() {
    BUILT_ARGS=("$ENVIRONMENT" "deploy" "-y")
    if [ -n "$REGION_OVERRIDE" ]; then BUILT_ARGS+=(--region "$REGION_OVERRIDE"); fi
}

build_db_args() {
    BUILT_ARGS=("$ENVIRONMENT" "deploy" "-y")
    if [ -n "$REGION_OVERRIDE" ]; then BUILT_ARGS+=(--region "$REGION_OVERRIDE"); fi
    if [ -n "$VPC_ID_OVERRIDE" ]; then BUILT_ARGS+=(--vpc-id "$VPC_ID_OVERRIDE"); fi
    if [ -n "$SUBNET_IDS_OVERRIDE" ]; then BUILT_ARGS+=(--subnet-ids "$SUBNET_IDS_OVERRIDE"); fi
    if [ -n "$MASTER_USERNAME_OVERRIDE" ]; then BUILT_ARGS+=(--master-username "$MASTER_USERNAME_OVERRIDE"); fi
    if [ -n "$MASTER_PASSWORD_OVERRIDE" ]; then BUILT_ARGS+=(--master-password "$MASTER_PASSWORD_OVERRIDE"); fi
    if [ -n "$PUBLIC_IP_OVERRIDE" ]; then BUILT_ARGS+=(--public-ip "$PUBLIC_IP_OVERRIDE"); fi
}

build_lambda_args() {
    BUILT_ARGS=("$ENVIRONMENT" "deploy" "-y")
    if [ -n "$REGION_OVERRIDE" ]; then BUILT_ARGS+=(--region "$REGION_OVERRIDE"); fi
    if [ -n "$VPC_ID_OVERRIDE" ]; then BUILT_ARGS+=(--vpc-id "$VPC_ID_OVERRIDE"); fi
    if [ -n "$SUBNET_IDS_OVERRIDE" ]; then BUILT_ARGS+=(--subnet-ids "$SUBNET_IDS_OVERRIDE"); fi
    if [ -n "$SECURITY_GROUP_IDS_OVERRIDE" ]; then BUILT_ARGS+=(--security-group-ids "$SECURITY_GROUP_IDS_OVERRIDE"); fi
    if [ -n "$S3_APP_CONFIG_URI_OVERRIDE" ]; then BUILT_ARGS+=(--s3_app_config_uri "$S3_APP_CONFIG_URI_OVERRIDE"); fi
    if [ -n "$LOCAL_APP_CONFIG_PATH_OVERRIDE" ]; then BUILT_ARGS+=(--local_app_config_path "$LOCAL_APP_CONFIG_PATH_OVERRIDE"); fi
    if [ -n "$IMAGE_TAG_OVERRIDE" ]; then BUILT_ARGS+=(--image-tag "$IMAGE_TAG_OVERRIDE"); fi
}

build_kb_args() {
    BUILT_ARGS=("$ENVIRONMENT" "deploy" "-y")
    if [ -n "$REGION_OVERRIDE" ]; then BUILT_ARGS+=(--region "$REGION_OVERRIDE"); fi
}

# Map resource name to the arg builder function
build_args_for_resource() {
    local resource=$1
    case "$resource" in
        network)            build_network_args ;;
        s3_bucket)          build_common_args ;;
        chat_db)            build_db_args ;;
        rag_knowledge_base) build_kb_args ;;
        rag_lambda_ecr)     build_common_args ;;
        rag_lambda)         build_lambda_args ;;
    esac
}

# =============================================================================
# Deployment Functions
# =============================================================================

run_deployment_script() {
    local script_name=$1
    local resource_name=$2
    shift 2
    local args=("$@")
    local script_path="$DEPLOY_SCRIPT_DIR/$script_name"
    local exit_code

    print_step "Deploying: $resource_name"

    if [ ! -f "$script_path" ]; then
        print_warning "Script not found: $script_path, skipping"
        return 0
    fi

    chmod +x "$script_path"

    # Run sub-script (disable set -e so we can capture exit code and print help on failure)
    set +e
    "$script_path" "${args[@]}"
    exit_code=$?
    set -e

    if [ $exit_code -eq 0 ]; then
        print_complete "$resource_name deployment completed"
        return 0
    else
        print_error "$resource_name deployment failed (exit code $exit_code)"
        print_info "To see full output, run from project root: $script_path ${args[*]}"
        return 1
    fi
}

# =============================================================================
# Main Deployment
# =============================================================================

DEPLOYMENT_STEPS=0
COMPLETED_STEPS=()
FAILED_STEPS=()
SKIPPED_STEPS=()

for resource in $DEPLOY_ORDER; do
    status=$(get_resource_status "$resource")
    script=$(get_resource_script "$resource")
    display_name=$(get_resource_display_name "$resource")

    if [ "$status" != "deploy" ]; then
        SKIPPED_STEPS+=("$display_name")
        continue
    fi

    DEPLOYMENT_STEPS=$((DEPLOYMENT_STEPS + 1))

    echo ""
    print_step "Step $DEPLOYMENT_STEPS: Deploying $display_name"

    # Build args for this resource
    build_args_for_resource "$resource"

    if ! run_deployment_script "$script" "$display_name" "${BUILT_ARGS[@]}"; then
        FAILED_STEPS+=("$display_name")
        print_error "Deployment failed at $display_name. Stopping."
        exit 1
    fi

    COMPLETED_STEPS+=("$display_name")
done

# Deploy cost allocation tags
if [ "$SKIP_COST_TAGS" = false ]; then
    echo ""
    DEPLOYMENT_STEPS=$((DEPLOYMENT_STEPS + 1))
    print_step "Step $DEPLOYMENT_STEPS: Activating Cost Allocation Tags"

    if [ -f "$DEPLOY_SCRIPT_DIR/deploy_cost_analysis_tags.sh" ]; then
        chmod +x "$DEPLOY_SCRIPT_DIR/deploy_cost_analysis_tags.sh"

        if "$DEPLOY_SCRIPT_DIR/deploy_cost_analysis_tags.sh" activate; then
            print_complete "Cost allocation tags activation completed"
            COMPLETED_STEPS+=("Cost Allocation Tags")
        else
            print_warning "Cost allocation tags activation failed or tags not found yet"
        fi
    else
        print_warning "Cost tags script not found, skipping activation"
    fi
else
    SKIPPED_STEPS+=("Cost Allocation Tags")
fi

# =============================================================================
# Sync App Config and Deploy Configs
# =============================================================================

echo ""
DEPLOYMENT_STEPS=$((DEPLOYMENT_STEPS + 1))
print_step "Step $DEPLOYMENT_STEPS: Syncing App Config"

if [ -f "$DEPLOY_SCRIPT_DIR/sync_app_config.sh" ]; then
    chmod +x "$DEPLOY_SCRIPT_DIR/sync_app_config.sh"
    if "$DEPLOY_SCRIPT_DIR/sync_app_config.sh" --env "$ENVIRONMENT"; then
        print_complete "App config sync completed"
        COMPLETED_STEPS+=("Sync App Config")
    else
        print_error "App config sync failed"
        FAILED_STEPS+=("Sync App Config")
        exit 1
    fi
else
    print_warning "sync_app_config.sh not found, skipping"
    SKIPPED_STEPS+=("Sync App Config")
fi

echo ""
DEPLOYMENT_STEPS=$((DEPLOYMENT_STEPS + 1))
print_step "Step $DEPLOYMENT_STEPS: Deploying Configs to S3"

if [ -f "$DEPLOY_SCRIPT_DIR/deploy_configs.sh" ]; then
    chmod +x "$DEPLOY_SCRIPT_DIR/deploy_configs.sh"
    if "$DEPLOY_SCRIPT_DIR/deploy_configs.sh" "$ENVIRONMENT"; then
        print_complete "Configs deployed to S3"
        COMPLETED_STEPS+=("Deploy Configs")
    else
        print_error "Deploy configs failed"
        FAILED_STEPS+=("Deploy Configs")
        exit 1
    fi
else
    print_warning "deploy_configs.sh not found, skipping"
    SKIPPED_STEPS+=("Deploy Configs")
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
echo ""
draw_box_top 64
draw_box_row_centered "DEPLOYMENT COMPLETE" 64
draw_box_separator 64
draw_box_row "Environment: $ENVIRONMENT" 64
draw_box_row "Region: $AWS_REGION" 64
draw_box_row "Steps completed: ${#COMPLETED_STEPS[@]}" 64
if [ ${#SKIPPED_STEPS[@]} -gt 0 ]; then
    draw_box_row "Steps skipped: ${#SKIPPED_STEPS[@]}" 64
fi
draw_box_bottom 64
echo ""

if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    print_complete "All deployment steps completed successfully!"
    echo ""
    if [ ${#COMPLETED_STEPS[@]} -gt 0 ]; then
        print_info "Deployed:"
        for step in "${COMPLETED_STEPS[@]}"; do
            print_info "  - $step"
        done
    fi
    if [ ${#SKIPPED_STEPS[@]} -gt 0 ]; then
        echo ""
        print_info "Skipped:"
        for step in "${SKIPPED_STEPS[@]}"; do
            print_info "  - $step"
        done
    fi
else
    print_error "Some deployment steps failed:"
    for step in "${FAILED_STEPS[@]}"; do
        print_error "  - $step"
    done
    exit 1
fi

echo ""
print_complete "Full application deployment completed successfully"
