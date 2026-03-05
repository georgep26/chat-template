#!/bin/bash
# Hydrate Configs - Injects secrets into templated config files
#
# Usage:
#   ./scripts/utils/hydrate_configs.sh <environment>
#   ./scripts/utils/hydrate_configs.sh dev
#   ./scripts/utils/hydrate_configs.sh --all   (local only: hydrate all envs)
#   ./scripts/utils/hydrate_configs.sh --restore   (reset to template versions)
#   ./scripts/utils/hydrate_configs.sh --run <environment> [missing_secrets_file] -- <command ...>
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
    echo "Usage:"
    echo "  $0 <environment> [missing_secrets_file]"
    echo "  $0 --run <environment> [missing_secrets_file] -- <command ...>"
    echo "  $0 --all"
    echo "  $0 --restore"
    echo ""
    echo "  <environment>        dev | staging | prod  (hydrate for this env)"
    echo "  missing_secrets_file optional path; keys hydrated with blank are written here (one per line)"
    echo "  --run                Hydrate, execute command, then restore original files"
    echo "  --all                (local only) Hydrate all environments from secrets files"
    echo "  --restore            Reset infra/infra.yaml and config/*/app_config.yaml to template versions"
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

# Record a secret key as missing (hydrated with blank) so the deploy script can warn.
record_missing() {
    local key=$1
    local missing_out="${2:-}"
    [[ -z "$missing_out" ]] && return 0
    echo "$key" >> "$missing_out"
}

