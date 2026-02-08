#!/bin/bash
# Deploy secrets from infra/secrets/{env}_secrets.yaml to GitHub environment secrets
# using the GitHub CLI (gh). Ensures deploy and run-evals workflows have the
# required environment secrets (see docs/github_environment_secrets.md).
#
# Prerequisites:
#   - gh CLI installed and authenticated (gh auth login)
#   - yq (mikefarah/yq) installed
#   - GitHub environments (dev, staging, prod) created in the repo
#
# Usage:
#   ./scripts/deploy/deploy_github_secrets.sh [environment] [options]
#
# Examples:
#   # Deploy secrets for all environments that have a secrets file
#   ./scripts/deploy/deploy_github_secrets.sh
#
#   # Deploy secrets for dev only
#   ./scripts/deploy/deploy_github_secrets.sh dev
#
#   # Dry run (print what would be set, do not call gh)
#   ./scripts/deploy/deploy_github_secrets.sh --dry-run

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

# -----------------------------------------------------------------------------
# Defaults and options
# -----------------------------------------------------------------------------
DRY_RUN=false
AUTO_CONFIRM=false
ENVIRONMENTS=()

# -----------------------------------------------------------------------------
# Usage
# -----------------------------------------------------------------------------
usage() {
    cat << EOF
Usage: $0 [environment] [options]

Deploy secrets from infra/secrets/ to GitHub environment secrets via gh CLI.

Environments (from infra/infra.yaml):
  dev       - Development
  staging   - Staging (also used by run-evals workflow)
  prod      - Production

Options:
  --dry-run   Print which secrets would be set; do not call gh
  -y, --yes   Skip confirmation prompt
  -h, --help  Show this help

Secrets files (one per environment):
  infra/secrets/dev_secrets.yaml
  infra/secrets/staging_secrets.yaml
  infra/secrets/prod_secrets.yaml

Each file should include a 'github_environment_secrets' map with names expected
by the workflows (see docs/github_environment_secrets.md), e.g.:
  github_environment_secrets:
    AWS_DEPLOYER_ROLE_ARN: "arn:aws:iam::..."
    MASTER_DB_USERNAME: "postgres"
    MASTER_DB_PASSWORD: "..."
    S3_APP_CONFIG_URI: "s3://bucket/key"
  # Optional: database.* and api_keys.* are for local use; MASTER_DB_* can
  # also be set from database.master_username / database.master_password.

Workflow secret names (deploy.yml, run-evals.yml):
  AWS_DEPLOYER_ROLE_ARN, AWS_EVALS_ROLE_ARN (staging), AWS_REGION,
  S3_APP_CONFIG_URI, LOCAL_APP_CONFIG_PATH, MASTER_DB_USERNAME, MASTER_DB_PASSWORD,
  VPC_ID, SUBNET_IDS, SECURITY_GROUP_IDS
EOF
}

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        dev|staging|prod)
            ENVIRONMENTS+=("$1")
            shift
            ;;
        *)
            print_error "Unknown option or environment: $1"
            usage
            exit 1
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Prereqs: gh and yq (and load config before resolving default envs)
# -----------------------------------------------------------------------------
check_yq_installed || exit 1
load_infra_config "$REPO_ROOT/infra/infra.yaml" || exit 1

