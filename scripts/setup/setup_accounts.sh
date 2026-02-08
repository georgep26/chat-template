#!/usr/bin/env bash

# AWS Organizations Account Setup Script
# Creates three member accounts (dev, staging, prod) under the management account using
# AWS Organizations. Optionally creates per-account budgets with email alerts.
# Reads defaults from infra/infra.yaml; CLI arguments override. Writes account info to infra/infra.yaml.
# Must be run from the management account with Organizations permissions.
#
# See docs/aws_organizations.md for background on Organizations and consolidated billing.
#
# Usage Examples:
#   # Create accounts using defaults from infra/infra.yaml
#   ./scripts/setup/setup_accounts.sh
#
#   # Override project name and emails via CLI
#   ./scripts/setup/setup_accounts.sh --project-name myapp \
#     --dev-email myapp+dev@example.com \
#     --staging-email myapp+staging@example.com \
#     --prod-email myapp+prod@example.com
#
#   # Enable budget alerts (from infra.yaml budgets.budget_email or CLI)
#   ./scripts/setup/setup_accounts.sh --budget-alert-email you@yourdomain.com
#
#   # Also write a JSON file
#   ./scripts/setup/setup_accounts.sh --out-json my-accounts.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        print_error "Missing required command: $1"
        exit 1
    fi
}

show_usage() {
    echo "AWS Organizations Account Setup Script"
    echo ""
    echo "Creates three member accounts (dev, staging, prod) under the management account."
    echo "Reads defaults from infra/infra.yaml (project, environments.*.email, budgets, org role)."
    echo "CLI options override infra.yaml. Writes account IDs and names to infra/infra.yaml."
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --project-name <name>         - Project name for account names (overrides infra.yaml)"
    echo "  --dev-email <email>           - Email for dev account"
    echo "  --staging-email <email>       - Email for staging account"
    echo "  --prod-email <email>           - Email for prod account"
    echo "  --budget-alert-email <email>  - Enable budgets and send alerts to this email"
    echo "  --dev-budget-usd <amount>     - Monthly budget limit for dev"
    echo "  --staging-budget-usd <amount> - Monthly budget limit for staging"
    echo "  --prod-budget-usd <amount>     - Monthly budget limit for prod"
    echo "  --org-access-role-name <name> - Role name Organizations creates in new accounts"
    echo "  --out-json <path>             - Also write account info to this JSON file (optional)"
    echo "  --poll-sleep-seconds <n>      - Seconds between status polls (default: 15)"
    echo "  --poll-max-minutes <n>        - Max minutes to wait per account (default: 20)"
    echo "  -y, --yes                     - Skip confirmation prompt"
    echo "  --help                        - Show this help"
    echo ""
    echo "Environment variables (override options):"
    echo "  PROJECT_NAME, DEV_EMAIL, STAGING_EMAIL, PROD_EMAIL"
    echo "  BUDGET_ALERT_EMAIL, DEV_BUDGET_USD, STAGING_BUDGET_USD, PROD_BUDGET_USD"
    echo "  ORG_ACCESS_ROLE_NAME, OUT_JSON, POLL_SLEEP_SECONDS, POLL_MAX_MINUTES"
}

