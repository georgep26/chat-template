#!/bin/bash
# Configuration parser for infra.yaml
# This file provides functions to read and parse the infrastructure configuration

# Source common utilities if not already sourced
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "$WHITE" ]; then
    source "$SCRIPT_DIR/common.sh"
fi

# =============================================================================
# yq Installation Check
# =============================================================================

# Check if yq is installed
check_yq_installed() {
    if ! command -v yq &> /dev/null; then
        print_error "yq is required but not installed."
        print_info "Install yq using one of these methods:"
        print_info "  macOS:   brew install yq"
        print_info "  Linux:   sudo wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/bin/yq && sudo chmod +x /usr/bin/yq"
        print_info "  Or visit: https://github.com/mikefarah/yq#install"
        return 1
    fi
    return 0
}

# =============================================================================
# Configuration Loading
# =============================================================================

# Global variables for loaded config
INFRA_CONFIG_LOADED=false
INFRA_CONFIG_PATH=""

# Load the infra.yaml configuration
load_infra_config() {
    local config_path=${1:-$(get_infra_yaml_path)}
    
    if [ ! -f "$config_path" ]; then
        print_error "Configuration file not found: $config_path"
        return 1
    fi
    
    check_yq_installed || return 1
    
    INFRA_CONFIG_PATH="$config_path"
    INFRA_CONFIG_LOADED=true
    return 0
}

# Ensure config is loaded
ensure_config_loaded() {
    if [ "$INFRA_CONFIG_LOADED" != "true" ]; then
        load_infra_config || return 1
    fi
    return 0
}

# =============================================================================
# Project Configuration
# =============================================================================

# Get project name
get_project_name() {
    ensure_config_loaded || return 1
    yq '.project.name' "$INFRA_CONFIG_PATH"
}

# Get default region
get_default_region() {
    ensure_config_loaded || return 1
    yq '.project.default_region' "$INFRA_CONFIG_PATH"
}

# =============================================================================
# Environment Configuration
# =============================================================================

# Get environment account ID
get_environment_account_id() {
    local env=$1
    ensure_config_loaded || return 1
    yq ".environments.${env}.account_id" "$INFRA_CONFIG_PATH"
}

# Get environment region
get_environment_region() {
    local env=$1
    ensure_config_loaded || return 1
    local region=$(yq ".environments.${env}.region" "$INFRA_CONFIG_PATH")
    if [ "$region" = "null" ] || [ -z "$region" ]; then
        get_default_region
    else
        echo "$region"
    fi
}

# Get environment deployer profile
get_environment_profile() {
    local env=$1
    ensure_config_loaded || return 1
    yq ".environments.${env}.deployer_profile" "$INFRA_CONFIG_PATH"
}

# Get environment secrets file path
get_environment_secrets_file() {
    local env=$1
    ensure_config_loaded || return 1
    local project_root=$(get_project_root)
    local secrets_path=$(yq ".environments.${env}.secrets_file" "$INFRA_CONFIG_PATH")
    echo "$project_root/infra/$secrets_path"
}

# Check if environment exists in config
environment_exists() {
    local env=$1
    ensure_config_loaded || return 1
    local account_id=$(yq ".environments.${env}.account_id" "$INFRA_CONFIG_PATH")
    [ "$account_id" != "null" ] && [ -n "$account_id" ]
}

# =============================================================================
# Resource Configuration
# =============================================================================

# Check if a resource is enabled
is_resource_enabled() {
    local resource=$1
    ensure_config_loaded || return 1
    local enabled=$(yq ".resources.${resource}.enabled" "$INFRA_CONFIG_PATH")
    [ "$enabled" = "true" ]
}

# Get list of all resources (in order defined in yaml)
get_resource_list() {
    ensure_config_loaded || return 1
    yq '.resources | keys | .[]' "$INFRA_CONFIG_PATH"
}

# Get list of enabled resources (in order defined in yaml)
get_enabled_resources() {
    ensure_config_loaded || return 1
    yq '.resources | to_entries | .[] | select(.value.enabled == true) | .key' "$INFRA_CONFIG_PATH"
}

# Get resource template path
get_resource_template() {
    local resource=$1
    local template_key=${2:-template}  # Default to 'template', can be 'main', 'secret', etc.
    ensure_config_loaded || return 1
    local project_root=$(get_project_root)
    
    # Check if it's a single template or multiple templates
    local template_path=$(yq ".resources.${resource}.template" "$INFRA_CONFIG_PATH")
    if [ "$template_path" = "null" ] || [ -z "$template_path" ]; then
        # Multiple templates - get specific one
        template_path=$(yq ".resources.${resource}.templates.${template_key}" "$INFRA_CONFIG_PATH")
    fi
    
    if [ "$template_path" != "null" ] && [ -n "$template_path" ]; then
        echo "$project_root/infra/$template_path"
    fi
}

# Get resource stack name (with variable substitution)
get_resource_stack_name() {
    local resource=$1
    local env=$2
    local stack_key=${3:-stack_name}  # Can be 'stack_name', 'secret_stack_name', etc.
    ensure_config_loaded || return 1
    
    local project_name=$(get_project_name)
    local stack_pattern=$(yq ".resources.${resource}.${stack_key}" "$INFRA_CONFIG_PATH")
    
    if [ "$stack_pattern" = "null" ] || [ -z "$stack_pattern" ]; then
        return 1
    fi
    
    # Substitute variables
    stack_pattern="${stack_pattern//\{project\}/$project_name}"
    stack_pattern="${stack_pattern//\{env\}/$env}"
    
    echo "$stack_pattern"
}

