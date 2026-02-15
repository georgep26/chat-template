#!/bin/bash

# Full Setup Script
# This script runs all one-time setup steps in the correct order based on infra.yaml.
# Setup is intended to be run once (or infrequently) when bootstrapping a new environment.
# For regular application deployments, use deploy_all.sh instead.
#
# Steps:
#   1. Setup AWS accounts       (setup_accounts.sh)
#   2. Setup OIDC provider       (setup_oidc_provider.sh)
#   3. Setup deployer roles      (deploy_deployer_github_action_role.sh)
#   4. Setup GitHub environments (setup_github.sh)
#   5. Setup evals roles         (deploy_evals_github_action_role.sh)
#
# Usage Examples:
#   # Run all setup steps for dev environment
#   ./scripts/setup/setup_all.sh dev
#
#   # Run all steps with auto-confirmation
#   ./scripts/setup/setup_all.sh dev -y
#
#   # Skip account setup (already done) and GitHub setup
#   ./scripts/setup/setup_all.sh dev --skip-accounts --skip-github
#
#   # Use a custom config file
#   ./scripts/setup/setup_all.sh dev --config infra/infra_custom.yaml

set -e

# Get script directory and source utilities (use SETUP_SCRIPT_DIR so sourced utils don't overwrite it)
SETUP_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SETUP_SCRIPT_DIR/../deploy"
source "$SETUP_SCRIPT_DIR/../utils/common.sh"
source "$SETUP_SCRIPT_DIR/../utils/config_parser.sh"
source "$SETUP_SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "Full Setup Script"
    echo ""
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Steps (run in order):"
    echo "  1. Setup AWS accounts        - Creates accounts, CLI roles, budgets"
    echo "  2. Setup OIDC provider        - Creates GitHub OIDC provider in AWS"
    echo "  3. Setup deployer roles       - Creates deployer IAM roles for GitHub Actions"
    echo "  4. Setup GitHub environments  - Creates environments, branch protection, secrets"
    echo "  5. Setup evals roles          - Creates evals IAM roles for GitHub Actions"
    echo ""
    echo "Options:"
    echo "  -y, --yes              Skip all confirmation prompts"
    echo "  --config <path>        Path to infra.yaml (default: infra/infra.yaml)"
    echo "  --skip-accounts        Skip step 1: AWS account setup"
    echo "  --skip-oidc            Skip step 2: OIDC provider setup"
    echo "  --skip-deployer-roles  Skip step 3: deployer role setup"
    echo "  --skip-github          Skip step 4: GitHub environment setup"
    echo "  --skip-evals-roles     Skip step 5: evals role setup"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Note: Steps also respect enabled/disabled flags in infra.yaml for roles."
    echo "      Defaults are loaded from infra/infra.yaml."
    echo ""
    echo "Examples:"
    echo "  $0 dev                                    # Run all steps for dev"
    echo "  $0 dev -y                                 # Run all steps, skip confirmations"
    echo "  $0 dev --skip-accounts                    # Skip account setup (already done)"
    echo "  $0 dev --skip-accounts --skip-github -y   # Skip accounts + GitHub, auto-confirm"
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
SKIP_ACCOUNTS=false
SKIP_OIDC=false
SKIP_DEPLOYER_ROLES=false
SKIP_GITHUB=false
SKIP_EVALS_ROLES=false

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
        --skip-accounts)
            SKIP_ACCOUNTS=true
            shift
            ;;
        --skip-oidc)
            SKIP_OIDC=true
            shift
            ;;
        --skip-deployer-roles)
            SKIP_DEPLOYER_ROLES=true
            shift
            ;;
        --skip-github)
            SKIP_GITHUB=true
            shift
            ;;
        --skip-evals-roles)
            SKIP_EVALS_ROLES=true
            shift
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

print_header "Full Setup"

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

# Get GitHub info from infra.yaml
GITHUB_ORG=$(get_github_org 2>/dev/null || echo "")
GITHUB_REPO=$(get_github_repo 2>/dev/null || echo "")

# =============================================================================
# Step Planning - Determine which steps will run
# =============================================================================

