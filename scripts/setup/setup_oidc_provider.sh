#!/bin/bash

# GitHub OIDC Provider Setup Script
# This script creates the GitHub OIDC identity provider in AWS for GitHub Actions authentication.
# It uses the CLI role profile for the target environment (e.g. chat-template-dev-cli) so the
# provider is created in that account. All configuration is read from infra.yaml.
#
# Usage Examples:
#   # Set up OIDC provider in dev account
#   ./scripts/setup/setup_oidc_provider.sh dev
#
#   # Set up with auto-confirmation
#   ./scripts/setup/setup_oidc_provider.sh dev -y
#
#   # Check if provider exists
#   ./scripts/setup/setup_oidc_provider.sh dev status

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Constants
# =============================================================================

GITHUB_OIDC_URL="https://token.actions.githubusercontent.com"
GITHUB_OIDC_THUMBPRINT="6938fd4d98bab03faadb97b34396831e3780aea1"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "GitHub OIDC Provider Setup Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Actions:"
    echo "  create    - Create the OIDC provider (default)"
    echo "  delete    - Delete the OIDC provider"
    echo "  status    - Check if provider exists"
    echo ""
    echo "Options:"
    echo "  -y, --yes   - Skip confirmation prompt"
    echo ""
    echo "Note: Uses CLI role profile per environment (infra.yaml cli_profile_name). Config from infra/infra.yaml"
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
ACTION="create"
AUTO_CONFIRM=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        create|delete|status)
            ACTION="$1"
            shift
            ;;
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

print_header "GitHub OIDC Provider Setup"

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration
print_step "Loading configuration for $ENVIRONMENT environment"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Get values from config (use CLI role profile to create OIDC provider in this account)
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")

# =============================================================================
# AWS CLI Helper
# =============================================================================

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# =============================================================================
# OIDC Provider Functions
# =============================================================================

get_provider_arn() {
    aws_cmd iam list-open-id-connect-providers \
        --query "OpenIDConnectProviderList[?ends_with(Arn, 'token.actions.githubusercontent.com')].Arn" \
        --output text 2>/dev/null
}

check_provider_exists() {
    local arn=$(get_provider_arn)
    [ -n "$arn" ] && [ "$arn" != "None" ]
}

show_status() {
    print_step "Checking OIDC provider status"
    
    local arn=$(get_provider_arn)
    
    if [ -n "$arn" ] && [ "$arn" != "None" ]; then
        print_complete "GitHub OIDC provider exists"
        print_info "Provider ARN: $arn"
        
        # Get provider details
        aws_cmd iam get-open-id-connect-provider --open-id-connect-provider-arn "$arn" \
            --query '{Url:Url,ClientIds:ClientIDList,Thumbprints:ThumbprintList}' 2>/dev/null || true
    else
        print_warning "GitHub OIDC provider does not exist"
        print_info "Run '$0 $ENVIRONMENT create' to create it"
    fi
}

create_provider() {
    print_step "Creating GitHub OIDC provider"
    
    # Check if already exists
    if check_provider_exists; then
        print_warning "GitHub OIDC provider already exists"
        show_status
        return 0
    fi
    
    # Show summary
    echo ""
    print_info "This will create a GitHub OIDC identity provider with:"
    print_info "  URL: $GITHUB_OIDC_URL"
    print_info "  Client ID: sts.amazonaws.com"
    print_info "  Account: $ACCOUNT_ID"
    echo ""
    
    # Confirm
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Create OIDC provider?" || exit 0
    fi
    
    # Create the provider
    local arn=$(aws_cmd iam create-open-id-connect-provider \
        --url "$GITHUB_OIDC_URL" \
        --client-id-list "sts.amazonaws.com" \
        --thumbprint-list "$GITHUB_OIDC_THUMBPRINT" \
        --query 'OpenIDConnectProviderArn' \
        --output text 2>&1)
    
    if [ $? -eq 0 ]; then
        print_complete "GitHub OIDC provider created successfully"
        print_info "Provider ARN: $arn"
        echo ""
        print_info "Next steps:"
        print_info "  1. Run deploy_deployer_github_action_role.sh to create deployer roles (e.g. ./scripts/deploy/deploy_deployer_github_action_role.sh dev deploy)"
        print_info "  2. Configure GitHub Actions to use the role"
    else
        print_error "Failed to create OIDC provider: $arn"
        exit 1
    fi
}

delete_provider() {
    print_step "Deleting GitHub OIDC provider"
    
    local arn=$(get_provider_arn)
    
    if [ -z "$arn" ] || [ "$arn" = "None" ]; then
        print_warning "GitHub OIDC provider does not exist"
        return 0
    fi
    
    # Show warning
    echo ""
    print_warning "This will delete the GitHub OIDC provider"
    print_warning "Provider ARN: $arn"
    print_warning "WARNING: This will break GitHub Actions authentication!"
    echo ""
    
    # Confirm
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete OIDC provider" || exit 0
    fi
    
    # Delete the provider
    if aws_cmd iam delete-open-id-connect-provider --open-id-connect-provider-arn "$arn"; then
        print_complete "GitHub OIDC provider deleted successfully"
    else
        print_error "Failed to delete OIDC provider"
        exit 1
    fi
}

# =============================================================================
# Main Execution
# =============================================================================

case $ACTION in
    create)
        create_provider
        ;;
    delete)
        delete_provider
        ;;
    status)
        show_status
        ;;
    *)
        print_error "Invalid action: $ACTION"
        show_usage
        exit 1
        ;;
esac

print_complete "OIDC provider operation completed"
