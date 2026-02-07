#!/usr/bin/env bash

# AWS Organizations Account Deployment Script
# Creates three member accounts (dev, staging, prod) under the management account using
# AWS Organizations. Optionally creates per-account budgets with email alerts.
# Must be run from the management account with Organizations permissions.
#
# See docs/aws_organizations.md for background on Organizations and consolidated billing.
#
# Usage Examples:
#   # Create accounts with default project name and email pattern
#   ./scripts/deploy/deploy_accounts.sh
#
#   # Create accounts with custom project name and emails
#   ./scripts/deploy/deploy_accounts.sh --project-name myapp \
#     --dev-email myapp+dev@example.com \
#     --staging-email myapp+staging@example.com \
#     --prod-email myapp+prod@example.com
#
#   # Create accounts and enable budget alerts
#   BUDGET_ALERT_EMAIL=you@yourdomain.com ./scripts/deploy/deploy_accounts.sh
#
#   # Custom output file and budgets
#   ./scripts/deploy/deploy_accounts.sh --out-json my-accounts.json \
#     --budget-alert-email you@example.com \
#     --dev-budget-usd 75 --staging-budget-usd 150 --prod-budget-usd 500

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}[DEPLOY ACCOUNTS]${NC} $1"
}

print_step() {
    echo -e "${CYAN}[STEP]${NC} $1"
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        print_error "Missing required command: $1"
        exit 1
    fi
}

show_usage() {
    echo "AWS Organizations Account Deployment Script"
    echo ""
    echo "Creates three member accounts (dev, staging, prod) under the management account."
    echo "Must be run from the management account with Organizations permissions."
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --project-name <name>         - Project name for account names (default: chat-template)"
    echo "  --dev-email <email>           - Email for dev account (default: <project>+dev@example.com)"
    echo "  --staging-email <email>        - Email for staging account"
    echo "  --prod-email <email>           - Email for prod account"
    echo "  --budget-alert-email <email>   - Enable budgets and send alerts to this email"
    echo "  --dev-budget-usd <amount>     - Monthly budget limit for dev (default: 75)"
    echo "  --staging-budget-usd <amount> - Monthly budget limit for staging (default: 150)"
    echo "  --prod-budget-usd <amount>     - Monthly budget limit for prod (default: 500)"
    echo "  --org-access-role-name <name> - Role name Organizations creates in new accounts (default: OrganizationAccountAccessRole)"
    echo "  --out-json <path>              - Output JSON file (default: accounts.json)"
    echo "  --poll-sleep-seconds <n>      - Seconds between status polls (default: 15)"
    echo "  --poll-max-minutes <n>         - Max minutes to wait per account (default: 20)"
    echo "  --help                         - Show this help"
    echo ""
    echo "Environment variables (override options):"
    echo "  PROJECT_NAME, DEV_EMAIL, STAGING_EMAIL, PROD_EMAIL"
    echo "  BUDGET_ALERT_EMAIL, DEV_BUDGET_USD, STAGING_BUDGET_USD, PROD_BUDGET_USD"
    echo "  ORG_ACCESS_ROLE_NAME, OUT_JSON, POLL_SLEEP_SECONDS, POLL_MAX_MINUTES"
    echo ""
    echo "Examples:"
    echo "  $0"
    echo "  $0 --project-name myapp --dev-email myapp+dev@example.com --staging-email myapp+staging@example.com --prod-email myapp+prod@example.com"
    echo "  BUDGET_ALERT_EMAIL=you@example.com $0 --out-json accounts.json"
}

# Defaults (env vars override these after parsing)
PROJECT_NAME="${PROJECT_NAME:-chat-template}"
DEV_EMAIL="${DEV_EMAIL:-${PROJECT_NAME}+dev@example.com}"
STAGING_EMAIL="${STAGING_EMAIL:-${PROJECT_NAME}+staging@example.com}"
PROD_EMAIL="${PROD_EMAIL:-${PROJECT_NAME}+prod@example.com}"
BUDGET_ALERT_EMAIL="${BUDGET_ALERT_EMAIL:-}"
DEV_BUDGET_USD="${DEV_BUDGET_USD:-75}"
STAGING_BUDGET_USD="${STAGING_BUDGET_USD:-150}"
PROD_BUDGET_USD="${PROD_BUDGET_USD:-500}"
ORG_ACCESS_ROLE_NAME="${ORG_ACCESS_ROLE_NAME:-OrganizationAccountAccessRole}"
OUT_JSON="${OUT_JSON:-accounts.json}"
POLL_SLEEP_SECONDS="${POLL_SLEEP_SECONDS:-15}"
POLL_MAX_MINUTES="${POLL_MAX_MINUTES:-20}"

DEV_EMAIL_SET=false
STAGING_EMAIL_SET=false
PROD_EMAIL_SET=false

# Parse options (CLI overrides env defaults)
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

# Apply project-based email defaults when not explicitly set
[[ "$DEV_EMAIL_SET" != true ]]     && DEV_EMAIL="${PROJECT_NAME}+dev@example.com"
[[ "$STAGING_EMAIL_SET" != true ]] && STAGING_EMAIL="${PROJECT_NAME}+staging@example.com"
[[ "$PROD_EMAIL_SET" != true ]]    && PROD_EMAIL="${PROJECT_NAME}+prod@example.com"

require_cmd aws
require_cmd date

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

print_header "Creating AWS Organizations member accounts (dev, staging, prod)"

print_status "Preflight: verifying Organizations access (must run from the management account)..."
if ! aws organizations describe-organization >/dev/null 2>&1; then
    print_error "Failed to describe organization. Ensure you are in the management account and have organizations:DescribeOrganization permission."
    exit 1
fi

MANAGEMENT_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
print_status "Management Account ID: ${MANAGEMENT_ACCOUNT_ID}"

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

        print_status "Waiting... request=${req_id} state=${status} (sleep ${POLL_SLEEP_SECONDS}s)"
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
        print_status "Budget already exists: ${budget_name} (skipping)"
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

    print_status "Created budget + alerts: ${budget_name} (limit \$${limit_usd}/mo) for LinkedAccount=${linked_account_id}"
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

# Write output JSON (relative to project root)
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

print_header "Done. Wrote: ${OUT_JSON}"
print_status "Dev:     ${DEV_ACCOUNT_ID} (${DEV_NAME})"
print_status "Staging: ${STAGING_ACCOUNT_ID} (${STAGING_NAME})"
print_status "Prod:    ${PROD_ACCOUNT_ID} (${PROD_NAME})"

if [[ -n "${BUDGET_ALERT_EMAIL}" ]]; then
    print_step "Creating budgets + email alerts (management account owns budgets)..."
    create_budget_for_linked_account "${DEV_ACCOUNT_ID}"     "${DEV_NAME}-monthly"     "${DEV_BUDGET_USD}"     "${BUDGET_ALERT_EMAIL}"
    create_budget_for_linked_account "${STAGING_ACCOUNT_ID}" "${STAGING_NAME}-monthly" "${STAGING_BUDGET_USD}" "${BUDGET_ALERT_EMAIL}"
    create_budget_for_linked_account "${PROD_ACCOUNT_ID}"    "${PROD_NAME}-monthly"    "${PROD_BUDGET_USD}"    "${BUDGET_ALERT_EMAIL}"
else
    print_status "Budgets skipped (set BUDGET_ALERT_EMAIL or --budget-alert-email to enable)."
fi

print_status "To assume the role in a member account: aws sts assume-role --role-arn arn:aws:iam::<ACCOUNT_ID>:role/${ORG_ACCESS_ROLE_NAME} --role-session-name <name>"
