#!/bin/bash

# GitHub Setup Script
# Combines branch protection setup and GitHub environment setup (create environments,
# deploy environment secrets from infra/secrets). By default creates environments and
# deploys secrets; use --skip-secrets to skip secret deployment. Reads settings from
# infra/infra.yaml (github.*) and infra/secrets for secrets. See .github/workflows/deploy.yml
# and docs/github_environment_secrets.md for required secret names.
#
# Usage:
#   ./scripts/setup/setup_github.sh
#   ./scripts/setup/setup_github.sh --repo owner/repo-name
#   ./scripts/setup/setup_github.sh dev                             # only ensure dev env and deploy dev secrets
#   ./scripts/setup/setup_github.sh dev staging                     # only dev and staging
#   ./scripts/setup/setup_github.sh --skip-secrets                  # do not deploy environment secrets
#   ./scripts/setup/setup_github.sh -y                              # skip confirmation prompts

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

# Solo mode: 1 = no reviewer required, 0 = require 1 approval (default)
# AUTO_YES: 1 = skip confirmation prompts
SOLO_MODE=0
AUTO_YES=0
SKIP_BRANCH_PROTECTION=0
SKIP_ENVIRONMENTS=0
DEPLOY_SECRETS=1        # default: deploy secrets from infra/secrets; use --skip-secrets to disable
DEPLOY_SECRETS_ENVS=()  # from --deploy-secrets; empty = all envs that have secrets files
SELECTED_ENVS=()        # from positional args (e.g. dev); when set, only these envs for ensure + deploy
SECRETS_DRY_RUN=0       # 1 = print what would be set, do not call gh

