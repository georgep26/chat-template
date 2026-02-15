#!/usr/bin/env bash
# Credential process script: assumes OrganizationAccountAccessRole in the target
# account, then assumes the project CLI role or deployer role. Outputs credentials
# in the format expected by AWS CLI credential_process.
#
# Usage: assume_role_for_cli.sh <environment> [role_type] [source_profile]
#   environment:    dev | staging | prod
#   role_type:      cli | deployer (default: cli)
#   source_profile: optional; profile that can assume OrganizationAccountAccessRole
#                   (default: <project>-management-admin, or "default")
#
# Called by AWS CLI when using a profile with credential_process.

set -euo pipefail

ENV="${1:?Usage: $0 <dev|staging|prod> [role_type] [source_profile]}"
ROLE_TYPE="${2:-cli}"
SOURCE_PROFILE="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INFRA_YAML="$PROJECT_ROOT/infra/infra.yaml"

if [[ ! -f "$INFRA_YAML" ]]; then
    echo "{\"Version\": 1, \"Error\": \"infra/infra.yaml not found at $INFRA_YAML\"}" >&2
    exit 1
fi

if ! command -v yq &>/dev/null; then
    echo "{\"Version\": 1, \"Error\": \"yq is required for assume_role_for_cli.sh\"}" >&2
    exit 1
fi

PROJECT_NAME="$(yq -r '.project.name' "$INFRA_YAML")"
ACCOUNT_ID_FROM_INFRA="$(yq -r ".environments.${ENV}.account_id" "$INFRA_YAML")"
REGION="$(yq -r ".environments.${ENV}.region // .project.default_region" "$INFRA_YAML")"
ORG_ROLE_NAME="$(yq -r ".environments.${ENV}.org_role_name // \"OrganizationAccountAccessRole\"" "$INFRA_YAML")"

# Resolve account_id: use infra value if concrete; else env var ACCOUNT_ID; else secrets file
if [[ "$ACCOUNT_ID_FROM_INFRA" != "null" && -n "$ACCOUNT_ID_FROM_INFRA" && "$ACCOUNT_ID_FROM_INFRA" != *'${'* ]]; then
    ACCOUNT_ID="$ACCOUNT_ID_FROM_INFRA"
else
    ACCOUNT_ID="${ACCOUNT_ID:-}"   # env var from caller if set
    if [[ -z "$ACCOUNT_ID" ]]; then
        SECRETS_FILE="$PROJECT_ROOT/infra/secrets/${ENV}_secrets.yaml"
        if [[ -f "$SECRETS_FILE" ]]; then
            ACCOUNT_ID="$(yq -r '.config_secrets.ACCOUNT_ID // ""' "$SECRETS_FILE" 2>/dev/null || echo "")"
        fi
    fi
    if [[ -z "$ACCOUNT_ID" || "$ACCOUNT_ID" == "null" ]]; then
        echo "{\"Version\": 1, \"Error\": \"No account_id for environment: $ENV. Set config_secrets.ACCOUNT_ID in infra/secrets/${ENV}_secrets.yaml or run ./scripts/utils/hydrate_configs.sh ${ENV} to hydrate infra.\"}" >&2
        exit 1
    fi
fi

case "$ROLE_TYPE" in
    cli)
        ROLE_NAME="$(yq -r ".environments.${ENV}.cli_role_name // \"${PROJECT_NAME}-${ENV}-admin-cli-role\"" "$INFRA_YAML")"
        [[ "$ROLE_NAME" == "null" ]] && ROLE_NAME="${PROJECT_NAME}-${ENV}-admin-cli-role"
        SESSION_SUFFIX="cli"
        ;;
    deployer)
        ROLE_NAME="${PROJECT_NAME}-${ENV}-deployer-github-actions-role"
        SESSION_SUFFIX="deployer"
        ;;
    *)
        echo "{\"Version\": 1, \"Error\": \"Invalid role_type: $ROLE_TYPE (use cli or deployer)\"}" >&2
        exit 1
        ;;
esac

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Use management profile if not specified
if [[ -z "$SOURCE_PROFILE" ]]; then
    SOURCE_PROFILE="${PROJECT_NAME}-management-admin"
fi

AWS_CMD=(aws --region "${REGION}")
if [[ -n "$SOURCE_PROFILE" && "$SOURCE_PROFILE" != "default" ]]; then
    AWS_CMD+=(--profile "$SOURCE_PROFILE")
fi

# Assume OrganizationAccountAccessRole in the member account
CREDS_ORG="$("${AWS_CMD[@]}" sts assume-role \
    --role-arn "arn:aws:iam::${ACCOUNT_ID}:role/${ORG_ROLE_NAME}" \
    --role-session-name "${SESSION_SUFFIX}-role-${ENV}" \
    --query 'Credentials' \
    --output json 2>/dev/null)" || {
    echo "{\"Version\": 1, \"Error\": \"Failed to assume org role in ${ENV} (account ${ACCOUNT_ID}). Ensure source profile has permission.\"}" >&2
    exit 1
}

export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
export AWS_SESSION_TOKEN
AWS_ACCESS_KEY_ID="$(echo "$CREDS_ORG" | yq -r '.AccessKeyId')"
AWS_SECRET_ACCESS_KEY="$(echo "$CREDS_ORG" | yq -r '.SecretAccessKey')"
AWS_SESSION_TOKEN="$(echo "$CREDS_ORG" | yq -r '.SessionToken')"

# Assume the target role (CLI or deployer) in the same account
CREDS_TARGET="$(aws sts assume-role \
    --role-arn "$ROLE_ARN" \
    --role-session-name "${SESSION_SUFFIX}-${ENV}" \
    --region "$REGION" \
    --query 'Credentials' \
    --output json 2>/dev/null)" || {
    echo "{\"Version\": 1, \"Error\": \"Failed to assume ${ROLE_TYPE} role ${ROLE_ARN}\"}" >&2
    exit 1
}

# Output credential_process format (Version 1). STS credential values are alphanumeric.
printf '{"Version":1,"AccessKeyId":"%s","SecretAccessKey":"%s","SessionToken":"%s"}\n' \
    "$(echo "$CREDS_TARGET" | yq -r '.AccessKeyId')" \
    "$(echo "$CREDS_TARGET" | yq -r '.SecretAccessKey')" \
    "$(echo "$CREDS_TARGET" | yq -r '.SessionToken')"
