#!/bin/bash
# Common utilities for deployment scripts
# This file provides shared functions for logging, colors, and common operations

# =============================================================================
# Color Definitions
# =============================================================================
WHITE='\033[1;37m'    # INFO - general information
CYAN='\033[0;36m'     # STEP - major deployment steps
YELLOW='\033[1;33m'   # WARNING - warnings
RED='\033[0;31m'      # ERROR - errors
GREEN='\033[0;32m'    # COMPLETE - successful completion
NC='\033[0m'          # No Color

# =============================================================================
# Logging Functions
# =============================================================================

# Print general information (no color)
print_info() {
    echo "[INFO] $1"
}

# Print major step/phase (cyan)
print_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

# Print warning message (yellow)
print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Print error message (red)
print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Print completion message (green)
print_complete() {
    echo -e "${GREEN}[COMPLETE]${NC} $1"
}

# Print a header for a deployment script
print_header() {
    local script_name=$1
    echo ""
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  ${script_name}${NC}"
    echo -e "${CYAN}════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# =============================================================================
# Validation Functions
# =============================================================================

# Validate environment name
validate_environment() {
    local env=$1
    case $env in
        dev|staging|prod)
            return 0
            ;;
        *)
            print_error "Invalid environment: $env"
            print_error "Valid environments are: dev, staging, prod"
            return 1
            ;;
    esac
}

# Validate action
validate_action() {
    local action=$1
    case $action in
        deploy|update|delete|validate|status)
            return 0
            ;;
        *)
            print_error "Invalid action: $action"
            print_error "Valid actions are: deploy, update, delete, validate, status"
            return 1
            ;;
    esac
}

# =============================================================================
# AWS Helper Functions
# =============================================================================

# Get AWS account ID
get_aws_account_id() {
    local profile=$1
    local region=$2
    
    local cmd="aws sts get-caller-identity --query 'Account' --output text"
    [ -n "$profile" ] && cmd="$cmd --profile $profile"
    [ -n "$region" ] && cmd="$cmd --region $region"
    
    eval $cmd 2>/dev/null
}

# Check if a CloudFormation stack exists
stack_exists() {
    local stack_name=$1
    local region=$2
    local profile=$3
    
    local cmd="aws cloudformation describe-stacks --stack-name $stack_name --region $region"
    [ -n "$profile" ] && cmd="$cmd --profile $profile"
    
    eval $cmd >/dev/null 2>&1
}

# Get stack status
get_stack_status() {
    local stack_name=$1
    local region=$2
    local profile=$3
    
    local cmd="aws cloudformation describe-stacks --stack-name $stack_name --region $region --query 'Stacks[0].StackStatus' --output text"
    [ -n "$profile" ] && cmd="$cmd --profile $profile"
    
    eval $cmd 2>/dev/null
}

# Get stack output value
get_stack_output() {
    local stack_name=$1
    local output_key=$2
    local region=$3
    local profile=$4
    
    local cmd="aws cloudformation describe-stacks --stack-name $stack_name --region $region --query \"Stacks[0].Outputs[?OutputKey=='$output_key'].OutputValue\" --output text"
    [ -n "$profile" ] && cmd="$cmd --profile $profile"
    
    eval $cmd 2>/dev/null
}

# =============================================================================
# File and Path Functions
# =============================================================================

# Get the project root directory (assumes scripts are in scripts/deploy or scripts/utils)
get_project_root() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    
    # Handle both scripts/deploy and scripts/utils locations
    if [[ "$script_dir" == */scripts/deploy ]] || [[ "$script_dir" == */scripts/utils ]] || [[ "$script_dir" == */scripts/setup ]]; then
        echo "$(dirname "$(dirname "$script_dir")")"
    else
        # Fallback: go up until we find infra/infra.yaml
        local current="$script_dir"
        while [[ "$current" != "/" ]]; do
            if [[ -f "$current/infra/infra.yaml" ]]; then
                echo "$current"
                return 0
            fi
            current="$(dirname "$current")"
        done
        print_error "Could not find project root"
        return 1
    fi
}

# Get the infra.yaml path
get_infra_yaml_path() {
    local project_root=$(get_project_root)
    echo "$project_root/infra/infra.yaml"
}

# =============================================================================
# Template Functions
# =============================================================================

# Validate a CloudFormation template
validate_template() {
    local template_file=$1
    local region=$2
    local profile=$3
    
    print_step "Validating CloudFormation template: $template_file"
    
    if [ ! -f "$template_file" ]; then
        print_error "Template file not found: $template_file"
        return 1
    fi
    
    local cmd="aws cloudformation validate-template --template-body file://$template_file --region $region"
    [ -n "$profile" ] && cmd="$cmd --profile $profile"
    
    if eval $cmd >/dev/null 2>&1; then
        print_complete "Template validation successful"
        return 0
    else
        print_error "Template validation failed"
        return 1
    fi
}

# =============================================================================
# Wait Functions
# =============================================================================

# Wait for stack operation to complete
wait_for_stack() {
    local stack_name=$1
    local operation=$2  # create or update
    local region=$3
    local profile=$4
    local timeout=${5:-600}  # Default 10 minutes
    
    local wait_cmd="stack-${operation}-complete"
    local cmd="aws cloudformation wait $wait_cmd --stack-name $stack_name --region $region"
    [ -n "$profile" ] && cmd="$cmd --profile $profile"
    
    print_info "Waiting for stack $operation to complete (timeout: ${timeout}s)..."
    
    # Run wait with timeout
    local start_time=$(date +%s)
    while true; do
        local status=$(get_stack_status "$stack_name" "$region" "$profile")
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        case "$status" in
            CREATE_COMPLETE|UPDATE_COMPLETE)
                print_complete "Stack $operation completed successfully"
                return 0
                ;;
            CREATE_FAILED|UPDATE_FAILED|ROLLBACK_COMPLETE|UPDATE_ROLLBACK_COMPLETE|DELETE_FAILED)
                print_error "Stack $operation failed with status: $status"
                return 1
                ;;
            DELETE_COMPLETE)
                print_complete "Stack deleted successfully"
                return 0
                ;;
        esac
        
        if [ $elapsed -ge $timeout ]; then
            print_error "Timeout waiting for stack $operation"
            return 1
        fi
        
        print_info "Stack status: $status (elapsed: ${elapsed}s)"
        sleep 10
    done
}
