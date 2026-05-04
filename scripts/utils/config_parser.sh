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

# Get GitHub org from infra (github.github_repo is "org/repo")
get_github_org() {
    ensure_config_loaded || return 1
    local repo=$(yq -r '.github.github_repo // ""' "$INFRA_CONFIG_PATH")
    if [ -n "$repo" ] && [[ "$repo" == *"/"* ]]; then
        echo "${repo%%/*}"
    fi
}

# Get GitHub repo name from infra (github.github_repo is "org/repo")
get_github_repo() {
    ensure_config_loaded || return 1
    local repo=$(yq -r '.github.github_repo // ""' "$INFRA_CONFIG_PATH")
    if [ -n "$repo" ] && [[ "$repo" == *"/"* ]]; then
        echo "${repo#*/}"
    fi
}

# =============================================================================
# Environment Configuration
# =============================================================================

normalize_yaml_scalar() {
    local value=$1
    if [ "$value" = "null" ]; then
        echo ""
    else
        echo "$value"
    fi
}

is_unresolved_placeholder_value() {
    local value=$1
    [[ "$value" == *'${'* ]]
}

resolve_project_path() {
    local path=$1
    local project_root
    project_root=$(get_project_root)

    path=$(normalize_yaml_scalar "$path")
    if [ -z "$path" ]; then
        return 0
    fi

    case "$path" in
        /*)
            echo "$path"
            ;;
        infra/*|config/*|scripts/*|docs/*)
            echo "$project_root/$path"
            ;;
        *)
            echo "$project_root/infra/$path"
            ;;
    esac
}

get_accounts_secrets_file() {
    ensure_config_loaded || return 1
    local project_root
    project_root=$(get_project_root)
    if [ -n "${CHAT_TEMPLATE_ACCOUNTS_SECRETS_FILE:-}" ]; then
        echo "$CHAT_TEMPLATE_ACCOUNTS_SECRETS_FILE"
        return 0
    fi

    local configured_path
    configured_path=$(yq '.secrets.accounts_file // ""' "$INFRA_CONFIG_PATH" 2>/dev/null)
    configured_path=$(normalize_yaml_scalar "$configured_path")
    if [ -n "$configured_path" ]; then
        resolve_project_path "$configured_path"
    else
        echo "$project_root/infra/secrets/accounts.secrets.yaml"
    fi
}

get_environment_names() {
    ensure_config_loaded || return 1
    yq '.environments | to_entries | .[] | .key' "$INFRA_CONFIG_PATH"
}

get_account_names() {
    ensure_config_loaded || return 1
    yq '.accounts // {} | to_entries | .[] | .key' "$INFRA_CONFIG_PATH"
}

get_environment_account_key() {
    local env=$1
    ensure_config_loaded || return 1
    local account
    account=$(yq ".environments.${env}.account // \"\"" "$INFRA_CONFIG_PATH")
    account=$(normalize_yaml_scalar "$account")
    if [ -n "$account" ]; then
        echo "$account"
        return 0
    fi

    # Legacy configs may not have a separate accounts block.
    echo "$env"
}

get_account_secret_ref() {
    local account=$1
    ensure_config_loaded || return 1
    local secret_ref
    secret_ref=$(yq ".accounts.${account}.secret_ref // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    secret_ref=$(normalize_yaml_scalar "$secret_ref")
    if [ -n "$secret_ref" ]; then
        echo "$secret_ref"
    else
        echo "$account"
    fi
}

get_environment_account_secret_ref() {
    local env=$1
    local account
    account=$(get_environment_account_key "$env")
    get_account_secret_ref "$account"
}

get_account_secret_value() {
    local account_or_ref=$1
    local key_path=$2
    local secrets_file
    secrets_file=$(get_accounts_secrets_file)

    if [ ! -f "$secrets_file" ]; then
        return 1
    fi

    local value
    value=$(yq ".accounts.${account_or_ref}.${key_path} // \"\"" "$secrets_file" 2>/dev/null)
    value=$(normalize_yaml_scalar "$value")
    [ -n "$value" ] && echo "$value"
}

get_environment_account_secret_value() {
    local env=$1
    local key_path=$2
    local secret_ref
    secret_ref=$(get_environment_account_secret_ref "$env")
    get_account_secret_value "$secret_ref" "$key_path"
}

get_environment_account_kind() {
    local env=$1
    ensure_config_loaded || return 1
    local account
    account=$(get_environment_account_key "$env")
    local kind
    kind=$(yq ".accounts.${account}.kind // \"existing\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    normalize_yaml_scalar "$kind"
}

is_environment_ci_cd_enabled() {
    local env=$1
    ensure_config_loaded || return 1
    local enabled
    enabled=$(yq ".environments.${env}.ci_cd.enabled // false" "$INFRA_CONFIG_PATH" 2>/dev/null)
    [ "$enabled" = "true" ]
}

get_environment_github_environment() {
    local env=$1
    ensure_config_loaded || return 1
    local github_env
    github_env=$(yq ".environments.${env}.ci_cd.github_environment // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    github_env=$(normalize_yaml_scalar "$github_env")
    if [ -n "$github_env" ]; then
        echo "$github_env"
    else
        echo "$env"
    fi
}

# Get environment account ID
get_environment_account_id() {
    local env=$1
    ensure_config_loaded || return 1
    local value
    value=$(yq ".environments.${env}.account_id // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    value=$(normalize_yaml_scalar "$value")
    if [ -n "$value" ] && ! is_unresolved_placeholder_value "$value"; then
        echo "$value"
        return 0
    fi

    value=$(get_environment_account_secret_value "$env" "account_id" 2>/dev/null || true)
    if [ -z "$value" ] && [ -n "${ACCOUNT_ID:-}" ]; then
        value="$ACCOUNT_ID"
    fi
    echo "$value"
}

get_environment_account_name() {
    local env=$1
    ensure_config_loaded || return 1
    local value
    value=$(yq ".environments.${env}.account_name // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    value=$(normalize_yaml_scalar "$value")
    if [ -n "$value" ] && ! is_unresolved_placeholder_value "$value"; then
        echo "$value"
        return 0
    fi

    value=$(get_environment_account_secret_value "$env" "account_name" 2>/dev/null || true)
    echo "$value"
}

# Get environment region
get_environment_region() {
    local env=$1
    ensure_config_loaded || return 1
    local region=$(yq ".environments.${env}.region // \"\"" "$INFRA_CONFIG_PATH")
    region=$(normalize_yaml_scalar "$region")
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
    local value
    value=$(yq ".environments.${env}.deployer_profile // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    value=$(normalize_yaml_scalar "$value")
    if [ -n "$value" ] && ! is_unresolved_placeholder_value "$value"; then
        echo "$value"
        return 0
    fi

    value=$(get_environment_account_secret_value "$env" "deployer_profile" 2>/dev/null || true)
    [ -z "$value" ] && value=$(get_environment_account_secret_value "$env" "deploy_profile" 2>/dev/null || true)
    [ -z "$value" ] && value=$(get_environment_account_secret_value "$env" "cli_profile" 2>/dev/null || true)
    [ -z "$value" ] && value=$(get_environment_account_secret_value "$env" "bootstrap_profile" 2>/dev/null || true)
    echo "$value"
}

# Get environment CLI role name (IAM role in the account)
get_environment_cli_role_name() {
    local env=$1
    ensure_config_loaded || return 1
    local name=$(yq ".environments.${env}.cli_role_name // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    name=$(normalize_yaml_scalar "$name")
    if [ -z "$name" ]; then
        name=$(get_environment_account_secret_value "$env" "cli_role_name" 2>/dev/null || true)
    fi
    if [ "$name" = "null" ] || [ -z "$name" ]; then
        local project_name=$(get_project_name)
        echo "${project_name}-${env}-admin-cli-role"
    else
        echo "$name"
    fi
}

# Get environment CLI profile name (AWS CLI profile in ~/.aws/config)
get_environment_cli_profile_name() {
    local env=$1
    ensure_config_loaded || return 1
    local name=$(yq ".environments.${env}.cli_profile_name // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    name=$(normalize_yaml_scalar "$name")
    if [ -z "$name" ]; then
        name=$(get_environment_account_secret_value "$env" "cli_profile_name" 2>/dev/null || true)
    fi
    [ -z "$name" ] && name=$(get_environment_account_secret_value "$env" "cli_profile" 2>/dev/null || true)
    [ -z "$name" ] && name=$(get_environment_account_secret_value "$env" "deploy_profile" 2>/dev/null || true)
    [ -z "$name" ] && name=$(get_environment_account_secret_value "$env" "bootstrap_profile" 2>/dev/null || true)
    if [ "$name" = "null" ] || [ -z "$name" ]; then
        local project_name=$(get_project_name)
        echo "${project_name}-${env}-cli"
    else
        echo "$name"
    fi
}

# Get environment secrets file path
get_environment_secrets_file() {
    local env=$1
    ensure_config_loaded || return 1
    local secrets_path
    secrets_path=$(yq ".environments.${env}.secrets_file // \"\"" "$INFRA_CONFIG_PATH")
    secrets_path=$(normalize_yaml_scalar "$secrets_path")
    if [ -z "$secrets_path" ]; then
        local project_root
        project_root=$(get_project_root)
        echo "$project_root/infra/secrets/${env}.secrets.yaml"
        return 0
    fi
    resolve_project_path "$secrets_path"
}

# Get environment VPC ID
get_environment_vpc_id() {
    local env=$1
    ensure_config_loaded || return 1
    local vpc_id=$(yq ".environments.${env}.vpc_id // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    vpc_id=$(normalize_yaml_scalar "$vpc_id")
    if [ -z "$vpc_id" ] || is_unresolved_placeholder_value "$vpc_id"; then
        vpc_id=$(get_secret_value "$env" "config_secrets.VPC_ID" 2>/dev/null || true)
        vpc_id=$(normalize_yaml_scalar "$vpc_id")
    fi
    echo "$vpc_id"
}

# Get environment subnet IDs
get_environment_subnet_ids() {
    local env=$1
    ensure_config_loaded || return 1
    local subnet_ids=$(yq ".environments.${env}.subnet_ids // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    subnet_ids=$(normalize_yaml_scalar "$subnet_ids")
    if [ -z "$subnet_ids" ] || is_unresolved_placeholder_value "$subnet_ids"; then
        subnet_ids=$(get_secret_value "$env" "config_secrets.SUBNET_IDS" 2>/dev/null || true)
        subnet_ids=$(normalize_yaml_scalar "$subnet_ids")
    fi
    echo "$subnet_ids"
}

# Check if environment exists in config
environment_exists() {
    local env=$1
    ensure_config_loaded || return 1
    yq -e ".environments | has(\"${env}\")" "$INFRA_CONFIG_PATH" >/dev/null 2>&1
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
    yq '.resources | to_entries | .[] | .key' "$INFRA_CONFIG_PATH"
}

# Get list of enabled resources (in order defined in yaml)
get_enabled_resources() {
    ensure_config_loaded || return 1
    yq '.resources | to_entries | .[] | select(.value.enabled == true) | .key' "$INFRA_CONFIG_PATH"
}

deployment_set_exists() {
    local deployment_set=$1
    ensure_config_loaded || return 1

    if ! yq -e '.deployment_sets // false' "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
        [ "$deployment_set" = "full" ]
        return $?
    fi

    DEPLOYMENT_SET="$deployment_set" yq -e '.deployment_sets | has(strenv(DEPLOYMENT_SET))' "$INFRA_CONFIG_PATH" >/dev/null 2>&1
}

validate_deployment_set() {
    local deployment_set=${1:-full}
    ensure_config_loaded || return 1

    if ! deployment_set_exists "$deployment_set"; then
        print_error "Unknown deployment set: $deployment_set"
        return 1
    fi

    if ! yq -e '.deployment_sets // false' "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
        return 0
    fi

    local resource_tag
    resource_tag=$(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].resources | tag' "$INFRA_CONFIG_PATH" 2>/dev/null)

    case "$resource_tag" in
        "!!str")
            local resource_value
            resource_value=$(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].resources' "$INFRA_CONFIG_PATH")
            if [ "$resource_value" != "all" ]; then
                print_error "deployment_sets.${deployment_set}.resources must be 'all' or a list of resource keys"
                return 1
            fi
            ;;
        "!!seq")
            local resource
            while IFS= read -r resource; do
                [ -z "$resource" ] && continue
                if ! RESOURCE_KEY="$resource" yq -e '.resources | has(strenv(RESOURCE_KEY))' "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
                    print_error "deployment_sets.${deployment_set}.resources references unknown resource: $resource"
                    return 1
                fi
            done < <(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].resources[]' "$INFRA_CONFIG_PATH")
            ;;
        *)
            print_error "deployment_sets.${deployment_set}.resources must be 'all' or a list of resource keys"
            return 1
            ;;
    esac
}

get_deployment_set_resources() {
    local deployment_set=${1:-full}
    ensure_config_loaded || return 1

    if ! yq -e '.deployment_sets // false' "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
        get_enabled_resources
        return 0
    fi

    validate_deployment_set "$deployment_set" >/dev/null || return 1

    local resource_tag
    resource_tag=$(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].resources | tag' "$INFRA_CONFIG_PATH" 2>/dev/null)
    if [ "$resource_tag" = "!!str" ]; then
        get_enabled_resources
        return 0
    fi

    local selected_resources="|"
    local resource
    while IFS= read -r resource; do
        [ -z "$resource" ] && continue
        selected_resources="${selected_resources}${resource}|"
    done < <(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].resources[]' "$INFRA_CONFIG_PATH")

    while IFS= read -r resource; do
        [ -z "$resource" ] && continue
        if [[ "$selected_resources" == *"|${resource}|"* ]]; then
            echo "$resource"
        fi
    done < <(get_enabled_resources)
}

is_deployment_set_cost_control_enabled() {
    local deployment_set=${1:-full}
    ensure_config_loaded || return 1

    if ! yq -e '.deployment_sets // false' "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
        return 0
    fi

    local enabled
    enabled=$(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].deploy_cost_control' "$INFRA_CONFIG_PATH")
    if [ "$enabled" = "null" ] || [ -z "$enabled" ]; then
        enabled=true
    fi
    [ "$enabled" = "true" ]
}

is_deployment_set_config_enabled() {
    local deployment_set=${1:-full}
    ensure_config_loaded || return 1

    if ! yq -e '.deployment_sets // false' "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
        return 0
    fi

    local enabled
    enabled=$(DEPLOYMENT_SET="$deployment_set" yq -r '.deployment_sets[strenv(DEPLOYMENT_SET)].deploy_config' "$INFRA_CONFIG_PATH")
    if [ "$enabled" = "null" ] || [ -z "$enabled" ]; then
        enabled=true
    fi
    [ "$enabled" = "true" ]
}

# Get resource template path
get_resource_template() {
    local resource=$1
    local template_key=${2:-template}  # Default to 'template', can be 'main', 'secret', etc.
    ensure_config_loaded || return 1
    
    # Check if it's a single template or multiple templates
    local template_path=$(yq ".resources.${resource}.template" "$INFRA_CONFIG_PATH")
    if [ "$template_path" = "null" ] || [ -z "$template_path" ]; then
        # Multiple templates - get specific one
        template_path=$(yq ".resources.${resource}.templates.${template_key}" "$INFRA_CONFIG_PATH")
    fi
    
    if [ "$template_path" != "null" ] && [ -n "$template_path" ]; then
        resolve_project_path "$template_path"
    fi
}

get_environment_global_suffix() {
    local env=$1
    local value
    value=$(get_environment_account_secret_value "$env" "global_suffix" 2>/dev/null || true)
    if [ -n "$value" ]; then
        echo "$value"
        return 0
    fi

    if [ -n "${GLOBAL_SUFFIX:-}" ]; then
        echo "$GLOBAL_SUFFIX"
        return 0
    fi

    if [ -n "${ACCOUNT_ID:-}" ]; then
        echo "$ACCOUNT_ID"
        return 0
    fi

    # Fallback for local summaries before accounts.secrets.yaml exists. This is not
    # globally unique; real deployments should set a stable global_suffix in account
    # secrets. The full AWS account ID is convenient, but any account-unique value is valid.
    echo "local"
}

substitute_config_tokens() {
    local value=$1
    local env=${2:-}
    ensure_config_loaded || return 1

    local project_name
    project_name=$(get_project_name)
    value="${value//\{project\}/$project_name}"

    if [ -n "$env" ]; then
        local account account_id region global_suffix
        account=$(get_environment_account_key "$env" 2>/dev/null || true)
        account_id=$(get_environment_account_id "$env" 2>/dev/null || true)
        region=$(get_environment_region "$env" 2>/dev/null || true)
        global_suffix=$(get_environment_global_suffix "$env" 2>/dev/null || true)
        value="${value//\{env\}/$env}"
        value="${value//\{account\}/$account}"
        value="${value//\{account_id\}/$account_id}"
        value="${value//\{region\}/$region}"
        value="${value//\{global_suffix\}/$global_suffix}"
    fi

    echo "$value"
}

# Get resource stack name (with variable substitution)
get_resource_stack_name() {
    local resource=$1
    local env=$2
    local stack_key=${3:-stack_name}  # Can be 'stack_name', 'secret_stack_name', etc.
    ensure_config_loaded || return 1
    
    local stack_pattern
    if [ "$stack_key" = "stack_name" ]; then
        stack_pattern=$(yq ".resources.${resource}.stack_name" "$INFRA_CONFIG_PATH")
    else
        stack_pattern=$(yq ".resources.${resource}.${stack_key} // .resources.${resource}.secondary_stacks.${stack_key}.stack_name" "$INFRA_CONFIG_PATH" 2>/dev/null)
    fi
    
    if [ "$stack_pattern" = "null" ] || [ -z "$stack_pattern" ]; then
        return 1
    fi
    
    substitute_config_tokens "$stack_pattern" "$env"
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
        value=$(substitute_config_tokens "$value" "$env")
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
    yq '.roles | to_entries | .[] | .key' "$INFRA_CONFIG_PATH"
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
        resolve_project_path "$template_path"
    fi
}

# Get role stack name
get_role_stack_name() {
    local role=$1
    local env=$2
    ensure_config_loaded || return 1
    
    local stack_pattern=$(yq ".roles.${role}.stack_name" "$INFRA_CONFIG_PATH")
    
    if [ "$stack_pattern" = "null" ] || [ -z "$stack_pattern" ]; then
        return 1
    fi
    
    substitute_config_tokens "$stack_pattern" "$env"
}

# Get role policies (for roles with multiple policies)
get_role_policies() {
    local role=$1
    ensure_config_loaded || return 1
    local project_root=$(get_project_root)
    
    local policies=$(yq ".roles.${role}.policies[]" "$INFRA_CONFIG_PATH" 2>/dev/null)
    if [ -n "$policies" ]; then
        while IFS= read -r policy; do
            resolve_project_path "$policy"
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
# Cost Control Configuration
# =============================================================================

# Check if cost control is enabled
is_cost_control_enabled() {
    ensure_config_loaded || return 1
    local enabled
    enabled=$(yq '.cost_control.enabled // false' "$INFRA_CONFIG_PATH")
    [ "$enabled" = "true" ]
}

# Get cost control value (e.g. "budget.amount_usd", "monitor.interval_minutes")
# Optional third arg applies {project}/{env} substitution for string values.
get_cost_control_value() {
    local key_path=$1
    local env=${2:-}
    ensure_config_loaded || return 1

    local value
    value=$(yq ".cost_control.${key_path}" "$INFRA_CONFIG_PATH")

    if [ -n "$env" ] && [ "$value" != "null" ]; then
        local project_name
        project_name=$(get_project_name)
        value="${value//\{project\}/$project_name}"
        value="${value//\{env\}/$env}"
    fi

    echo "$value"
}

# Get cost control stack name with substitutions.
get_cost_control_stack_name() {
    local env=$1
    ensure_config_loaded || return 1
    local pattern
    pattern=$(yq '.cost_control.stack_name // ""' "$INFRA_CONFIG_PATH")
    if [ -z "$pattern" ] || [ "$pattern" = "null" ]; then
        local project_name
        project_name=$(get_project_name)
        echo "${project_name}-cost-guard-${env}"
        return 0
    fi
    local project_name
    project_name=$(get_project_name)
    pattern="${pattern//\{project\}/$project_name}"
    pattern="${pattern//\{env\}/$env}"
    echo "$pattern"
}

# Returns compact JSON for .cost_control with {project}/{env} substitutions.
get_cost_control_json() {
    local env=$1
    ensure_config_loaded || return 1

    local project_name
    project_name=$(get_project_name)
    local value
    value=$(yq -o=json '.cost_control // {}' "$INFRA_CONFIG_PATH")
    # Compact and apply placeholder substitution.
    value="${value//$'\n'/}"
    value="${value//$'\t'/}"
    value="${value//\{project\}/$project_name}"
    value="${value//\{env\}/$env}"
    echo "$value"
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
    local resources=()
    local resource
    while IFS= read -r resource; do
        [ -n "$resource" ] && resources+=("$resource")
    done < <(get_resource_list)

    local i
    for (( i=${#resources[@]}-1; i>=0; i-- )); do
        echo "${resources[$i]}"
    done
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
    local env
    while IFS= read -r env; do
        if environment_exists "$env"; then
            local account_id=$(get_environment_account_id "$env")
            local account_key=$(get_environment_account_key "$env")
            local region=$(get_environment_region "$env")
            if [ -n "$account_id" ]; then
                print_info "  $env: account=$account_key ($account_id), region=$region"
            else
                print_info "  $env: account=$account_key, region=$region"
            fi
        fi
    done < <(get_environment_names)
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
    
    local account_key
    account_key=$(get_environment_account_key "$env")
    if [ -z "$account_key" ]; then
        print_error "Missing account reference for environment: $env"
        return 1
    fi

    if yq -e '.accounts // false' "$INFRA_CONFIG_PATH" >/dev/null 2>&1 && ! yq -e ".accounts | has(\"${account_key}\")" "$INFRA_CONFIG_PATH" >/dev/null 2>&1; then
        print_error "Environment '$env' references unknown account '$account_key'"
        return 1
    fi
    
    local region=$(get_environment_region "$env")
    if [ "$region" = "null" ] || [ -z "$region" ]; then
        print_error "Missing region for environment: $env"
        return 1
    fi

    # Cost control validation (if enabled)
    if is_cost_control_enabled; then
        local budget_amount warning_percent hard_percent interval_mins breach_count cooldown_mins mode
        budget_amount=$(get_cost_control_value "budget.amount_usd")
        warning_percent=$(get_cost_control_value "budget.warning_percent")
        hard_percent=$(get_cost_control_value "budget.hard_percent")
        interval_mins=$(get_cost_control_value "monitor.interval_minutes")
        breach_count=$(get_cost_control_value "monitor.consecutive_breach_count")
        cooldown_mins=$(get_cost_control_value "monitor.cooldown_minutes")
        mode=$(get_cost_control_value "mode")

        if [ -z "$budget_amount" ] || [ "$budget_amount" = "null" ]; then
            print_error "Missing cost_control.budget.amount_usd"
            return 1
        fi
        if [ -z "$warning_percent" ] || [ "$warning_percent" = "null" ]; then
            print_error "Missing cost_control.budget.warning_percent"
            return 1
        fi
        if [ -z "$hard_percent" ] || [ "$hard_percent" = "null" ]; then
            print_error "Missing cost_control.budget.hard_percent"
            return 1
        fi
        if [ -z "$interval_mins" ] || [ "$interval_mins" = "null" ]; then
            print_error "Missing cost_control.monitor.interval_minutes"
            return 1
        fi
        if [ -z "$breach_count" ] || [ "$breach_count" = "null" ]; then
            print_error "Missing cost_control.monitor.consecutive_breach_count"
            return 1
        fi
        if [ -z "$cooldown_mins" ] || [ "$cooldown_mins" = "null" ]; then
            print_error "Missing cost_control.monitor.cooldown_minutes"
            return 1
        fi
        if [ "$mode" != "observe" ] && [ "$mode" != "enforce" ]; then
            print_error "Invalid cost_control.mode: $mode (expected observe or enforce)"
            return 1
        fi
    fi
    
    local account_id
    account_id=$(get_environment_account_id "$env")
    if [ -z "$account_id" ]; then
        print_warning "No account_id found for '$env'. Add it to $(get_accounts_secrets_file) before deploying."
    fi

    print_complete "Configuration validation successful"
    return 0
}