# Load infra config and read defaults from infra.yaml (before CLI overrides)
INFRA_YAML=""
read_defaults_from_infra() {
    local root
    root="$(get_project_root)"
    INFRA_YAML="$root/infra/infra.yaml"
    if [[ ! -f "$INFRA_YAML" ]]; then
        print_error "infra/infra.yaml not found at $INFRA_YAML"
        exit 1
    fi
    check_yq_installed || exit 1

    local v
    v=$(yq -r '.project.name // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" ]] && PROJECT_NAME="$v"
    v=$(yq -r '.environments.dev.email // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" ]] && DEV_EMAIL="$v"
    v=$(yq -r '.environments.staging.email // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" ]] && STAGING_EMAIL="$v"
    v=$(yq -r '.environments.prod.email // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" ]] && PROD_EMAIL="$v"
    v=$(yq -r '.environments.dev.org_role_name // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" ]] && ORG_ACCESS_ROLE_NAME="$v"
    v=$(yq -r '.budgets.budget_email // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" ]] && BUDGET_ALERT_EMAIL="$v"
    v=$(yq -r '.budgets.dev_max_budget // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" && "$v" != "null" ]] && DEV_BUDGET_USD="$v"
    v=$(yq -r '.budgets.staging_max_budget // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" && "$v" != "null" ]] && STAGING_BUDGET_USD="$v"
    v=$(yq -r '.budgets.prod_max_budget // ""' "$INFRA_YAML" 2>/dev/null)
    [[ -n "$v" && "$v" != "null" ]] && PROD_BUDGET_USD="$v"
}

# Save env vars so we can apply them after loading infra (env overrides infra)
ENV_PROJECT_NAME="${PROJECT_NAME:-}"
ENV_DEV_EMAIL="${DEV_EMAIL:-}"
ENV_STAGING_EMAIL="${STAGING_EMAIL:-}"
ENV_PROD_EMAIL="${PROD_EMAIL:-}"
ENV_BUDGET_ALERT_EMAIL="${BUDGET_ALERT_EMAIL:-}"
ENV_DEV_BUDGET_USD="${DEV_BUDGET_USD:-}"
ENV_STAGING_BUDGET_USD="${STAGING_BUDGET_USD:-}"
ENV_PROD_BUDGET_USD="${PROD_BUDGET_USD:-}"
ENV_ORG_ACCESS_ROLE_NAME="${ORG_ACCESS_ROLE_NAME:-}"
ENV_OUT_JSON="${OUT_JSON:-}"
ENV_POLL_SLEEP_SECONDS="${POLL_SLEEP_SECONDS:-}"
ENV_POLL_MAX_MINUTES="${POLL_MAX_MINUTES:-}"
ENV_AUTO_CONFIRM="${AUTO_CONFIRM:-}"

# Script defaults (lowest precedence)
PROJECT_NAME="chat-template"
DEV_EMAIL=""
STAGING_EMAIL=""
PROD_EMAIL=""
BUDGET_ALERT_EMAIL=""
DEV_BUDGET_USD="75"
STAGING_BUDGET_USD="150"
PROD_BUDGET_USD="500"
ORG_ACCESS_ROLE_NAME="OrganizationAccountAccessRole"
OUT_JSON=""
POLL_SLEEP_SECONDS="15"
POLL_MAX_MINUTES="20"
AUTO_CONFIRM="0"

DEV_EMAIL_SET=false
STAGING_EMAIL_SET=false
PROD_EMAIL_SET=false

# Apply project-based email defaults when not set from infra or CLI
apply_email_defaults() {
    [[ -z "$DEV_EMAIL" ]] && DEV_EMAIL="${PROJECT_NAME}+dev@example.com"
    [[ -z "$STAGING_EMAIL" ]] && STAGING_EMAIL="${PROJECT_NAME}+staging@example.com"
    [[ -z "$PROD_EMAIL" ]] && PROD_EMAIL="${PROJECT_NAME}+prod@example.com"
}

# Load infra and read defaults (precedence: script defaults < infra.yaml < env < CLI)
read_defaults_from_infra

# Env var overrides
[[ -n "$ENV_PROJECT_NAME" ]] && PROJECT_NAME="$ENV_PROJECT_NAME"
[[ -n "$ENV_DEV_EMAIL" ]] && DEV_EMAIL="$ENV_DEV_EMAIL" && DEV_EMAIL_SET=true
[[ -n "$ENV_STAGING_EMAIL" ]] && STAGING_EMAIL="$ENV_STAGING_EMAIL" && STAGING_EMAIL_SET=true
[[ -n "$ENV_PROD_EMAIL" ]] && PROD_EMAIL="$ENV_PROD_EMAIL" && PROD_EMAIL_SET=true
[[ -n "$ENV_BUDGET_ALERT_EMAIL" ]] && BUDGET_ALERT_EMAIL="$ENV_BUDGET_ALERT_EMAIL"
[[ -n "$ENV_DEV_BUDGET_USD" ]] && DEV_BUDGET_USD="$ENV_DEV_BUDGET_USD"
[[ -n "$ENV_STAGING_BUDGET_USD" ]] && STAGING_BUDGET_USD="$ENV_STAGING_BUDGET_USD"
[[ -n "$ENV_PROD_BUDGET_USD" ]] && PROD_BUDGET_USD="$ENV_PROD_BUDGET_USD"
[[ -n "$ENV_ORG_ACCESS_ROLE_NAME" ]] && ORG_ACCESS_ROLE_NAME="$ENV_ORG_ACCESS_ROLE_NAME"
[[ -n "$ENV_OUT_JSON" ]] && OUT_JSON="$ENV_OUT_JSON"
[[ -n "$ENV_POLL_SLEEP_SECONDS" ]] && POLL_SLEEP_SECONDS="$ENV_POLL_SLEEP_SECONDS"
[[ -n "$ENV_POLL_MAX_MINUTES" ]] && POLL_MAX_MINUTES="$ENV_POLL_MAX_MINUTES"
[[ -n "$ENV_AUTO_CONFIRM" ]] && AUTO_CONFIRM="$ENV_AUTO_CONFIRM"

# Parse options (CLI overrides infra and env)
while [[ $# -gt 0 ]]; do
    case $1 in
        --project-name)
            PROJECT_NAME="$2"
            shift 2
            ;;
        --dev-email)
            DEV_EMAIL="$2"
            DEV_EMAIL_SET=true
            shift 2
            ;;
        --staging-email)
            STAGING_EMAIL="$2"
            STAGING_EMAIL_SET=true
            shift 2
            ;;
        --prod-email)
            PROD_EMAIL="$2"
            PROD_EMAIL_SET=true
            shift 2
            ;;
        --budget-alert-email)
            BUDGET_ALERT_EMAIL="$2"
            shift 2
            ;;
        --dev-budget-usd)
            DEV_BUDGET_USD="$2"
            shift 2
            ;;
        --staging-budget-usd)
            STAGING_BUDGET_USD="$2"
            shift 2
            ;;
        --prod-budget-usd)
            PROD_BUDGET_USD="$2"
            shift 2
            ;;
        --org-access-role-name)
            ORG_ACCESS_ROLE_NAME="$2"
            shift 2
            ;;
        --out-json)
            OUT_JSON="$2"
            shift 2
            ;;
        --poll-sleep-seconds)
            POLL_SLEEP_SECONDS="$2"
            shift 2
            ;;
        --poll-max-minutes)
            POLL_MAX_MINUTES="$2"
            shift 2
            ;;
        -y|--yes)
            AUTO_CONFIRM=1
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