# Read github_repo and solo_mode from infra/infra.yaml (under github: key)
# Sets CONFIG_REPO and CONFIG_SOLO_MODE (0 or 1). Returns 0 if at least repo was found.
read_infra_config() {
    local project_root="$1"
    local infra_file="${project_root}/infra/infra.yaml"
    CONFIG_REPO=""
    CONFIG_SOLO_MODE=""

    [[ ! -f "$infra_file" ]] && return 1

    local repo_line
    repo_line=$(grep 'github_repo:' "$infra_file" | head -1)
    if [[ -n "$repo_line" ]]; then
        CONFIG_REPO=$(echo "$repo_line" | sed -E 's/.*github_repo:[[:space:]]*"?([^"]*)"?.*/\1/' | tr -d ' "')
    fi

    local solo_line
    solo_line=$(grep 'solo_mode:' "$infra_file" | head -1)
    if [[ -n "$solo_line" ]]; then
        local raw
        raw=$(echo "$solo_line" | sed -E 's/.*solo_mode:[[:space:]]*(true|false).*/\1/i' | tr -d ' ')
        if [[ "$(echo "$raw" | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
            CONFIG_SOLO_MODE="1"
        else
            CONFIG_SOLO_MODE="0"
        fi
    fi

    [[ -n "$CONFIG_REPO" ]]
}

# Default branch protection config (used when infra has no value for a key; infra overrides).
# required_approving_review_count is always set from solo_mode after merge.
get_protection_default_json() {
    local required_reviews=$1
    cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Run Tests"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": ${required_reviews},
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
EOF
}

# Output branch protection JSON: default config with github.branch_protection from infra
# overriding. Infra values take precedence; only missing keys come from defaults.
# required_approving_review_count is always set from solo_mode after merge.
get_protection_json() {
    local project_root="$1"
    local infra_file="${project_root}/infra/infra.yaml"
    local required_reviews=$([[ "$SOLO_MODE" -eq 1 ]] && echo "0" || echo "1")

    local default_json
    default_json=$(get_protection_default_json "$required_reviews")

    # If we have yq and infra, get github.branch_protection and deep-merge (infra overrides default).
    if command -v yq &> /dev/null && [[ -f "$infra_file" ]]; then
        if yq -e '.github.branch_protection' "$infra_file" &> /dev/null; then
            local infra_json
            infra_json=$(yq -o=json '.github.branch_protection' "$infra_file" 2>/dev/null)
            if [[ -n "$infra_json" ]] && [[ "$infra_json" != "null" ]]; then
                if command -v jq &> /dev/null; then
                    echo "$default_json" | jq --argjson infra "$infra_json" --argjson n "$required_reviews" '
                        def deep_merge(base; override):
                            if override == null then base
                            elif (base | type) != "object" or (override | type) != "object" then override
                            else (base | keys) + (override | keys) | unique as $keys |
                                {} | reduce $keys[] as $k (.; .[$k] = deep_merge(base[$k]; override[$k]))
                            end;
                        (deep_merge(.; $infra) | .required_pull_request_reviews.required_approving_review_count = $n)
                    '
                    return
                fi
                # No jq: use infra JSON only and inject required_approving_review_count (no merge).
                echo "$infra_json" | yq -o=json ".required_pull_request_reviews.required_approving_review_count = $required_reviews"
                return
            fi
        fi
    fi

    echo "$default_json"
}

show_usage() {
    echo "GitHub Setup Script"
    echo ""
    echo "Usage: $0 [options] [environment ...]"
    echo ""
    echo "Options:"
    echo "  --repo <owner/repo>       - GitHub repository (overrides infra/infra.yaml)"
    echo "  --solo                    - Solo mode: no reviewer required (overrides infra config)"
    echo "  --deploy-secrets [envs]   - Deploy secrets for specific envs only (default: all with secrets files)"
    echo "  --skip-secrets            - Do not deploy environment secrets (default is to deploy)"
    echo "  --dry-run                 - For secrets: print what would be set, do not call gh"
    echo "  --skip-branch-protection  - Do not set up branch protection on main"
    echo "  --skip-environments       - Do not create/ensure GitHub environments"
    echo "  -y, --yes                 - Non-interactive: skip confirmation prompts"
    echo "  --help, -h                - Show this help message"
    echo ""
    echo "Environment (positional): dev, staging, prod"
    echo "  No args: ensure all environments (dev, staging, prod) and deploy secrets for all with secrets files."
    echo "  With args: only ensure and deploy secrets for the listed environment(s). e.g. $0 dev"
    echo ""
    echo "Config: CLI args > infra/infra.yaml (github.github_repo, github.solo_mode, github.branch_protection)"
    echo ""
    echo "This script (default behavior):"
    echo "  1. Sets branch protection on main (unless --skip-branch-protection)"
    echo "  2. Ensures GitHub environment(s) (all or only those listed)"
    echo "  3. Deploys secrets from infra/secrets (use --skip-secrets to skip)"
    echo ""
    echo "Secrets (see .github/workflows/deploy.yml and docs/github_environment_secrets.md):"
    echo "  AWS_DEPLOYER_ROLE_ARN, AWS_REGION, S3_APP_CONFIG_URI, LOCAL_APP_CONFIG_PATH,"
    echo "  MASTER_DB_USERNAME, MASTER_DB_PASSWORD, VPC_ID, SUBNET_IDS, SECURITY_GROUP_IDS"
}

check_gh_cli() {
    if ! command -v gh &> /dev/null; then
        print_error "GitHub CLI (gh) is not installed."
        print_info "Please install it from: https://cli.github.com/"
        exit 1
    fi
    if ! gh auth status &> /dev/null; then
        print_error "GitHub CLI is not authenticated."
        print_info "Please run: gh auth login"
        exit 1
    fi
    print_info "GitHub CLI is installed and authenticated."
}

get_repository_from_git() {
    if command -v git &> /dev/null && git rev-parse --git-dir &> /dev/null; then
        local remote_url
        remote_url=$(git remote get-url origin 2>/dev/null || echo "")
        if [[ -n "$remote_url" ]]; then
            local repo
            repo=$(echo "$remote_url" | sed -E 's|.*github\.com[:/]([^/]+/[^/]+)(\.git)?$|\1|' | sed 's|\.git$||')
            if [[ -n "$repo" ]]; then
                echo "$repo"
                return 0
            fi
        fi
    fi
    return 1
}

parse_cli_args() {
    REPO_OVERRIDE=""
    SOLO_OVERRIDE=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --repo)
                REPO_OVERRIDE="$2"
                shift 2
                ;;
            --solo)
                SOLO_OVERRIDE="1"
                shift
                ;;
            --deploy-secrets)
                DEPLOY_SECRETS=1
                shift
                while [[ $# -gt 0 ]] && [[ "$1" != --* ]]; do
                    case $1 in
                        dev|staging|prod)
                            DEPLOY_SECRETS_ENVS+=("$1")
                            shift
                            ;;
                        *)
                            break
                            ;;
                    esac
                done
                ;;
            dev|staging|prod)
                SELECTED_ENVS+=("$1")
                shift
                ;;
            --skip-secrets)
                DEPLOY_SECRETS=0
                shift
                ;;
            --skip-branch-protection)
                SKIP_BRANCH_PROTECTION=1
                shift
                ;;
            --skip-environments)
                SKIP_ENVIRONMENTS=1
                shift
                ;;
            --dry-run)
                SECRETS_DRY_RUN=1
                shift
                ;;
            -y|--yes)
                AUTO_YES=1
                shift
                ;;
            --help|-h)
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
}