hydrate_infra() {
    local project_root=$1
    local env=$2
    local missing_out="${3:-}"
    local infra_file="$project_root/infra/infra.yaml"

    if [[ ! -f "$infra_file" ]]; then
        print_error "infra.yaml not found: $infra_file"
        return 1
    fi

    check_yq_installed || return 1

    # Project-level (set once); hydrate with blank if missing and record for warning
    if [[ -n "${MANAGEMENT_ACCOUNT_ID:-}" ]]; then
        yq -i ".project.management_account_id = strenv(MANAGEMENT_ACCOUNT_ID)" "$infra_file"
    else
        yq -i '.project.management_account_id = ""' "$infra_file"
        record_missing "MANAGEMENT_ACCOUNT_ID" "$missing_out"
    fi
    if [[ -n "${REPO:-}" ]]; then
        yq -i ".github.github_repo = strenv(REPO)" "$infra_file"
    else
        yq -i '.github.github_repo = ""' "$infra_file"
        record_missing "REPO" "$missing_out"
    fi
    if [[ -n "${BUDGET_EMAIL:-}" ]]; then
        yq -i ".budgets.budget_email = strenv(BUDGET_EMAIL)" "$infra_file"
        yq -i ".cost_control.notifications.warning_email = strenv(BUDGET_EMAIL)" "$infra_file"
        yq -i ".cost_control.notifications.critical_email = strenv(BUDGET_EMAIL)" "$infra_file"
    else
        yq -i '.budgets.budget_email = ""' "$infra_file"
        yq -i '.cost_control.notifications.warning_email = ""' "$infra_file"
        yq -i '.cost_control.notifications.critical_email = ""' "$infra_file"
        record_missing "BUDGET_EMAIL" "$missing_out"
    fi

    # Current environment only
    if [[ -n "${ACCOUNT_ID:-}" ]]; then
        yq -i ".environments.${env}.account_id = strenv(ACCOUNT_ID)" "$infra_file"
    else
        yq -i ".environments.${env}.account_id = \"\"" "$infra_file"
        record_missing "ACCOUNT_ID" "$missing_out"
    fi
    if [[ -n "${EMAIL:-}" ]]; then
        yq -i ".environments.${env}.email = strenv(EMAIL)" "$infra_file"
    else
        yq -i ".environments.${env}.email = \"\"" "$infra_file"
        record_missing "EMAIL" "$missing_out"
    fi
    if [[ -n "${DEPLOYER_ROLE_ARN:-}" ]]; then
        yq -i ".environments.${env}.github_actions_deployer_role_arn = strenv(DEPLOYER_ROLE_ARN)" "$infra_file"
    else
        yq -i ".environments.${env}.github_actions_deployer_role_arn = \"\"" "$infra_file"
        record_missing "DEPLOYER_ROLE_ARN" "$missing_out"
    fi
    if [[ -n "${VPC_ID:-}" ]]; then
        yq -i ".environments.${env}.vpc_id = strenv(VPC_ID)" "$infra_file"
    else
        yq -i ".environments.${env}.vpc_id = \"\"" "$infra_file"
        record_missing "VPC_ID" "$missing_out"
    fi
    if [[ -n "${SUBNET_IDS:-}" ]]; then
        yq -i ".environments.${env}.subnet_ids = strenv(SUBNET_IDS)" "$infra_file"
    else
        yq -i ".environments.${env}.subnet_ids = \"\"" "$infra_file"
        record_missing "SUBNET_IDS" "$missing_out"
    fi
    if [[ -n "${DB_CLUSTER_ARN:-}" ]]; then
        yq -i ".environments.${env}.db_cluster_arn = strenv(DB_CLUSTER_ARN)" "$infra_file"
    else
        yq -i ".environments.${env}.db_cluster_arn = \"\"" "$infra_file"
        record_missing "DB_CLUSTER_ARN" "$missing_out"
    fi
    if [[ -n "${DB_CREDENTIALS_SECRET_ARN:-}" ]]; then
        yq -i ".environments.${env}.db_credentials_secret_arn = strenv(DB_CREDENTIALS_SECRET_ARN)" "$infra_file"
    else
        yq -i ".environments.${env}.db_credentials_secret_arn = \"\"" "$infra_file"
        record_missing "DB_CREDENTIALS_SECRET_ARN" "$missing_out"
    fi
    if [[ -n "${KNOWLEDGE_BASE_ID:-}" ]]; then
        yq -i ".environments.${env}.knowledge_base_id = strenv(KNOWLEDGE_BASE_ID)" "$infra_file"
    else
        yq -i ".environments.${env}.knowledge_base_id = \"\"" "$infra_file"
        record_missing "KNOWLEDGE_BASE_ID" "$missing_out"
    fi
    if [[ -n "${COGNITO_REGION:-}" ]]; then
        yq -i '.resources.rag_lambda.config.cognito_region = strenv(COGNITO_REGION)' "$infra_file"
    else
        yq -i '.resources.rag_lambda.config.cognito_region = ""' "$infra_file"
        record_missing "COGNITO_REGION" "$missing_out"
    fi
    if [[ -n "${COGNITO_USER_POOL_ID:-}" ]]; then
        yq -i '.resources.rag_lambda.config.cognito_user_pool_id = strenv(COGNITO_USER_POOL_ID)' "$infra_file"
    else
        yq -i '.resources.rag_lambda.config.cognito_user_pool_id = ""' "$infra_file"
        record_missing "COGNITO_USER_POOL_ID" "$missing_out"
    fi
    if [[ -n "${COGNITO_APP_CLIENT_ID:-}" ]]; then
        yq -i '.resources.rag_lambda.config.cognito_app_client_id = strenv(COGNITO_APP_CLIENT_ID)' "$infra_file"
    else
        yq -i '.resources.rag_lambda.config.cognito_app_client_id = ""' "$infra_file"
        record_missing "COGNITO_APP_CLIENT_ID" "$missing_out"
    fi
    # Optional Cognito deploy config overrides (keep defaults when omitted).
    if [[ -n "${COGNITO_CALLBACK_URLS:-}" ]]; then
        yq -i '.resources.cognito.config.callback_urls = strenv(COGNITO_CALLBACK_URLS)' "$infra_file"
    fi
    if [[ -n "${COGNITO_LOGOUT_URLS:-}" ]]; then
        yq -i '.resources.cognito.config.logout_urls = strenv(COGNITO_LOGOUT_URLS)' "$infra_file"
    fi
    if [[ -n "${COGNITO_DOMAIN_PREFIX:-}" ]]; then
        yq -i '.resources.cognito.config.domain_prefix = strenv(COGNITO_DOMAIN_PREFIX)' "$infra_file"
    fi
    if [[ -n "${COGNITO_GENERATE_CLIENT_SECRET:-}" ]]; then
        yq -i '.resources.cognito.config.generate_client_secret = strenv(COGNITO_GENERATE_CLIENT_SECRET)' "$infra_file"
    fi

    print_info "Hydrated infra/infra.yaml for environment: $env"
    return 0
}

