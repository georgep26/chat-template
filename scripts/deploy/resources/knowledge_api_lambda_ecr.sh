#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/resource_wrapper.sh"
export RESOURCE_NAME="knowledge_api_lambda_ecr"
run_legacy_resource_script "deploy_ecr_repo.sh" "$@"