print_branch_protection_summary() {
    local repo=$1
    print_header "Branch protection summary"
    echo "  Repository:     ${repo}"
    echo "  Branch:        main"
    if [[ "$SOLO_MODE" -eq 1 ]]; then
        echo "  Mode:          Solo (PRs required, no reviewer approval)"
    else
        echo "  Mode:          Standard (PRs require 1 approval)"
    fi
    echo "  Rules:         Require PRs, up-to-date branch, 'Run Tests' check, conversation resolution"
    echo ""
}

setup_branch_protection() {
    local repo=$1
    local project_root=${2:-$(get_project_root)}

    print_step "Setting up branch protection for 'main' branch in ${repo}..."

    local existing=0
    if gh api "repos/${repo}/branches/main/protection" &> /dev/null; then
        existing=1
        print_warning "Branch protection already exists for 'main' branch."
    fi

    print_branch_protection_summary "$repo"

    if [[ "$AUTO_YES" -eq 0 ]]; then
        if [[ "$existing" -eq 1 ]]; then
            read -p "Do you want to update it? (y/N): " -r
        else
            read -p "Apply the above rules? (y/N): " -r
        fi
        echo
        if [[ ! "$REPLY" =~ ^[yY]([eE][sS])?$ ]]; then
            print_info "Skipping branch protection update. Exiting."
            exit 0
        fi
    fi

    get_protection_json "$project_root" | gh api "repos/${repo}/branches/main/protection" \
        --method PUT \
        --input - \
        --silent

    print_info "Branch protection rules configured successfully."
    return 0
}

# Ensure GitHub environments exist. Creates via API if missing.
# Usage: ensure_github_environments repo [env1 env2 ...]
# If no envs given, ensures dev, staging, prod.
ensure_github_environments() {
    local repo=$1
    shift
    local envs=("$@")
    if [[ ${#envs[@]} -eq 0 ]]; then
        envs=(dev staging prod)
    fi

    print_step "Ensuring GitHub environment(s): ${envs[*]}..."
    for env in "${envs[@]}"; do
        if gh api -X PUT "repos/${repo}/environments/${env}" --silent 2>/dev/null; then
            print_info "Environment '$env' exists or was created."
        else
            print_warning "Could not ensure environment '$env' (may already exist or need repo permissions)."
        fi
    done
    print_info "GitHub environment(s) check complete."
}

# Set one secret for an environment (or dry-run).
# Usage: set_github_secret env name value [repo]
# If repo is set, uses --repo so gh targets the correct repository.
set_github_secret() {
    local env="$1"
    local name="$2"
    local value="$3"
    local repo="${4:-}"
    if [[ -z "$name" ]] || [[ -z "$env" ]]; then
        return 1
    fi
    if [[ "$SECRETS_DRY_RUN" -eq 1 ]]; then
        if [[ -n "$value" ]]; then
            print_info "[$env] Would set $name (value length ${#value})"
        else
            print_warning "[$env] Would set $name but value is empty"
        fi
        return 0
    fi
    if [[ -z "$value" ]]; then
        print_warning "[$env] Skipping empty secret: $name"
        return 0
    fi
    local gh_err gh_ret
    if [[ -n "$repo" ]]; then
        gh_err=$(printf '%s' "$value" | gh secret set "$name" --env "$env" --repo "$repo" 2>&1)
    else
        gh_err=$(printf '%s' "$value" | gh secret set "$name" --env "$env" 2>&1)
    fi
    gh_ret=$?
    if [[ $gh_ret -eq 0 ]]; then
        print_info "[$env] Set secret: $name"
    else
        print_error "[$env] Failed to set secret: $name"
        [[ -n "$gh_err" ]] && print_error "$gh_err"
        return 1
    fi
}

# Read YAML value (raw string); handles empty and null.
yq_raw() {
    local file="$1"
    local path="$2"
    local v
    v=$(yq -r "$path" "$file" 2>/dev/null || true)
    if [[ "$v" = "null" ]] || [[ -z "$v" ]]; then
        echo ""
        return
    fi
    echo "$v"
}

# Deploy secrets from one env secrets file to one GitHub environment.
# Usage: deploy_env_secrets env secrets_file repo
deploy_env_secrets() {
    local env="$1"
    local secrets_file="$2"
    local repo="${3:-}"
    local failed=0

    if [[ ! -f "$secrets_file" ]]; then
        print_error "Secrets file not found: $secrets_file"
        return 1
    fi

    # 1) Sync github_environment_secrets map (exact names for workflows)
    if yq -e '.github_environment_secrets | keys | length > 0' "$secrets_file" &>/dev/null; then
        local keys
        keys=$(yq -r '.github_environment_secrets | keys[]' "$secrets_file" 2>/dev/null || true)
        while IFS= read -r key; do
            [[ -z "$key" ]] && continue
            local value
            value=$(yq -r ".github_environment_secrets[\"$key\"]" "$secrets_file" 2>/dev/null || true)
            [[ "$value" = "null" ]] && value=""
            if ! set_github_secret "$env" "$key" "$value" "$repo"; then
                failed=$((failed + 1))
            fi
        done <<< "$keys"
    fi

    # 2) Map database.* to MASTER_DB_* if not already in github_environment_secrets
    for yaml_path in "database.master_username" "database.master_password"; do
        case "$yaml_path" in
            database.master_username) gh_name="MASTER_DB_USERNAME" ;;
            database.master_password) gh_name="MASTER_DB_PASSWORD" ;;
            *) continue ;;
        esac
        if ! yq -e ".github_environment_secrets.${gh_name}" "$secrets_file" &>/dev/null; then
            value=$(yq_raw "$secrets_file" ".$yaml_path")
            if [[ -n "$value" ]] && ! set_github_secret "$env" "$gh_name" "$value" "$repo"; then
                failed=$((failed + 1))
            fi
        fi
    done

    [[ $failed -gt 0 ]] && return 1
    return 0
}