# -----------------------------------------------------------------------------
# Hydrate app_config.yaml (sed text replacement)
# Use temp file instead of sed -i for portability (BSD/macOS sed -i is quirky).
# -----------------------------------------------------------------------------

do_sed_replace() {
    local placeholder=$1
    local replacement=$2
    local app_config_file=$3
    [[ -z "$app_config_file" ]] || [[ ! -f "$app_config_file" ]] && return 1
    local escaped
    escaped=$(sed_escape "$replacement")
    local tmp_file
    tmp_file=$(mktemp)
    sed -e "s|${placeholder}|${escaped}|g" "$app_config_file" > "$tmp_file" && mv "$tmp_file" "$app_config_file"
    rm -f "$tmp_file" 2>/dev/null || true
}

hydrate_app_config() {
    local app_config_file=$1
    local missing_out="${2:-}"

    if [[ -z "$app_config_file" ]]; then
        print_warning "Hydrate app_config: path is empty (skipping)"
        return 0
    fi
    if [[ ! -f "$app_config_file" ]]; then
        print_warning "App config not found: $app_config_file (skipping)"
        return 0
    fi

    if ! has_placeholders "$app_config_file"; then
        return 0
    fi

    # Replace each placeholder; use "" when secret is missing and record for warning
    # Pattern uses \$ so sed treats $ as literal
    if grep -q '${KNOWLEDGE_BASE_ID}' "$app_config_file" 2>/dev/null; then
        do_sed_replace '\${KNOWLEDGE_BASE_ID}' "${KNOWLEDGE_BASE_ID:-}" "$app_config_file"
        [[ -z "${KNOWLEDGE_BASE_ID:-}" ]] && record_missing "KNOWLEDGE_BASE_ID" "$missing_out"
    fi
    if grep -q '${DB_CLUSTER_ARN}' "$app_config_file" 2>/dev/null; then
        do_sed_replace '\${DB_CLUSTER_ARN}' "${DB_CLUSTER_ARN:-}" "$app_config_file"
        [[ -z "${DB_CLUSTER_ARN:-}" ]] && record_missing "DB_CLUSTER_ARN" "$missing_out"
    fi
    if grep -q '${DB_CREDENTIALS_SECRET_ARN}' "$app_config_file" 2>/dev/null; then
        do_sed_replace '\${DB_CREDENTIALS_SECRET_ARN}' "${DB_CREDENTIALS_SECRET_ARN:-}" "$app_config_file"
        [[ -z "${DB_CREDENTIALS_SECRET_ARN:-}" ]] && record_missing "DB_CREDENTIALS_SECRET_ARN" "$missing_out"
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
    local missing_out="${2:-}"  # optional: file to write missing secret keys (one per line)
    local project_root
    project_root=$(cd "$(get_project_root)" && pwd)

    if [[ "${HYDRATE_CONFIGS:-}" = "false" ]]; then
        print_info "HYDRATE_CONFIGS=false; skipping hydration"
        return 0
    fi

    local infra_file="$project_root/infra/infra.yaml"
    local app_config_file="$project_root/config/$env/app_config.yaml"

    if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
        local secrets_file="$project_root/infra/secrets/${env}_secrets.yaml"
        load_config_secrets_from_file "$secrets_file" || return 1
    fi

    # Clear missing-secrets report so caller can warn if any placeholders were hydrated with blank
    [[ -n "$missing_out" ]] && : > "$missing_out"

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
        hydrate_infra "$project_root" "$env" "$missing_out" || return 1
    fi
    if [[ -n "$app_config_file" ]] && [[ -f "$app_config_file" ]] && has_placeholders "$app_config_file"; then
        hydrate_app_config "$app_config_file" "$missing_out" || return 1
    fi

    return 0
}

# -----------------------------------------------------------------------------
# Runtime hydration wrapper (hydrate -> run command -> restore files)
# -----------------------------------------------------------------------------