apply_email_defaults

require_cmd aws
require_cmd date

PROJECT_ROOT="$(get_project_root)"
cd "$PROJECT_ROOT"

print_header "Creating AWS Organizations member accounts (dev, staging, prod)"

print_step "Summary: Create member accounts (dev, staging, prod) under management account."
print_info "  Project: $PROJECT_NAME"
print_info "  Dev email: $DEV_EMAIL | Staging: $STAGING_EMAIL | Prod: $PROD_EMAIL"
print_info "  Output: infra/infra.yaml (environments)"
[[ -n "$OUT_JSON" ]] && print_info "  Also writing: $OUT_JSON"
if [ "$AUTO_CONFIRM" -eq 0 ]; then
    source "$SCRIPT_DIR/../utils/deploy_summary.sh"
    confirm_deployment "Proceed with creating AWS accounts?" || exit 0
fi

print_info "Preflight: verifying Organizations access (must run from the management account)..."
if ! aws organizations describe-organization >/dev/null 2>&1; then
    print_error "Failed to describe organization. Ensure you are in the management account and have organizations:DescribeOrganization permission."
    exit 1
fi

MANAGEMENT_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
print_info "Management Account ID: ${MANAGEMENT_ACCOUNT_ID}"

find_account_by_name() {
    local name="$1"
    aws organizations list-accounts \
        --query "Accounts[?Name=='${name}'].Id | [0]" \
        --output text 2>/dev/null | sed 's/None//g' || true
}

create_account_request() {
    local name="$1"
    local email="$2"
    local existing
    existing="$(find_account_by_name "$name")"
    if [[ -n "${existing}" && "${existing}" != "None" ]]; then
        echo "${existing}"
        return 0
    fi
    local req_id
    req_id="$(aws organizations create-account \
        --account-name "${name}" \
        --email "${email}" \
        --role-name "${ORG_ACCESS_ROLE_NAME}" \
        --query "CreateAccountStatus.Id" \
        --output text)"
    echo "${req_id}"
}

