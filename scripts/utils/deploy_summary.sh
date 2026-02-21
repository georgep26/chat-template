#!/bin/bash
# Deploy summary functions for deployment scripts
# This file provides functions to display deployment summaries and confirmations

# Source common utilities and config parser if not already sourced
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "$WHITE" ]; then
    source "$SCRIPT_DIR/common.sh"
fi
if [ -z "$INFRA_CONFIG_LOADED" ]; then
    source "$SCRIPT_DIR/config_parser.sh"
fi

# =============================================================================
# Summary Box Drawing
# =============================================================================

# Draw a horizontal line
draw_line() {
    local width=${1:-64}
    local char=${2:-═}
    printf '%*s' "$width" | tr ' ' "$char"
}

# Draw a box top
draw_box_top() {
    local width=${1:-64}
    echo "╔$(draw_line $((width-2)))╗"
}

# Draw a box bottom
draw_box_bottom() {
    local width=${1:-64}
    echo "╚$(draw_line $((width-2)))╝"
}

# Draw a box separator
draw_box_separator() {
    local width=${1:-64}
    echo "╠$(draw_line $((width-2)))╣"
}

# Draw a box row with content
draw_box_row() {
    local content=$1
    local width=${2:-64}
    local padding=$((width - 4 - ${#content}))
    if [ $padding -lt 0 ]; then
        padding=0
        content="${content:0:$((width-4))}"
    fi
    printf "║  %s%*s║\n" "$content" "$padding" ""
}

# Draw a centered box row
draw_box_row_centered() {
    local content=$1
    local width=${2:-64}
    local content_len=${#content}
    local inner_width=$((width - 2))  # subtract 2 for ║...║ borders
    local total_padding=$((inner_width - content_len))
    local left_padding=$((total_padding / 2))
    local right_padding=$((total_padding - left_padding))
    printf "║%*s%s%*s║\n" "$left_padding" "" "$content" "$right_padding" ""
}

# =============================================================================
# Deploy Summary Functions
# =============================================================================

# Print a deployment summary for a single resource
print_resource_summary() {
    local resource_name=$1
    local env=$2
    local action=${3:-deploy}
    local width=64
    
    ensure_config_loaded || return 1
    
    # Get config path (ensure it's set)
    local config_path="${INFRA_CONFIG_PATH:-$(get_infra_yaml_path)}"
    
    local project_name=$(get_project_name)
    local account_id=$(get_environment_account_id "$env")
    local account_name=$(yq ".environments.${env}.account_name" "$config_path" 2>/dev/null || echo "")
    [ "$account_name" = "null" ] && account_name=""
    local region=$(get_environment_region "$env")
    local profile=$(get_environment_profile "$env")
    
    # Resolve placeholder account_id if needed
    if [[ "$account_id" == *'${'* ]]; then
        # Try to get actual account ID from AWS credentials
        if command -v aws &> /dev/null; then
            local aws_cmd="aws"
            [ "$profile" != "null" ] && [ -n "$profile" ] && aws_cmd="aws --profile $profile"
            local resolved_account_id=$($aws_cmd sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
            if [ -n "$resolved_account_id" ]; then
                account_id="$resolved_account_id"
            fi
        fi
    fi
    local stack_name=$(get_resource_stack_name "$resource_name" "$env")
    local template=$(get_resource_template "$resource_name")
    
    echo ""
    draw_box_top $width
    draw_box_row_centered "DEPLOY SUMMARY" $width
    draw_box_separator $width
    draw_box_row "Environment:  $env" $width
    draw_box_row "Region:       $region" $width
    if [ -n "$account_name" ] && [ "$account_name" != "null" ]; then
        draw_box_row "Account:      $account_name ($account_id)" $width
    else
        draw_box_row "Account:      $account_id" $width
    fi
    [ "$profile" != "null" ] && draw_box_row "Profile:      $profile" $width
    draw_box_separator $width
    draw_box_row "Resource:     $resource_name" $width
    draw_box_row "Stack:        $stack_name" $width
    [ -n "$template" ] && draw_box_row "Template:     ${template##*/}" $width
    draw_box_row "Action:       $(echo "$action" | tr '[:lower:]' '[:upper:]')" $width
    draw_box_bottom $width
    echo ""
}

# Print a deployment summary for all resources
print_full_deploy_summary() {
    local env=$1
    local action=${2:-deploy}
    local width=64
    
    ensure_config_loaded || return 1
    
    local project_name=$(get_project_name)
    local account_id=$(get_environment_account_id "$env")
    local region=$(get_environment_region "$env")
    local profile=$(get_environment_profile "$env")
    
    echo ""
    draw_box_top $width
    draw_box_row_centered "FULL DEPLOYMENT SUMMARY" $width
    draw_box_separator $width
    draw_box_row "Project:      $project_name" $width
    draw_box_row "Environment:  $env" $width
    draw_box_row "Region:       $region" $width
    draw_box_row "Account:      $account_id" $width
    [ "$profile" != "null" ] && draw_box_row "Profile:      $profile" $width
    draw_box_separator $width
    draw_box_row "Resources to ${action}:" $width
    
    local order=$(get_deployment_order)
    while IFS= read -r resource; do
        if is_resource_enabled "$resource"; then
            local stack_name=$(get_resource_stack_name "$resource" "$env")
            draw_box_row "  - $resource ($stack_name)" $width
        fi
    done <<< "$order"
    
    draw_box_bottom $width
    echo ""
}

# Print a teardown summary
print_teardown_summary() {
    local env=$1
    local width=64
    
    ensure_config_loaded || return 1
    
    local project_name=$(get_project_name)
    local account_id=$(get_environment_account_id "$env")
    local region=$(get_environment_region "$env")
    local profile=$(get_environment_profile "$env")
    
    echo ""
    echo -e "${RED}"
    draw_box_top $width
    draw_box_row_centered "TEARDOWN SUMMARY" $width
    draw_box_separator $width
    draw_box_row "Project:      $project_name" $width
    draw_box_row "Environment:  $env" $width
    draw_box_row "Region:       $region" $width
    draw_box_row "Account:      $account_id" $width
    draw_box_separator $width
    draw_box_row "Resources to DELETE (in order):" $width
    
    local order=$(get_teardown_order)
    while IFS= read -r resource; do
        if is_resource_enabled "$resource"; then
            local stack_name=$(get_resource_stack_name "$resource" "$env")
            draw_box_row "  - $resource ($stack_name)" $width
        fi
    done <<< "$order"
    
    draw_box_bottom $width
    echo -e "${NC}"
    echo ""
}

# =============================================================================
# Confirmation Functions
# =============================================================================

# Prompt for deployment confirmation
# Returns 0 for yes, 1 for no
confirm_deployment() {
    local message=${1:-"Do you want to proceed?"}
    
    echo -en "${WHITE}$message (y/N): ${NC}"
    read -r response
    
    case "$response" in
        [yY]|[yY][eE][sS])
            return 0
            ;;
        *)
            print_info "Deployment cancelled"
            return 1
            ;;
    esac
}

# Prompt for destructive action confirmation (requires typing 'yes')
confirm_destructive_action() {
    local env=$1
    local action=${2:-"delete"}
    
    echo ""
    print_warning "This is a DESTRUCTIVE action!"
    print_warning "You are about to $action resources in the '$env' environment."
    echo ""
    echo -en "${RED}Type 'yes' to confirm: ${NC}"
    read -r response
    
    if [ "$response" = "yes" ]; then
        return 0
    else
        print_info "Action cancelled"
        return 1
    fi
}

# =============================================================================
# Resource Configuration Display
# =============================================================================

# Print resource configuration details
print_resource_config() {
    local resource=$1
    local env=$2
    
    ensure_config_loaded || return 1
    
    print_info "Configuration for $resource:"
    
    case "$resource" in
        network)
            local vpc_cidr=$(get_resource_config "network" "vpc_cidr")
            local nat_gateway=$(get_resource_config "network" "enable_nat_gateway")
            print_info "  VPC CIDR: $vpc_cidr"
            print_info "  NAT Gateway: $nat_gateway"
            ;;
        s3_bucket)
            local bucket_name=$(get_resource_config "s3_bucket" "bucket_name" "$env")
            local versioning=$(get_resource_config "s3_bucket" "enable_versioning")
            local lifecycle=$(get_resource_config "s3_bucket" "enable_lifecycle")
            print_info "  Bucket Name: $bucket_name"
            print_info "  Versioning: $versioning"
            print_info "  Lifecycle: $lifecycle"
            ;;
        chat_db)
            local min_acu=$(get_resource_config "chat_db" "min_acu")
            local max_acu=$(get_resource_config "chat_db" "max_acu")
            local engine=$(get_resource_config "chat_db" "engine_version")
            print_info "  Min ACU: $min_acu"
            print_info "  Max ACU: $max_acu"
            print_info "  Engine Version: $engine"
            ;;
        rag_lambda_ecr)
            local repo_name=$(get_resource_config "rag_lambda_ecr" "repository_name" "$env")
            local max_images=$(get_resource_config "rag_lambda_ecr" "max_image_count")
            print_info "  Repository Name: $repo_name"
            print_info "  Max Image Count: $max_images"
            ;;
        rag_lambda)
            local func_name=$(get_resource_config "rag_lambda" "function_name" "$env")
            local memory=$(get_resource_config "rag_lambda" "memory_size")
            local timeout=$(get_resource_config "rag_lambda" "timeout")
            print_info "  Function Name: $func_name"
            print_info "  Memory Size: ${memory}MB"
            print_info "  Timeout: ${timeout}s"
            ;;
    esac
}