# Resolve which environments to deploy secrets for; sets DEPLOY_SECRETS_ENVS_RESOLVED (array).
# If SELECTED_ENVS is set (positional args), use only those; else use DEPLOY_SECRETS_ENVS if set; else all with secrets files.
resolve_secrets_environments() {
    local project_root="$1"
    DEPLOY_SECRETS_ENVS_RESOLVED=()
    # Positional args: only these envs (must have secrets file)
    if [[ ${#SELECTED_ENVS[@]} -gt 0 ]]; then
        load_infra_config "${project_root}/infra/infra.yaml" || return 1
        for env in "${SELECTED_ENVS[@]}"; do
            local secrets_file
            secrets_file=$(get_environment_secrets_file "$env" 2>/dev/null)
            if [[ -f "$secrets_file" ]]; then
                DEPLOY_SECRETS_ENVS_RESOLVED+=("$env")
            else
                print_error "Secrets file not found for $env: $secrets_file"
                return 1
            fi
        done
        return 0
    fi
    # --deploy-secrets env list
    if [[ ${#DEPLOY_SECRETS_ENVS[@]} -gt 0 ]]; then
        DEPLOY_SECRETS_ENVS_RESOLVED=("${DEPLOY_SECRETS_ENVS[@]}")
        return 0
    fi
    # Default: all envs that exist in config and have a secrets file
    load_infra_config "${project_root}/infra/infra.yaml" || return 1
    for env in dev staging prod; do
        if environment_exists "$env" 2>/dev/null; then
            local secrets_file
            secrets_file=$(get_environment_secrets_file "$env" 2>/dev/null)
            if [[ -f "$secrets_file" ]]; then
                DEPLOY_SECRETS_ENVS_RESOLVED+=("$env")
            else
                print_warning "Secrets file not found for $env: $secrets_file (skipping)"
            fi
        fi
    done
    if [[ ${#DEPLOY_SECRETS_ENVS_RESOLVED[@]} -eq 0 ]]; then
        print_error "No environment secrets files found. Create e.g. infra/secrets/dev_secrets.yaml from infra/secrets/template.secrets.yaml"
        return 1
    fi
    return 0
}

# Deploy secrets from infra/secrets to GitHub environment secrets (inline logic).
# Usage: run_deploy_github_secrets project_root repo
run_deploy_github_secrets() {
    local project_root="$1"
    local repo="${2:-}"
    if ! command -v yq &> /dev/null; then
        print_error "yq is required for deploying secrets. Install: brew install yq"
        return 1
    fi
    check_yq_installed || return 1
    if ! resolve_secrets_environments "$project_root"; then
        return 1
    fi

    if [[ "$SECRETS_DRY_RUN" -eq 0 ]] && [[ "$AUTO_YES" -eq 0 ]]; then
        echo ""
        print_step "Summary: Deploy secrets to GitHub environment(s): ${DEPLOY_SECRETS_ENVS_RESOLVED[*]}"
        print_info "Environments: ${DEPLOY_SECRETS_ENVS_RESOLVED[*]}"
        if [[ -f "$SCRIPT_DIR/../utils/deploy_summary.sh" ]]; then
            source "$SCRIPT_DIR/../utils/deploy_summary.sh"
            confirm_deployment "Proceed with deploying secrets to GitHub?" || exit 0
        fi
    fi

    if [[ "$SECRETS_DRY_RUN" -eq 1 ]]; then
        print_warning "Dry run: no secrets will be set"
    fi

    print_step "Deploying environment secrets to GitHub..."
    local failed_envs=()
    for env in "${DEPLOY_SECRETS_ENVS_RESOLVED[@]}"; do
        local secrets_file
        secrets_file=$(get_environment_secrets_file "$env")
        print_info "Environment: $env ($secrets_file)"
        if deploy_env_secrets "$env" "$secrets_file" "$repo"; then
            print_info "Done: $env"
        else
            failed_envs+=("$env")
        fi
    done

    if [[ ${#failed_envs[@]} -gt 0 ]]; then
        print_error "Failed environments: ${failed_envs[*]}"
        return 1
    fi
    print_info "Environment secrets deployed successfully."
    return 0
}

main() {
    parse_cli_args "$@"

    print_header "GitHub setup"

    local project_root
    project_root=$(get_project_root)
    read_infra_config "$project_root" || true

    local repo=""
    if [[ -n "$REPO_OVERRIDE" ]]; then
        repo="$REPO_OVERRIDE"
        print_info "Repository from CLI: ${repo}"
    elif [[ -n "$CONFIG_REPO" ]]; then
        repo="$CONFIG_REPO"
        print_info "Repository from infra/infra.yaml: ${repo}"
    elif repo=$(get_repository_from_git); then
        print_info "Repository from git remote: ${repo}"
    else
        print_error "Could not determine repository."
        print_info "Add github.github_repo to infra/infra.yaml or use: $0 --repo owner/repo-name"
        exit 1
    fi

    if [[ -n "$SOLO_OVERRIDE" ]]; then
        SOLO_MODE=$SOLO_OVERRIDE
    elif [[ -n "$CONFIG_SOLO_MODE" ]]; then
        SOLO_MODE=$CONFIG_SOLO_MODE
    else
        SOLO_MODE=0
    fi

    check_gh_cli

    if [[ "$SKIP_BRANCH_PROTECTION" -eq 0 ]]; then
        setup_branch_protection "$repo" "$project_root"
    else
        print_info "Skipping branch protection (--skip-branch-protection)."
    fi

    if [[ "$SKIP_ENVIRONMENTS" -eq 0 ]]; then
        if [[ ${#SELECTED_ENVS[@]} -gt 0 ]]; then
            ensure_github_environments "$repo" "${SELECTED_ENVS[@]}"
        else
            ensure_github_environments "$repo"
        fi
    else
        print_info "Skipping environments (--skip-environments)."
    fi

    if [[ "$DEPLOY_SECRETS" -eq 1 ]]; then
        run_deploy_github_secrets "$project_root" "$repo" || exit 1
    fi

    print_step "GitHub setup complete!"
    print_info "Summary:"
    [[ "$SKIP_BRANCH_PROTECTION" -eq 0 ]] && print_info "  Branch protection on main configured"
    if [[ "$SKIP_ENVIRONMENTS" -eq 0 ]]; then
        if [[ ${#SELECTED_ENVS[@]} -gt 0 ]]; then
            print_info "  Environment(s) ensured: ${SELECTED_ENVS[*]}"
        else
            print_info "  Environments dev, staging, prod ensured"
        fi
    fi
    [[ "$DEPLOY_SECRETS" -eq 1 ]] && print_info "  Environment secrets deployed from infra/secrets"
}

main "$@"