wait_for_account() {
    local req_id="$1"
    local max_seconds=$((POLL_MAX_MINUTES * 60))
    local start elapsed status account_id failure_reason epoch_now

    start="$(date +%s)"
    while true; do
        status="$(aws organizations describe-create-account-status \
            --create-account-request-id "${req_id}" \
            --query "CreateAccountStatus.State" \
            --output text)"

        if [[ "${status}" == "SUCCEEDED" ]]; then
            account_id="$(aws organizations describe-create-account-status \
                --create-account-request-id "${req_id}" \
                --query "CreateAccountStatus.AccountId" \
                --output text)"
            echo "${account_id}"
            return 0
        fi

        if [[ "${status}" == "FAILED" ]]; then
            failure_reason="$(aws organizations describe-create-account-status \
                --create-account-request-id "${req_id}" \
                --query "CreateAccountStatus.FailureReason" \
                --output text)"
            print_error "Account creation FAILED for request ${req_id}: ${failure_reason}"
            exit 1
        fi

        epoch_now="$(date +%s)"
        elapsed=$((epoch_now - start))
        if (( elapsed > max_seconds )); then
            print_error "Timed out waiting for account creation (request ${req_id}) after ${POLL_MAX_MINUTES} minutes"
            exit 1
        fi

        print_info "Waiting... request=${req_id} state=${status} (sleep ${POLL_SLEEP_SECONDS}s)"
        sleep "${POLL_SLEEP_SECONDS}"
    done
}

resolve_account_id() {
    local maybe_id="$1"
    if [[ "${maybe_id}" =~ ^[0-9]{12}$ ]]; then
        echo "${maybe_id}"
    else
        wait_for_account "${maybe_id}"
    fi
}

create_budget_for_linked_account() {
    local linked_account_id="$1"
    local budget_name="$2"
    local limit_usd="$3"
    local alert_email="$4"

    if aws budgets describe-budget --account-id "${MANAGEMENT_ACCOUNT_ID}" --budget-name "${budget_name}" >/dev/null 2>&1; then
        print_info "Budget already exists: ${budget_name} (skipping)"
        return 0
    fi

    aws budgets create-budget \
        --account-id "${MANAGEMENT_ACCOUNT_ID}" \
        --budget "{
          \"BudgetName\": \"${budget_name}\",
          \"BudgetLimit\": {\"Amount\": \"${limit_usd}\", \"Unit\": \"USD\"},
          \"TimeUnit\": \"MONTHLY\",
          \"BudgetType\": \"COST\",
          \"CostFilters\": {\"LinkedAccount\": [\"${linked_account_id}\"]}
        }" >/dev/null

    for threshold in 80 100; do
        aws budgets create-notification \
            --account-id "${MANAGEMENT_ACCOUNT_ID}" \
            --budget-name "${budget_name}" \
            --notification "NotificationType=ACTUAL,ComparisonOperator=GREATER_THAN,Threshold=${threshold},ThresholdType=PERCENTAGE" \
            >/dev/null 2>&1 || true

        aws budgets create-subscriber \
            --account-id "${MANAGEMENT_ACCOUNT_ID}" \
            --budget-name "${budget_name}" \
            --notification "NotificationType=ACTUAL,ComparisonOperator=GREATER_THAN,Threshold=${threshold},ThresholdType=PERCENTAGE" \
            --subscriber "SubscriptionType=EMAIL,Address=${alert_email}" \
            >/dev/null 2>&1 || true
    done

    print_info "Created budget + alerts: ${budget_name} (limit \$${limit_usd}/mo) for LinkedAccount=${linked_account_id}"
}

# Account names
DEV_NAME="${PROJECT_NAME}-dev"
STAGING_NAME="${PROJECT_NAME}-staging"
PROD_NAME="${PROJECT_NAME}-prod"

print_step "Creating or reusing accounts..."
DEV_REQ_OR_ID="$(create_account_request "${DEV_NAME}" "${DEV_EMAIL}")"
STAGING_REQ_OR_ID="$(create_account_request "${STAGING_NAME}" "${STAGING_EMAIL}")"
PROD_REQ_OR_ID="$(create_account_request "${PROD_NAME}" "${PROD_EMAIL}")"

