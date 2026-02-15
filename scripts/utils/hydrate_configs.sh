#!/bin/bash
# Hydrate Configs - Injects secrets into templated config files
#
# Usage:
#   ./scripts/utils/hydrate_configs.sh <environment>
#   ./scripts/utils/hydrate_configs.sh dev
#   ./scripts/utils/hydrate_configs.sh --all   (local only: hydrate all envs)
#   ./scripts/utils/hydrate_configs.sh --restore   (reset to template versions)
#
# Modes:
#   CI (GITHUB_ACTIONS=true): reads secrets from environment variables
#   Local: reads from infra/secrets/<env>_secrets.yaml config_secrets section
#
# No-op when:
#   - HYDRATE_CONFIGS=false (explicit disable for private forks)
#   - No ${...} placeholders found in target files
#
# Requires: yq (https://github.com/mikefarah/yq)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# -----------------------------------------------------------------------------
# Usage
# -----------------------------------------------------------------------------

show_usage() {
    echo "Hydrate Configs - Injects secrets into templated config files"
    echo ""
    echo "Usage: $0 <environment> | --all | --restore"
    echo ""
    echo "  <environment>   dev | staging | prod  (hydrate for this env)"
    echo "  --all           (local only) Hydrate all environments from secrets files"
    echo "  --restore       Reset infra/infra.yaml and config/*/app_config.yaml to template versions"
    echo ""
    echo "No-op when HYDRATE_CONFIGS=false or when no placeholders are present (e.g. private fork)."
}

# -----------------------------------------------------------------------------
# Restore
# -----------------------------------------------------------------------------

do_restore() {
    local project_root
    project_root=$(get_project_root)
    print_step "Restoring templated config files..."
    (cd "$project_root" && git checkout -- infra/infra.yaml config/dev/app_config.yaml config/staging/app_config.yaml config/prod/app_config.yaml 2>/dev/null) || true
    print_complete "Restored template versions"
    return 0
}

# -----------------------------------------------------------------------------
# No-op detection
# -----------------------------------------------------------------------------

has_placeholders() {
    local file=$1
    grep -qE '\$\{[A-Z_]+\}' "$file" 2>/dev/null
}

# -----------------------------------------------------------------------------
# Load config_secrets from secrets file into environment
# -----------------------------------------------------------------------------

load_config_secrets_from_file() {
    local secrets_file=$1
    if [[ ! -f "$secrets_file" ]]; then
        print_error "Secrets file not found: $secrets_file"
        return 1
    fi
    if ! command -v yq &>/dev/null; then
        print_error "yq is required. Install: brew install yq"
        return 1
    fi
    if ! yq -e '.config_secrets | keys | length > 0' "$secrets_file" &>/dev/null; then
        print_warning "No config_secrets in $secrets_file"
        return 0
    fi
    local keys
    keys=$(yq -r '.config_secrets | keys[]' "$secrets_file" 2>/dev/null || true)
    while IFS= read -r key; do
        [[ -z "$key" ]] && continue
        local value
        value=$(yq -r ".config_secrets[\"$key\"]" "$secrets_file" 2>/dev/null || true)
        [[ "$value" = "null" ]] && value=""
        export "${key}=${value}"
    done <<< "$keys"
    return 0
}

# Escape a value for use in sed replacement (avoid & and \ issues)
sed_escape() {
    local v="$1"
    v="${v//\\/\\\\}"
    v="${v//&/\\&}"
    echo "$v"
}

# -----------------------------------------------------------------------------
# Hydrate infra.yaml (yq path-based for current env + project-level)
# -----------------------------------------------------------------------------

hydrate_infra() {
    local project_root=$1
    local env=$2
    local infra_file="$project_root/infra/infra.yaml"

    if [[ ! -f "$infra_file" ]]; then
        print_error "infra.yaml not found: $infra_file"
        return 1
    fi

    check_yq_installed || return 1

    # Project-level (set once)
    if [[ -n "${MANAGEMENT_ACCOUNT_ID:-}" ]]; then
        yq -i ".project.management_account_id = strenv(MANAGEMENT_ACCOUNT_ID)" "$infra_file"
    fi
    if [[ -n "${REPO:-}" ]]; then
        yq -i ".github.github_repo = strenv(REPO)" "$infra_file"
    fi
    if [[ -n "${BUDGET_EMAIL:-}" ]]; then
        yq -i ".budgets.budget_email = strenv(BUDGET_EMAIL)" "$infra_file"
    fi

    # Current environment only
    if [[ -n "${ACCOUNT_ID:-}" ]]; then
        yq -i ".environments.${env}.account_id = strenv(ACCOUNT_ID)" "$infra_file"
    fi
    if [[ -n "${EMAIL:-}" ]]; then
        yq -i ".environments.${env}.email = strenv(EMAIL)" "$infra_file"
    fi
    if [[ -n "${DEPLOYER_ROLE_ARN:-}" ]]; then
        yq -i ".environments.${env}.github_actions_deployer_role_arn = strenv(DEPLOYER_ROLE_ARN)" "$infra_file"
    fi
    if [[ -n "${VPC_ID:-}" ]]; then
        yq -i ".environments.${env}.vpc_id = strenv(VPC_ID)" "$infra_file"
    fi
    if [[ -n "${SUBNET_IDS:-}" ]]; then
        yq -i ".environments.${env}.subnet_ids = strenv(SUBNET_IDS)" "$infra_file"
    fi
    if [[ -n "${DB_CLUSTER_ARN:-}" ]]; then
        yq -i ".environments.${env}.db_cluster_arn = strenv(DB_CLUSTER_ARN)" "$infra_file"
    fi
    if [[ -n "${DB_CREDENTIALS_SECRET_ARN:-}" ]]; then
        yq -i ".environments.${env}.db_credentials_secret_arn = strenv(DB_CREDENTIALS_SECRET_ARN)" "$infra_file"
    fi
    if [[ -n "${KNOWLEDGE_BASE_ID:-}" ]]; then
        yq -i ".environments.${env}.knowledge_base_id = strenv(KNOWLEDGE_BASE_ID)" "$infra_file"
    fi

    print_info "Hydrated infra/infra.yaml for environment: $env"
    return 0
}

