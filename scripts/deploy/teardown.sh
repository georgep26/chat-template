#!/bin/bash

# Environment Teardown Script
# This script tears down all resources for an environment in reverse dependency order
# Resources are deleted in reverse order of how they are defined in infra.yaml
# If a resource is disabled but its stack exists, it will still be deleted
#
# Usage Examples:
#   # Teardown dev environment
#   ./scripts/deploy/teardown.sh dev
#
#   # Teardown with auto-confirmation (DANGEROUS!)
#   ./scripts/deploy/teardown.sh dev -y
#
#   # Dry run to see what would be deleted
#   ./scripts/deploy/teardown.sh dev --dry-run

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
    echo "Environment Teardown Script"
    echo ""
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Options:"
    echo "  -y, --yes     - Skip all confirmation prompts (DANGEROUS!)"
    echo "  --dry-run     - Show what would be deleted without deleting"
    echo ""
    echo "Note: Resources are deleted in reverse order of infra.yaml"
    echo "      If a stack exists, it will be deleted even if disabled in config"
    echo ""
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 staging --dry-run"
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
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
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

print_header "Environment Teardown"

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

ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

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
# Stack Functions
# =============================================================================

check_stack_exists() {
    local stack_name=$1
    aws_cmd cloudformation describe-stacks --stack-name "$stack_name" >/dev/null 2>&1
}

delete_stack() {
    local stack_name=$1
    local resource_name=$2
    
    if ! check_stack_exists "$stack_name"; then
        print_info "Stack $stack_name does not exist, skipping"
        return 0
    fi
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would delete stack: $stack_name"
        return 0
    fi
    
    print_step "Deleting stack: $stack_name"
    
    if aws_cmd cloudformation delete-stack --stack-name "$stack_name"; then
        print_info "Deletion initiated for $stack_name"
        print_info "Waiting for deletion to complete..."
        
        if aws_cmd cloudformation wait stack-delete-complete --stack-name "$stack_name" 2>/dev/null; then
            print_complete "$resource_name deleted successfully"
        else
            # Check if it's actually deleted or failed
            if check_stack_exists "$stack_name"; then
                local status=$(aws_cmd cloudformation describe-stacks \
                    --stack-name "$stack_name" \
                    --query 'Stacks[0].StackStatus' \
                    --output text 2>/dev/null)
                print_error "$resource_name deletion failed with status: $status"
                return 1
            else
                print_complete "$resource_name deleted successfully"
            fi
        fi
    else
        print_error "Failed to initiate deletion for $stack_name"
        return 1
    fi
}

# =============================================================================
# Scan for Existing Stacks
# =============================================================================

STACKS_TO_DELETE=()

print_step "Scanning for existing stacks..."

# Get all resources (not just enabled ones) and check if their stacks exist
RESOURCES=$(get_all_resources | tac)  # Reverse order for teardown

while IFS= read -r resource; do
    [ -z "$resource" ] && continue
    
    stack_name=$(get_resource_stack_name "$resource" "$ENVIRONMENT")
    if [ -n "$stack_name" ] && check_stack_exists "$stack_name"; then
        STACKS_TO_DELETE+=("$resource:$stack_name")
    fi
    
    # Check for secondary stacks (like secret_stack_name)
    secret_stack=$(get_resource_stack_name "$resource" "$ENVIRONMENT" "secret_stack_name" 2>/dev/null)
    if [ -n "$secret_stack" ] && [ "$secret_stack" != "null" ] && check_stack_exists "$secret_stack"; then
        STACKS_TO_DELETE+=("${resource}_secret:$secret_stack")
    fi
done <<< "$RESOURCES"

# =============================================================================
# Teardown Summary
# =============================================================================

if [ ${#STACKS_TO_DELETE[@]} -eq 0 ]; then
    print_info "No stacks found to delete for environment: $ENVIRONMENT"
    exit 0
fi

echo ""
print_warning "The following stacks will be PERMANENTLY DELETED:"
echo ""

for item in "${STACKS_TO_DELETE[@]}"; do
    resource="${item%%:*}"
    stack="${item##*:}"
    print_warning "  - $resource ($stack)"
done

echo ""

# =============================================================================
# Confirmation
# =============================================================================

if [ "$DRY_RUN" = true ]; then
    print_info "DRY RUN MODE - No resources will be deleted"
    echo ""
else
    if [ "$AUTO_CONFIRM" = false ]; then
        echo ""
        print_warning "This action is IRREVERSIBLE!"
        print_warning "All data in these resources will be PERMANENTLY LOST!"
        echo ""
        confirm_destructive_action "$ENVIRONMENT" "teardown" || exit 0
    else
        print_warning "Auto-confirm enabled - proceeding with teardown"
    fi
fi

# =============================================================================
# Teardown Execution
# =============================================================================

TEARDOWN_STEPS=0
FAILED_STEPS=()

for item in "${STACKS_TO_DELETE[@]}"; do
    resource="${item%%:*}"
    stack="${item##*:}"
    
    TEARDOWN_STEPS=$((TEARDOWN_STEPS + 1))
    
    echo ""
    print_step "Step $TEARDOWN_STEPS: Deleting $resource"
    
    if ! delete_stack "$stack" "$resource"; then
        FAILED_STEPS+=("$resource")
        print_warning "Continuing with remaining teardown..."
    fi
done

# =============================================================================
# Summary
# =============================================================================

echo ""
echo ""
draw_box_top 64
if [ "$DRY_RUN" = true ]; then
    draw_box_row_centered "DRY RUN COMPLETE" 64
else
    draw_box_row_centered "TEARDOWN COMPLETE" 64
fi
draw_box_separator 64
draw_box_row "Environment: $ENVIRONMENT" 64
draw_box_row "Steps executed: $TEARDOWN_STEPS" 64
draw_box_bottom 64
echo ""

if [ ${#FAILED_STEPS[@]} -eq 0 ]; then
    if [ "$DRY_RUN" = true ]; then
        print_complete "Dry run completed - no resources were deleted"
    else
        print_complete "All teardown steps completed successfully!"
    fi
else
    print_error "Some teardown steps failed:"
    for step in "${FAILED_STEPS[@]}"; do
        print_error "  - $step"
    done
    print_warning "You may need to manually delete remaining resources"
    exit 1
fi

print_complete "Environment teardown completed"