DEV_ACCOUNT_ID="$(resolve_account_id "${DEV_REQ_OR_ID}")"
STAGING_ACCOUNT_ID="$(resolve_account_id "${STAGING_REQ_OR_ID}")"
PROD_ACCOUNT_ID="$(resolve_account_id "${PROD_REQ_OR_ID}")"

# Update infra/infra.yaml with account info (project.management_account_id, environments.*)
print_step "Writing account information to infra/infra.yaml..."
yq -i ".project.management_account_id = \"${MANAGEMENT_ACCOUNT_ID}\"" "$INFRA_YAML"
yq -i ".environments.dev.account_id = \"${DEV_ACCOUNT_ID}\"" "$INFRA_YAML"
yq -i ".environments.dev.account_name = \"${DEV_NAME}\"" "$INFRA_YAML"
yq -i ".environments.dev.email = \"${DEV_EMAIL}\"" "$INFRA_YAML"
yq -i ".environments.dev.org_role_name = \"${ORG_ACCESS_ROLE_NAME}\"" "$INFRA_YAML"
yq -i ".environments.staging.account_id = \"${STAGING_ACCOUNT_ID}\"" "$INFRA_YAML"
yq -i ".environments.staging.account_name = \"${STAGING_NAME}\"" "$INFRA_YAML"
yq -i ".environments.staging.email = \"${STAGING_EMAIL}\"" "$INFRA_YAML"
yq -i ".environments.staging.org_role_name = \"${ORG_ACCESS_ROLE_NAME}\"" "$INFRA_YAML"
yq -i ".environments.prod.account_id = \"${PROD_ACCOUNT_ID}\"" "$INFRA_YAML"
yq -i ".environments.prod.account_name = \"${PROD_NAME}\"" "$INFRA_YAML"
yq -i ".environments.prod.email = \"${PROD_EMAIL}\"" "$INFRA_YAML"
yq -i ".environments.prod.org_role_name = \"${ORG_ACCESS_ROLE_NAME}\"" "$INFRA_YAML"

# Optional: also write JSON if requested
if [[ -n "${OUT_JSON}" ]]; then
    cat > "${OUT_JSON}" <<EOF
{
  "project": "${PROJECT_NAME}",
  "management_account_id": "${MANAGEMENT_ACCOUNT_ID}",
  "accounts": {
    "dev":     {"name": "${DEV_NAME}",     "email": "${DEV_EMAIL}",     "account_id": "${DEV_ACCOUNT_ID}"},
    "staging": {"name": "${STAGING_NAME}", "email": "${STAGING_EMAIL}", "account_id": "${STAGING_ACCOUNT_ID}"},
    "prod":    {"name": "${PROD_NAME}",    "email": "${PROD_EMAIL}",    "account_id": "${PROD_ACCOUNT_ID}"}
  },
  "org_access_role_name": "${ORG_ACCESS_ROLE_NAME}"
}
EOF
    print_info "Also wrote: ${OUT_JSON}"
fi

print_header "Done. Updated: infra/infra.yaml"
print_info "Dev:     ${DEV_ACCOUNT_ID} (${DEV_NAME})"
print_info "Staging: ${STAGING_ACCOUNT_ID} (${STAGING_NAME})"
print_info "Prod:    ${PROD_ACCOUNT_ID} (${PROD_NAME})"

if [[ -n "${BUDGET_ALERT_EMAIL}" ]]; then
    print_step "Creating budgets + email alerts (management account owns budgets)..."
    create_budget_for_linked_account "${DEV_ACCOUNT_ID}"     "${DEV_NAME}-monthly"     "${DEV_BUDGET_USD}"     "${BUDGET_ALERT_EMAIL}"
    create_budget_for_linked_account "${STAGING_ACCOUNT_ID}" "${STAGING_NAME}-monthly" "${STAGING_BUDGET_USD}" "${BUDGET_ALERT_EMAIL}"
    create_budget_for_linked_account "${PROD_ACCOUNT_ID}"    "${PROD_NAME}-monthly"    "${PROD_BUDGET_USD}"    "${BUDGET_ALERT_EMAIL}"
else
    print_info "Budgets skipped (set budgets.budget_email in infra.yaml or --budget-alert-email to enable)."
fi

print_info "To assume the role in a member account: aws sts assume-role --role-arn arn:aws:iam::<ACCOUNT_ID>:role/${ORG_ACCESS_ROLE_NAME} --role-session-name <name>"