# Each step can be skipped by: (1) skip flag, or (2) disabled in infra.yaml
# Status per step: "run", "skip-flag", "skip-disabled"

# Step 1: Accounts (no infra.yaml enable check - always available unless skipped)
if [ "$SKIP_ACCOUNTS" = true ]; then
    STEP_1_STATUS="skip-flag"
else
    STEP_1_STATUS="run"
fi

# Step 2: OIDC provider
if [ "$SKIP_OIDC" = true ]; then
    STEP_2_STATUS="skip-flag"
elif ! is_role_enabled "oidc_provider"; then
    STEP_2_STATUS="skip-disabled"
else
    STEP_2_STATUS="run"
fi

# Step 3: Deployer roles
if [ "$SKIP_DEPLOYER_ROLES" = true ]; then
    STEP_3_STATUS="skip-flag"
elif ! is_role_enabled "deployer"; then
    STEP_3_STATUS="skip-disabled"
else
    STEP_3_STATUS="run"
fi

# Step 4: GitHub (no infra.yaml enable check - always available unless skipped)
if [ "$SKIP_GITHUB" = true ]; then
    STEP_4_STATUS="skip-flag"
else
    STEP_4_STATUS="run"
fi

# Step 5: Evals roles
if [ "$SKIP_EVALS_ROLES" = true ]; then
    STEP_5_STATUS="skip-flag"
elif ! is_role_enabled "evals"; then
    STEP_5_STATUS="skip-disabled"
else
    STEP_5_STATUS="run"
fi

# Helper to get step name
get_step_name() {
    case "$1" in
        1) echo "Setup AWS accounts" ;;
        2) echo "Setup OIDC provider" ;;
        3) echo "Setup deployer roles" ;;
        4) echo "Setup GitHub environments" ;;
        5) echo "Setup evals roles" ;;
    esac
}

# Helper to get step status
get_step_status() {
    case "$1" in
        1) echo "$STEP_1_STATUS" ;;
        2) echo "$STEP_2_STATUS" ;;
        3) echo "$STEP_3_STATUS" ;;
        4) echo "$STEP_4_STATUS" ;;
        5) echo "$STEP_5_STATUS" ;;
    esac
}

# =============================================================================
# Setup Summary
# =============================================================================

echo ""
print_info "Setup configuration:"
print_info "  Environment:  $ENVIRONMENT"
print_info "  Region:       $AWS_REGION"
print_info "  Config:       ${CONFIG_PATH:-infra/infra.yaml}"
[ -n "$GITHUB_ORG" ] && print_info "  GitHub:       $GITHUB_ORG/$GITHUB_REPO"
echo ""

print_info "Steps:"
for step_num in 1 2 3 4 5; do
    status=$(get_step_status "$step_num")
    name=$(get_step_name "$step_num")
    case "$status" in
        run)
            print_info "  $step_num. $name"
            ;;
        skip-flag)
            print_info "  $step_num. $name (SKIPPED by flag)"
            ;;
        skip-disabled)
            print_info "  $step_num. $name (DISABLED in infra.yaml)"
            ;;
    esac
done
echo ""

# Confirm
if [ "$AUTO_CONFIRM" = false ]; then
    confirm_deployment "Proceed with setup?" || exit 0
fi

# =============================================================================
# Helper Function
# =============================================================================

run_setup_step() {
    local step_num=$1
    local step_name=$2
    local script_path=$3
    shift 3
    local extra_args=("$@")

    echo ""
    print_step "Step $step_num: $step_name"

    if [ ! -f "$script_path" ]; then
        print_warning "Script not found: $script_path, skipping"
        SKIPPED_STEPS+=("$step_name")
        return 0
    fi

    chmod +x "$script_path"

    if "$script_path" "${extra_args[@]}"; then
        print_complete "$step_name completed"
        COMPLETED_STEPS+=("$step_name")
        return 0
    else
        print_error "$step_name failed"
        FAILED_STEPS+=("$step_name")
        return 1
    fi
}

# =============================================================================
# Run Setup Steps
# =============================================================================

COMPLETED_STEPS=()
FAILED_STEPS=()
SKIPPED_STEPS=()
TOTAL_RUN=0

