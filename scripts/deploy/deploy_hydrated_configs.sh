#!/bin/bash
# Sync app config and upload it while hydrate_configs.sh --run is active.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -lt 1 ]; then
    echo "[ERROR] Environment is required" >&2
    exit 1
fi

ENVIRONMENT=$1

"$SCRIPT_DIR/sync_app_config.sh" --env "$ENVIRONMENT"
"$SCRIPT_DIR/deploy_configs.sh" "$ENVIRONMENT"