# =============================================================================
# Status Display
# =============================================================================

# Print stack status with color coding
print_stack_status() {
    local stack_name=$1
    local region=$2
    local profile=$3
    
    local status=$(get_stack_status "$stack_name" "$region" "$profile")
    
    if [ -z "$status" ] || [ "$status" = "null" ]; then
        print_info "Stack $stack_name: ${YELLOW}NOT FOUND${NC}"
        return 1
    fi
    
    case "$status" in
        CREATE_COMPLETE|UPDATE_COMPLETE)
            echo -e "${GREEN}[ACTIVE]${NC} $stack_name: $status"
            ;;
        CREATE_IN_PROGRESS|UPDATE_IN_PROGRESS|DELETE_IN_PROGRESS)
            echo -e "${CYAN}[IN PROGRESS]${NC} $stack_name: $status"
            ;;
        ROLLBACK_COMPLETE|UPDATE_ROLLBACK_COMPLETE|CREATE_FAILED|DELETE_FAILED)
            echo -e "${RED}[FAILED]${NC} $stack_name: $status"
            ;;
        DELETE_COMPLETE)
            echo -e "${YELLOW}[DELETED]${NC} $stack_name: $status"
            ;;
        *)
            echo -e "${WHITE}[UNKNOWN]${NC} $stack_name: $status"
            ;;
    esac
    
    return 0
}

# Print status of all resources for an environment
print_environment_status() {
    local env=$1
    local region=$(get_environment_region "$env")
    local profile=$(get_environment_profile "$env")
    
    print_step "Checking resource status for $env environment"
    echo ""
    
    local order=$(get_deployment_order)
    while IFS= read -r resource; do
        if is_resource_enabled "$resource"; then
            local stack_name=$(get_resource_stack_name "$resource" "$env")
            print_stack_status "$stack_name" "$region" "$profile"
        fi
    done <<< "$order"
    
    echo ""
}