# Get resource config value (with variable substitution)
get_resource_config() {
    local resource=$1
    local config_key=$2
    local env=${3:-}
    ensure_config_loaded || return 1
    
    local value=$(yq ".resources.${resource}.config.${config_key}" "$INFRA_CONFIG_PATH")
    
    # Substitute variables if env is provided
    if [ -n "$env" ] && [ "$value" != "null" ]; then
        local project_name=$(get_project_name)
        value="${value//\{project\}/$project_name}"
        value="${value//\{env\}/$env}"
    fi
    
    echo "$value"
}

# =============================================================================
# Role Configuration
# =============================================================================

# Check if a role is enabled
is_role_enabled() {
    local role=$1
    ensure_config_loaded || return 1
    local enabled=$(yq ".roles.${role}.enabled" "$INFRA_CONFIG_PATH")
    [ "$enabled" = "true" ]
}

# Get list of all roles (in order defined in yaml)
get_role_list() {
    ensure_config_loaded || return 1
    yq '.roles | keys | .[]' "$INFRA_CONFIG_PATH"
}

# Get list of enabled roles (in order defined in yaml)
get_enabled_roles() {
    ensure_config_loaded || return 1
    yq '.roles | to_entries | .[] | select(.value.enabled == true) | .key' "$INFRA_CONFIG_PATH"
}

# Get role template path
get_role_template() {
    local role=$1
    ensure_config_loaded || return 1
    local project_root=$(get_project_root)
    local template_path=$(yq ".roles.${role}.template" "$INFRA_CONFIG_PATH")
    
    if [ "$template_path" != "null" ] && [ -n "$template_path" ]; then
        echo "$project_root/infra/$template_path"
    fi
}

# Get role stack name
get_role_stack_name() {
    local role=$1
    local env=$2
    ensure_config_loaded || return 1
    
    local project_name=$(get_project_name)
    local stack_pattern=$(yq ".roles.${role}.stack_name" "$INFRA_CONFIG_PATH")
    
    if [ "$stack_pattern" = "null" ] || [ -z "$stack_pattern" ]; then
        return 1
    fi
    
    # Substitute variables
    stack_pattern="${stack_pattern//\{project\}/$project_name}"
    stack_pattern="${stack_pattern//\{env\}/$env}"
    
    echo "$stack_pattern"
}

# Get role policies (for roles with multiple policies)
get_role_policies() {
    local role=$1
    ensure_config_loaded || return 1
    local project_root=$(get_project_root)
    
    local policies=$(yq ".roles.${role}.policies[]" "$INFRA_CONFIG_PATH" 2>/dev/null)
    if [ -n "$policies" ]; then
        while IFS= read -r policy; do
            echo "$project_root/infra/$policy"
        done <<< "$policies"
    fi
}

# =============================================================================
# Tags Configuration
# =============================================================================

# Get required tags
get_required_tags() {
    ensure_config_loaded || return 1
    yq '.tags.required[]' "$INFRA_CONFIG_PATH"
}

# Get optional tags
get_optional_tags() {
    ensure_config_loaded || return 1
    yq '.tags.optional[]' "$INFRA_CONFIG_PATH"
}

# Get cost allocation tags
get_cost_allocation_tags() {
    ensure_config_loaded || return 1
    yq '.cost_tags.allocation_tags[]' "$INFRA_CONFIG_PATH"
}

# =============================================================================
# Secrets Configuration
# =============================================================================

# Get secret value from environment secrets file
get_secret_value() {
    local env=$1
    local key_path=$2  # e.g., "database.master_password"
    
    local secrets_file=$(get_environment_secrets_file "$env")
    
    if [ ! -f "$secrets_file" ]; then
        print_warning "Secrets file not found: $secrets_file"
        return 1
    fi
    
    yq ".$key_path" "$secrets_file"
}

# =============================================================================
# Deployment Order (based on order in infra.yaml)
# =============================================================================

# Get deployment order for resources (order defined in yaml)
get_deployment_order() {
    get_enabled_resources
}

# Get teardown order for resources (reverse of deployment)
get_teardown_order() {
    get_enabled_resources | tac
}

# Get all resources (enabled or not) for teardown check
get_all_resources() {
    get_resource_list
}

# =============================================================================
# Utility Functions
# =============================================================================

# Print all environment configurations
print_environments() {
    ensure_config_loaded || return 1
    print_info "Available environments:"
    for env in dev staging prod; do
        if environment_exists "$env"; then
            local account_id=$(get_environment_account_id "$env")
            local region=$(get_environment_region "$env")
            print_info "  $env: account=$account_id, region=$region"
        fi
    done
}

# Validate configuration for an environment
validate_config() {
    local env=$1
    ensure_config_loaded || return 1
    
    print_step "Validating configuration for environment: $env"
    
    # Check environment exists
    if ! environment_exists "$env"; then
        print_error "Environment '$env' not found in configuration"
        return 1
    fi
    
    # Check required fields
    local account_id=$(get_environment_account_id "$env")
    if [ "$account_id" = "null" ] || [ -z "$account_id" ]; then
        print_error "Missing account_id for environment: $env"
        return 1
    fi
    
    local region=$(get_environment_region "$env")
    if [ "$region" = "null" ] || [ -z "$region" ]; then
        print_error "Missing region for environment: $env"
        return 1
    fi
    
    print_complete "Configuration validation successful"
    return 0
}
