#!/bin/bash

# Restore a previously enforced cost guard action for one resource.
#
# Usage:
#   ./scripts/cost/cost_guard_restore.sh --env dev --resource-id rag_lambda

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

ENVIRONMENT=""
RESOURCE_ID=""
INFRA_PATH="infra/infra.yaml"
TABLE_NAME=""

show_usage() {
    echo "Cost Guard Restore Script"
    echo ""
    echo "Usage: $0 --env <dev|staging|prod> --resource-id <id> [options]"
    echo ""
    echo "Options:"
    echo "  --env <env>             Environment"
    echo "  --resource-id <id>      Resource ID from cost_control.resources"
    echo "  --infra <path>          Path to infra.yaml (default: infra/infra.yaml)"
    echo "  --table-name <name>     Override DynamoDB table name"
    echo "  -h, --help              Show this help"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            ENVIRONMENT="$2"
            shift 2
            ;;
        --resource-id)
            RESOURCE_ID="$2"
            shift 2
            ;;
        --infra)
            INFRA_PATH="$2"
            shift 2
            ;;
        --table-name)
            TABLE_NAME="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

if [ -z "$ENVIRONMENT" ] || [ -z "$RESOURCE_ID" ]; then
    echo "Both --env and --resource-id are required."
    show_usage
    exit 1
fi

ARGS=(--env "$ENVIRONMENT" --resource-id "$RESOURCE_ID" --infra "$INFRA_PATH")
if [ -n "$TABLE_NAME" ]; then
    ARGS+=(--table-name "$TABLE_NAME")
fi

python3 scripts/cost/restore_cost_guard.py "${ARGS[@]}"