RUNTIME_ACTIVE=false
RUNTIME_BACKUP_DIR=""
RUNTIME_PROJECT_ROOT=""
RUNTIME_ENV=""

runtime_backup_files() {
    local project_root=$1
    local env=$2
    local backup_dir
    backup_dir=$(mktemp -d)

    local infra_file="$project_root/infra/infra.yaml"
    local app_config_file="$project_root/config/$env/app_config.yaml"

    if [[ -f "$infra_file" ]]; then
        cp "$infra_file" "$backup_dir/infra.yaml"
    fi

    if [[ -f "$app_config_file" ]]; then
        cp "$app_config_file" "$backup_dir/app_config.yaml"
        echo "present" > "$backup_dir/app_config.state"
    else
        echo "missing" > "$backup_dir/app_config.state"
    fi

    echo "$backup_dir"
}

runtime_restore_files() {
    if [[ "$RUNTIME_ACTIVE" != "true" ]]; then
        return 0
    fi
    if [[ -z "$RUNTIME_BACKUP_DIR" ]] || [[ ! -d "$RUNTIME_BACKUP_DIR" ]]; then
        return 0
    fi

    local infra_file="$RUNTIME_PROJECT_ROOT/infra/infra.yaml"
    local app_config_file="$RUNTIME_PROJECT_ROOT/config/$RUNTIME_ENV/app_config.yaml"
    local app_state=""

    if [[ -f "$RUNTIME_BACKUP_DIR/infra.yaml" ]]; then
        cp "$RUNTIME_BACKUP_DIR/infra.yaml" "$infra_file"
    fi

    if [[ -f "$RUNTIME_BACKUP_DIR/app_config.state" ]]; then
        app_state=$(cat "$RUNTIME_BACKUP_DIR/app_config.state" 2>/dev/null || echo "")
    fi
    if [[ "$app_state" = "present" ]] && [[ -f "$RUNTIME_BACKUP_DIR/app_config.yaml" ]]; then
        cp "$RUNTIME_BACKUP_DIR/app_config.yaml" "$app_config_file"
    elif [[ "$app_state" = "missing" ]]; then
        rm -f "$app_config_file" 2>/dev/null || true
    fi

    rm -rf "$RUNTIME_BACKUP_DIR" 2>/dev/null || true
    RUNTIME_ACTIVE=false
}

run_hydrate_runtime() {
    local env=$1
    local missing_out="${2:-}"
    shift 2
    if [[ $# -eq 0 ]]; then
        print_error "--run requires a command after '--'"
        return 1
    fi

    if [[ "${HYDRATE_CONFIGS:-}" = "false" ]]; then
        print_info "HYDRATE_CONFIGS=false; running command without hydration"
        "$@"
        return $?
    fi

    local project_root
    project_root=$(cd "$(get_project_root)" && pwd)

    RUNTIME_PROJECT_ROOT="$project_root"
    RUNTIME_ENV="$env"
    RUNTIME_BACKUP_DIR=$(runtime_backup_files "$project_root" "$env") || return 1
    RUNTIME_ACTIVE=true
    trap runtime_restore_files EXIT

    run_hydrate "$env" "$missing_out" || return 1
    "$@"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
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
        --run)
            if [[ $# -lt 4 ]]; then
                print_error "--run requires: <environment> [missing_secrets_file] -- <command ...>"
                show_usage
                exit 1
            fi
            shift
            local env="$1"
            shift
            validate_environment "$env" || exit 1

            local missing_out=""
            if [[ "${1:-}" != "--" ]]; then
                missing_out="$1"
                shift
            fi

            if [[ "${1:-}" != "--" ]]; then
                print_error "--run requires '--' before command"
                show_usage
                exit 1
            fi
            shift

            if [[ $# -eq 0 ]]; then
                print_error "--run requires a command after '--'"
                show_usage
                exit 1
            fi

            if run_hydrate_runtime "$env" "$missing_out" "$@"; then
                print_complete "Hydration runtime command complete for $env"
                exit 0
            else
                exit $?
            fi
            ;;
        dev|staging|prod)
            validate_environment "$1" || exit 1
            run_hydrate "$1" "${2:-}" || exit 1
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
