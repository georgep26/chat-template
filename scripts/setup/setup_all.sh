#!/bin/bash

# Full Setup Script
# This script runs all setup scripts in the correct order based on infra.yaml
# Only enabled roles are set up
#
# Usage Examples:
#   # Set up all roles for dev environment
#   ./scripts/setup/setup_all.sh dev
#
#   # Set up with auto-confirmation
#   ./scripts/setup/setup_all.sh dev -y

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

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
    echo "Options:"
    echo "  -y, --yes   - Skip all confirmation prompts"
    echo ""
    echo "Note: Only enabled roles in infra.yaml will be set up"
    echo "      GitHub org/repo are read from secrets file"
    echo ""
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 staging -y"
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

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
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
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Get values from config
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

# Get GitHub info from secrets
GITHUB_ORG=$(get_secret_value "$ENVIRONMENT" "github.org" 2>/dev/null || echo "")
GITHUB_REPO=$(get_secret_value "$ENVIRONMENT" "github.repo" 2>/dev/null || echo "")

# Check if GitHub info is needed
NEEDS_GITHUB=false
if is_role_enabled "deployer" || is_role_enabled "evals"; then
    NEEDS_GITHUB=true
fi

if [ "$NEEDS_GITHUB" = true ] && ([ -z "$GITHUB_ORG" ] || [ -z "$GITHUB_REPO" ]); then
    print_error "GitHub org and repo are required for deployer/evals roles"
    print_info "Add them to infra/secrets/${ENVIRONMENT}_secrets.yaml:"
    print_info "  github:"
    print_info "    org: your-org"
    print_info "    repo: your-repo"
    exit 1
fi

# =============================================================================
# Setup Summary
# =============================================================================

echo ""
print_info "Setup configuration:"
print_info "  Environment: $ENVIRONMENT"
print_info "  Region: $AWS_REGION"
[ -n "$GITHUB_ORG" ] && print_info "  GitHub Org: $GITHUB_ORG"
[ -n "$GITHUB_REPO" ] && print_info "  GitHub Repo: $GITHUB_REPO"
echo ""

print_info "Roles to set up (based on infra.yaml):"
ROLES=$(get_enabled_roles)
while IFS= read -r role; do
    [ -z "$role" ] && continue
    print_info "  - $role"
done <<< "$ROLES"
echo ""

# Confirm
if [ "$AUTO_CONFIRM" = false ]; then
    confirm_deployment "Proceed with setup?" || exit 0
fi

# =============================================================================
# Role to Script Mapping
# =============================================================================

get_setup_script_for_role() {
    local role=$1
    case "$role" in
        oidc_provider)
            echo "setup_oidc_provider.sh"
            ;;
        deployer)
            echo "setup_deployer_roles.sh"
            ;;
        evals)
            echo "setup_evals_roles.sh"
            ;;
        cli)
            echo "setup_cli_role.sh"
            ;;
        rag_lambda_execution)
            # This is deployed as part of the resource deployment, not setup
            echo ""
            ;;
        *)
            echo ""
            ;;
    esac
}

# =============================================================================
# Run Setup Scripts
# =============================================================================

run_setup_script() {
    local script_name=$1
    local role_name=$2
    local extra_args=("${@:3}")
    
    print_step "Setting up: $role_name"
    
    if [ ! -f "$SCRIPT_DIR/$script_name" ]; then
        print_warning "Script not found: $SCRIPT_DIR/$script_name, skipping"
        return 0
    fi
    
    chmod +x "$SCRIPT_DIR/$script_name"
    
    if "$SCRIPT_DIR/$script_name" "$ENVIRONMENT" -y "${extra_args[@]}"; then
        print_complete "$role_name setup completed"
        return 0
    else
        print_error "$role_name setup failed"
        return 1
    fi
}

SETUP_STEPS=0
FAILED_STEPS=()
SKIPPED_STEPS=()

# Process roles in order
while IFS= read -r role; do
    [ -z "$role" ] && continue
    
    SETUP_STEPS=$((SETUP_STEPS + 1))
    
    # Get the setup script for this role
    script=$(get_setup_script_for_role "$role")
    
    if [ -z "$script" ]; then
        print_info "Role $role does not require separate setup, skipping"
        SKIPPED_STEPS+=("$role")
        continue
    fi
    
    echo ""
    print_step "Step $SETUP_STEPS: Setting up $role"
    
    # Build extra args based on role type
    extra_args=()
    case "$role" in
        deployer|evals)
            extra_args=(--github-org "$GITHUB_ORG" --github-repo "$GITHUB_REPO")
            ;;
    esac
    
    if ! run_setup_script "$script" "$role" "${extra_args[@]}"; then
        FAILED_STEPS+=("$role")
        print_error "Setup failed at $role. Stopping."
        exit 1
    fi
done <<< "$ROLES"

# =============================================================================
# Summary
# =============================================================================

echo ""
echo ""
draw_box_top 64
draw_box_row_centered "SETUP COMPLETE" 64
draw_box_separator 64
draw_box_row "Environment: $ENVIRONMENT" 64
draw_box_row "Steps completed: $SETUP_STEPS" 64
if [ ${#SKIPPED_STEPS[@]} -gt 0 ]; then
    draw_box_row "Steps skipped: ${#SKIPPED_STEPS[@]}" 64
fi
draw_box_bottom 64
echo ""

if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    print_complete "All setup steps completed successfully!"
    echo ""
    print_info "Next steps:"
    print_info "  1. Add role ARNs to GitHub repository secrets"
    print_info "  2. Configure AWS CLI profiles for local development"
    print_info "  3. Run deploy_all.sh to deploy infrastructure"
else
    print_error "Some setup steps failed:"
    for step in "${FAILED_STEPS[@]}"; do
        print_error "  - $step"
    done
    exit 1
fi

print_complete "Full setup completed successfully"
