#!/usr/bin/env bash
# Credential process script: assumes OrganizationAccountAccessRole in the target
# account, then assumes the project CLI role or deployer role. Outputs credentials
# in the format expected by AWS CLI credential_process.
#
# Usage: assume_role_for_cli.sh <environment> [role_type] [source_profile]
#   environment:    any environment defined in infra/infra.yaml
#   role_type:      cli | deployer (default: cli)
#   source_profile: optional; profile that can assume OrganizationAccountAccessRole
#                   (default: <project>-management-admin, or "default")
#
# Called by AWS CLI when using a profile with credential_process.

set -euo pipefail

ENV="${1:?Usage: $0 <environment> [role_type] [source_profile]}"
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
REGION="$(ENV_NAME="$ENV" yq -r '.environments[strenv(ENV_NAME)].region // .project.default_region' "$INFRA_YAML")"
ACCOUNT_KEY="$(ENV_NAME="$ENV" yq -r '.environments[strenv(ENV_NAME)].account // ""' "$INFRA_YAML")"
if [[ -z "$ACCOUNT_KEY" || "$ACCOUNT_KEY" == "null" ]]; then
    echo "{\"Version\": 1, \"Error\": \"Unknown environment or missing account mapping: $ENV\"}" >&2
    exit 1
fi

ACCOUNT_KIND="$(ACCOUNT_KEY="$ACCOUNT_KEY" yq -r '.accounts[strenv(ACCOUNT_KEY)].kind // ""' "$INFRA_YAML")"
SECRET_REF="$(ACCOUNT_KEY="$ACCOUNT_KEY" yq -r '.accounts[strenv(ACCOUNT_KEY)].secret_ref // strenv(ACCOUNT_KEY)' "$INFRA_YAML")"
if [[ -n "${CHAT_TEMPLATE_ACCOUNTS_SECRETS_FILE:-}" ]]; then
    ACCOUNTS_SECRETS_FILE="$CHAT_TEMPLATE_ACCOUNTS_SECRETS_FILE"
else
    CONFIGURED_ACCOUNTS_FILE="$(yq -r '.secrets.accounts_file // ""' "$INFRA_YAML")"
    if [[ -n "$CONFIGURED_ACCOUNTS_FILE" && "$CONFIGURED_ACCOUNTS_FILE" != "null" ]]; then
        case "$CONFIGURED_ACCOUNTS_FILE" in
            /*) ACCOUNTS_SECRETS_FILE="$CONFIGURED_ACCOUNTS_FILE" ;;
            *) ACCOUNTS_SECRETS_FILE="$PROJECT_ROOT/$CONFIGURED_ACCOUNTS_FILE" ;;
        esac
    else
        ACCOUNTS_SECRETS_FILE="$PROJECT_ROOT/infra/secrets/accounts.secrets.yaml"
    fi
fi
if [[ ! -f "$ACCOUNTS_SECRETS_FILE" ]]; then
    echo "{\"Version\": 1, \"Error\": \"Missing account secrets file: $ACCOUNTS_SECRETS_FILE. Create it from infra/secrets/template.accounts.secrets.yaml and fill it in.\"}" >&2
    exit 1
fi

ACCOUNT_ID="$(SECRET_REF="$SECRET_REF" yq -r '.accounts[strenv(SECRET_REF)].account_id // ""' "$ACCOUNTS_SECRETS_FILE")"
if [[ -z "$ACCOUNT_ID" || "$ACCOUNT_ID" == "null" ]]; then
    echo "{\"Version\": 1, \"Error\": \"No account_id for environment: $ENV. Set accounts.${SECRET_REF}.account_id in $ACCOUNTS_SECRETS_FILE.\"}" >&2
    exit 1
fi
ORG_ROLE_NAME="$(SECRET_REF="$SECRET_REF" yq -r '.accounts[strenv(SECRET_REF)].organization_access_role // "OrganizationAccountAccessRole"' "$ACCOUNTS_SECRETS_FILE")"

case "$ROLE_TYPE" in
    cli)
        ROLE_NAME="$(SECRET_REF="$SECRET_REF" yq -r '.accounts[strenv(SECRET_REF)].cli_role_name // ""' "$ACCOUNTS_SECRETS_FILE")"
        [[ "$ROLE_NAME" == "null" ]] && ROLE_NAME="${PROJECT_NAME}-${ENV}-admin-cli-role"
        [[ -z "$ROLE_NAME" ]] && ROLE_NAME="${PROJECT_NAME}-${ENV}-admin-cli-role"
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
    SOURCE_PROFILE="$(SECRET_REF="$SECRET_REF" yq -r '.accounts[strenv(SECRET_REF)].bootstrap_profile // ""' "$ACCOUNTS_SECRETS_FILE")"
    [[ -z "$SOURCE_PROFILE" || "$SOURCE_PROFILE" == "null" ]] && SOURCE_PROFILE="${PROJECT_NAME}-management-admin"
fi

AWS_CMD=(aws --region "${REGION}")
if [[ -n "$SOURCE_PROFILE" && "$SOURCE_PROFILE" != "default" ]]; then
    AWS_CMD+=(--profile "$SOURCE_PROFILE")
fi

if [[ "$ACCOUNT_KIND" != "management" ]]; then
    # Assume OrganizationAccountAccessRole in the member account first.
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
fi

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