# If no environments specified, use all that exist in config and have a secrets file
if [ ${#ENVIRONMENTS[@]} -eq 0 ]; then
    for env in dev staging prod; do
        if environment_exists "$env" 2>/dev/null; then
            secrets_file=$(get_environment_secrets_file "$env" 2>/dev/null)
            if [ -f "$secrets_file" ]; then
                ENVIRONMENTS+=("$env")
            else
                print_warning "Secrets file not found for $env: $secrets_file (skipping)"
            fi
        fi
    done
    if [ ${#ENVIRONMENTS[@]} -eq 0 ]; then
        print_error "No environment secrets files found. Create e.g. infra/secrets/dev_secrets.yaml from infra/secrets/template.secrets.yaml"
        exit 1
    fi
fi

if ! command -v gh &>/dev/null; then
    print_error "gh (GitHub CLI) is required. Install: https://cli.github.com/"
    exit 1
fi

if ! gh auth status &>/dev/null; then
    print_error "Not logged in to GitHub. Run: gh auth login"
    exit 1
fi

# Summary and confirmation (unless dry-run or -y)
if [ "$DRY_RUN" = false ] && [ "$AUTO_CONFIRM" = false ]; then
    echo ""
    print_step "Summary: Deploy secrets to GitHub environment(s): ${ENVIRONMENTS[*]}"
    print_info "Environments: ${ENVIRONMENTS[*]}"
    source "$SCRIPT_DIR/../utils/deploy_summary.sh"
    confirm_deployment "Proceed with deploying secrets to GitHub?" || exit 0
fi

# -----------------------------------------------------------------------------
# Set one secret for an environment (or dry-run)
# -----------------------------------------------------------------------------
set_github_secret() {
    local env="$1"
    local name="$2"
    local value="$3"
    if [ -z "$name" ] || [ -z "$env" ]; then
        return 1
    fi
    if [ "$DRY_RUN" = true ]; then
        if [ -n "$value" ]; then
            print_info "[$env] Would set $name (value length ${#value})"
        else
            print_warning "[$env] Would set $name but value is empty"
        fi
        return 0
    fi
    if [ -z "$value" ]; then
        print_warning "[$env] Skipping empty secret: $name"
        return 0
    fi
    if printf '%s' "$value" | gh secret set "$name" --env "$env" 2>/dev/null; then
        print_info "[$env] Set secret: $name"
    else
        print_error "[$env] Failed to set secret: $name"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# Read YAML value (raw string); handles empty and null
# -----------------------------------------------------------------------------
yq_raw() {
    local file="$1"
    local path="$2"
    local v
    v=$(yq -r "$path" "$file" 2>/dev/null || true)
    if [ "$v" = "null" ] || [ -z "$v" ]; then
        echo ""
        return
    fi
    echo "$v"
}

# -----------------------------------------------------------------------------
# Deploy secrets from one env secrets file to one GitHub environment
# -----------------------------------------------------------------------------
deploy_env_secrets() {
    local env="$1"
    local secrets_file="$2"
    local count=0
    local failed=0

    if [ ! -f "$secrets_file" ]; then
        print_error "Secrets file not found: $secrets_file"
        return 1
    fi

    # 1) Sync github_environment_secrets map (exact names for workflows)
    if yq -e '.github_environment_secrets | keys | length > 0' "$secrets_file" &>/dev/null; then
        local keys
        keys=$(yq -r '.github_environment_secrets | keys[]' "$secrets_file" 2>/dev/null || true)
        while IFS= read -r key; do
            [ -z "$key" ] && continue
            # Use @json to preserve value then strip quotes for simple strings; for multiline, yq -r can output literal
            value=$(yq -r ".github_environment_secrets[\"$key\"]" "$secrets_file" 2>/dev/null || true)
            if [ "$value" = "null" ]; then
                value=""
            fi
            if set_github_secret "$env" "$key" "$value"; then
                count=$((count + 1))
            else
                failed=$((failed + 1))
            fi
        done <<< "$keys"
    fi

    # 2) Map template keys to workflow secret names (if not already set by github_environment_secrets)
    # database.master_username -> MASTER_DB_USERNAME, database.master_password -> MASTER_DB_PASSWORD
    for yaml_path in "database.master_username" "database.master_password"; do
        case "$yaml_path" in
            database.master_username) gh_name="MASTER_DB_USERNAME" ;;
            database.master_password) gh_name="MASTER_DB_PASSWORD" ;;
            *) continue ;;
        esac
        # Only set if we didn't already set it from github_environment_secrets
        if ! yq -e ".github_environment_secrets.${gh_name}" "$secrets_file" &>/dev/null; then
            value=$(yq_raw "$secrets_file" ".$yaml_path")
            if [ -n "$value" ]; then
                if set_github_secret "$env" "$gh_name" "$value"; then
                    count=$((count + 1))
                else
                    failed=$((failed + 1))
                fi
            fi
        fi
    done

    if [ $failed -gt 0 ]; then
        return 1
    fi
    return 0
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
print_header "Deploy GitHub environment secrets"
if [ "$DRY_RUN" = true ]; then
    print_warning "Dry run: no secrets will be set"
fi

failed_envs=()
for env in "${ENVIRONMENTS[@]}"; do
    secrets_file=$(get_environment_secrets_file "$env")
    print_step "Environment: $env (${secrets_file})"
    if deploy_env_secrets "$env" "$secrets_file"; then
        print_info "Done: $env"
    else
        failed_envs+=("$env")
    fi
done

if [ ${#failed_envs[@]} -gt 0 ]; then
    print_error "Failed environments: ${failed_envs[*]}"
    exit 1
fi

print_info "All selected environments updated."
