#!/bin/bash

# Sync App Config Script
# Pulls common settings from infra/infra.yaml into config/<env>/app_config.yaml for each environment.
# Updates: knowledge_base_id, db_cluster_arn, db_credentials_secret_arn (only when present in infra).
#
# Run after deploying DB or Knowledge Base so app configs stay in sync with infra.
#
# Usage Examples:
#   # Sync all environments that have an app config
#   ./scripts/deploy/sync_app_config.sh
#
#   # Sync only one environment
#   ./scripts/deploy/sync_app_config.sh --env dev
#   ./scripts/deploy/sync_app_config.sh --env staging
#
#   # Dry run (show what would be updated)
#   ./scripts/deploy/sync_app_config.sh --dry-run

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "Sync App Config Script"
    echo ""
    echo "Pulls knowledge_base_id, db_cluster_arn, and db_credentials_secret_arn from"
    echo "infra/infra.yaml into config/<env>/app_config.yaml for each environment."
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --env <env>     Sync only this environment (dev, staging, prod)"
    echo "  --config <path> Path to infra.yaml (default: infra/infra.yaml)"
    echo "  --dry-run       Print what would be updated without writing files"
    echo "  -h, --help      Show this help"
    echo ""
    echo "Examples:"
    echo "  $0                    # Sync all environments"
    echo "  $0 --env staging      # Sync only staging"
    echo "  $0 --dry-run          # Show diff without writing"
}

# =============================================================================
# Argument Parsing
# =============================================================================

ENV_FILTER=""
CONFIG_PATH=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --env)
            ENV_FILTER="$2"
            shift 2
            ;;
        --config)
            CONFIG_PATH="$2"
            shift 2
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

if [ -n "$ENV_FILTER" ] && [[ ! "$ENV_FILTER" =~ ^(dev|staging|prod)$ ]]; then
    print_error "Invalid --env: $ENV_FILTER (must be dev, staging, or prod)"
    exit 1
fi

# =============================================================================
# Load Config
# =============================================================================

PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

if [ -z "$CONFIG_PATH" ]; then
    CONFIG_PATH="$PROJECT_ROOT/infra/infra.yaml"
fi

if [ ! -f "$CONFIG_PATH" ]; then
    print_error "Infra config not found: $CONFIG_PATH"
    exit 1
fi

load_infra_config "$CONFIG_PATH" || exit 1
check_yq_installed || exit 1

# =============================================================================
# Sync One Environment
# =============================================================================

sync_env() {
    local env=$1
    local app_config="$PROJECT_ROOT/config/$env/app_config.yaml"

    if [ ! -f "$app_config" ]; then
        print_info "Skipping $env: config/$env/app_config.yaml not found"
        return 0
    fi

    local kb_id secret_arn db_arn
    kb_id=$(yq -r ".environments.${env}.knowledge_base_id // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    db_arn=$(yq -r ".environments.${env}.db_cluster_arn // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)
    secret_arn=$(yq -r ".environments.${env}.db_credentials_secret_arn // \"\"" "$INFRA_CONFIG_PATH" 2>/dev/null)

    # Normalize: yq -r can return "null" as string if key missing
    [ "$kb_id" = "null" ] && kb_id=""
    [ "$db_arn" = "null" ] && db_arn=""
    [ "$secret_arn" = "null" ] && secret_arn=""

    local updated=0

    if [ -n "$kb_id" ]; then
        if [ "$DRY_RUN" = true ]; then
            local current
            current=$(yq -r '.rag_chat.retrieval.knowledge_base_id // ""' "$app_config" 2>/dev/null)
            [ "$current" = "null" ] && current=""
            if [ "$current" != "$kb_id" ]; then
                print_info "[$env] rag_chat.retrieval.knowledge_base_id: \"$current\" -> \"$kb_id\""
                updated=$((updated + 1))
            fi
        else
            yq -i ".rag_chat.retrieval.knowledge_base_id = \"${kb_id}\"" "$app_config"
            print_info "[$env] Set rag_chat.retrieval.knowledge_base_id = $kb_id"
            updated=$((updated + 1))
        fi
    fi

    if [ -n "$db_arn" ]; then
        if [ "$DRY_RUN" = true ]; then
            local current
            current=$(yq -r '.rag_chat.chat_history_store.db_cluster_arn // ""' "$app_config" 2>/dev/null)
            [ "$current" = "null" ] && current=""
            if [ "$current" != "$db_arn" ]; then
                print_info "[$env] rag_chat.chat_history_store.db_cluster_arn: \"$current\" -> \"$db_arn\""
                updated=$((updated + 1))
            fi
        else
            yq -i ".rag_chat.chat_history_store.db_cluster_arn = \"${db_arn}\"" "$app_config"
            print_info "[$env] Set rag_chat.chat_history_store.db_cluster_arn"
            updated=$((updated + 1))
        fi
    fi

    if [ -n "$secret_arn" ]; then
        if [ "$DRY_RUN" = true ]; then
            local current
            current=$(yq -r '.rag_chat.chat_history_store.db_credentials_secret_arn // ""' "$app_config" 2>/dev/null)
            [ "$current" = "null" ] && current=""
            if [ "$current" != "$secret_arn" ]; then
                print_info "[$env] rag_chat.chat_history_store.db_credentials_secret_arn: ... -> (from infra)"
                updated=$((updated + 1))
            fi
        else
            yq -i ".rag_chat.chat_history_store.db_credentials_secret_arn = \"${secret_arn}\"" "$app_config"
            print_info "[$env] Set rag_chat.chat_history_store.db_credentials_secret_arn"
            updated=$((updated + 1))
        fi
    fi

    if [ $updated -eq 0 ] && [ "$DRY_RUN" = false ]; then
        print_info "[$env] No values in infra to sync (run deploy_chat_template_db.sh and deploy_knowledge_base.sh first)"
    elif [ $updated -eq 0 ] && [ "$DRY_RUN" = true ]; then
        print_info "[$env] No changes needed"
    fi

    return 0
}

# =============================================================================
# Main
# =============================================================================

print_header "Syncing app config from infra.yaml"

if [ -n "$ENV_FILTER" ]; then
    sync_env "$ENV_FILTER"
else
    for env in dev staging prod; do
        sync_env "$env"
    done
fi

if [ "$DRY_RUN" = true ]; then
    print_complete "Dry run complete (no files modified)"
else
    print_complete "App config sync complete"
fi