# -----------------------------------------------------------------------------
# Hydrate app_config.yaml (sed text replacement)
# -----------------------------------------------------------------------------

hydrate_app_config() {
    local app_config_file=$1

    if [[ ! -f "$app_config_file" ]]; then
        print_warning "App config not found: $app_config_file (skipping)"
        return 0
    fi

    local need_sed=0
    if has_placeholders "$app_config_file"; then
        need_sed=1
    fi
    if [[ $need_sed -eq 0 ]]; then
        return 0
    fi

    # Use | delimiter to avoid issues with / in ARNs
    if [[ -n "${KNOWLEDGE_BASE_ID:-}" ]]; then
        local escaped
        escaped=$(sed_escape "$KNOWLEDGE_BASE_ID")
        if [[ "$(uname -s)" = "Darwin" ]]; then
            sed -i '' "s|\${KNOWLEDGE_BASE_ID}|${escaped}|g" "$app_config_file"
        else
            sed -i "s|\${KNOWLEDGE_BASE_ID}|${escaped}|g" "$app_config_file"
        fi
    fi
    if [[ -n "${DB_CLUSTER_ARN:-}" ]]; then
        local escaped
        escaped=$(sed_escape "$DB_CLUSTER_ARN")
        if [[ "$(uname -s)" = "Darwin" ]]; then
            sed -i '' "s|\${DB_CLUSTER_ARN}|${escaped}|g" "$app_config_file"
        else
            sed -i "s|\${DB_CLUSTER_ARN}|${escaped}|g" "$app_config_file"
        fi
    fi
    if [[ -n "${DB_CREDENTIALS_SECRET_ARN:-}" ]]; then
        local escaped
        escaped=$(sed_escape "$DB_CREDENTIALS_SECRET_ARN")
        if [[ "$(uname -s)" = "Darwin" ]]; then
            sed -i '' "s|\${DB_CREDENTIALS_SECRET_ARN}|${escaped}|g" "$app_config_file"
        else
            sed -i "s|\${DB_CREDENTIALS_SECRET_ARN}|${escaped}|g" "$app_config_file"
        fi
    fi

    print_info "Hydrated $app_config_file"
    return 0
}

# -----------------------------------------------------------------------------
# Check yq (from config_parser)
# -----------------------------------------------------------------------------

check_yq_installed() {
    if ! command -v yq &>/dev/null; then
        print_error "yq is required but not installed."
        print_info "Install: brew install yq (macOS) or see https://github.com/mikefarah/yq"
        return 1
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Main hydrate for one environment
# -----------------------------------------------------------------------------

run_hydrate() {
    local env=$1
    local project_root
    project_root=$(get_project_root)

    local infra_file="$project_root/infra/infra.yaml"
    local app_config_file="$project_root/config/$env/app_config.yaml"

    if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
        local secrets_file="$project_root/infra/secrets/${env}_secrets.yaml"
        load_config_secrets_from_file "$secrets_file" || return 1
    fi

    local infra_has_placeholders=0
    local app_has_placeholders=0
    if has_placeholders "$infra_file"; then
        infra_has_placeholders=1
    fi
    if [[ -f "$app_config_file" ]] && has_placeholders "$app_config_file"; then
        app_has_placeholders=1
    fi

    if [[ $infra_has_placeholders -eq 0 ]] && [[ $app_has_placeholders -eq 0 ]]; then
        print_info "No placeholders found; skipping hydration (e.g. private fork with real values)"
        return 0
    fi

    if [[ $infra_has_placeholders -eq 1 ]]; then
        hydrate_infra "$project_root" "$env" || return 1
    fi
    if [[ $app_has_placeholders -eq 1 ]]; then
        hydrate_app_config "$app_config_file" || return 1
    fi

    return 0
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
    if [[ "${HYDRATE_CONFIGS:-}" = "false" ]]; then
        print_info "HYDRATE_CONFIGS=false; skipping hydration"
        return 0
    fi

    if [[ $# -lt 1 ]]; then
        print_error "Environment or --restore or --all required"
        show_usage
        exit 1
    fi

    case "$1" in
        -h|--help)
            show_usage
            exit 0
            ;;
        --restore)
            do_restore
            exit 0
            ;;
        --all)
            if [[ "${GITHUB_ACTIONS:-}" = "true" ]]; then
                print_error "--all is not supported in CI (deploy one environment per run)"
                exit 1
            fi
            for env in dev staging prod; do
                if [[ -f "$(get_project_root)/infra/secrets/${env}_secrets.yaml" ]]; then
                    print_step "Hydrating for $env..."
                    run_hydrate "$env" || exit 1
                fi
            done
            print_complete "Hydrated all environments"
            exit 0
            ;;
        dev|staging|prod)
            validate_environment "$1" || exit 1
            run_hydrate "$1" || exit 1
            print_complete "Hydration complete for $1"
            exit 0
            ;;
        *)
            print_error "Invalid argument: $1"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