# Build common args
CONFIRM_ARGS=()
[ "$AUTO_CONFIRM" = true ] && CONFIRM_ARGS+=(-y)

# ---- Step 1: Setup AWS accounts ----
if [ "$STEP_1_STATUS" = "run" ]; then
    TOTAL_RUN=$((TOTAL_RUN + 1))
    if ! run_setup_step 1 "Setup AWS accounts" \
        "$SETUP_SCRIPT_DIR/setup_accounts.sh" \
        "${CONFIRM_ARGS[@]}"; then
        print_error "Setup failed at step 1. Stopping."
        exit 1
    fi
else
    SKIPPED_STEPS+=("$(get_step_name 1)")
fi

# ---- Step 2: Setup OIDC provider ----
if [ "$STEP_2_STATUS" = "run" ]; then
    TOTAL_RUN=$((TOTAL_RUN + 1))
    if ! run_setup_step 2 "Setup OIDC provider" \
        "$SETUP_SCRIPT_DIR/setup_oidc_provider.sh" \
        "$ENVIRONMENT" "${CONFIRM_ARGS[@]}"; then
        print_error "Setup failed at step 2. Stopping."
        exit 1
    fi
else
    SKIPPED_STEPS+=("$(get_step_name 2)")
fi

# ---- Step 3: Setup deployer roles ----
if [ "$STEP_3_STATUS" = "run" ]; then
    TOTAL_RUN=$((TOTAL_RUN + 1))
    if ! run_setup_step 3 "Setup deployer roles" \
        "$DEPLOY_DIR/deploy_deployer_github_action_role.sh" \
        "$ENVIRONMENT" deploy "${CONFIRM_ARGS[@]}"; then
        print_error "Setup failed at step 3. Stopping."
        exit 1
    fi
else
    SKIPPED_STEPS+=("$(get_step_name 3)")
fi

# ---- Step 4: Setup GitHub environments ----
if [ "$STEP_4_STATUS" = "run" ]; then
    TOTAL_RUN=$((TOTAL_RUN + 1))
    if ! run_setup_step 4 "Setup GitHub environments" \
        "$SETUP_SCRIPT_DIR/setup_github.sh" \
        "$ENVIRONMENT" "${CONFIRM_ARGS[@]}"; then
        print_error "Setup failed at step 4. Stopping."
        exit 1
    fi
else
    SKIPPED_STEPS+=("$(get_step_name 4)")
fi

# ---- Step 5: Setup evals roles ----
if [ "$STEP_5_STATUS" = "run" ]; then
    TOTAL_RUN=$((TOTAL_RUN + 1))
    if ! run_setup_step 5 "Setup evals roles" \
        "$DEPLOY_DIR/deploy_evals_github_action_role.sh" \
        "$ENVIRONMENT" deploy "${CONFIRM_ARGS[@]}"; then
        print_error "Setup failed at step 5. Stopping."
        exit 1
    fi
else
    SKIPPED_STEPS+=("$(get_step_name 5)")
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
echo ""
draw_box_top 64
draw_box_row_centered "SETUP COMPLETE" 64
draw_box_separator 64
draw_box_row "Environment: $ENVIRONMENT" 64
draw_box_row "Steps completed: ${#COMPLETED_STEPS[@]}" 64
if [ ${#SKIPPED_STEPS[@]} -gt 0 ]; then
    draw_box_row "Steps skipped: ${#SKIPPED_STEPS[@]}" 64
fi
draw_box_bottom 64
echo ""

if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    print_complete "All setup steps completed successfully!"
    echo ""
    print_info "Next steps:"
    print_info "  1. Verify IAM roles and OIDC provider in AWS console"
    print_info "  2. Verify GitHub environments and secrets"
    print_info "  3. Run deploy_all.sh to deploy infrastructure:"
    print_info "     ./scripts/deploy/deploy_all.sh $ENVIRONMENT"
else
    print_error "Some setup steps failed:"
    for step in "${FAILED_STEPS[@]}"; do
        print_error "  - $step"
    done
    exit 1
fi

print_complete "Full setup completed successfully"
