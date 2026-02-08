#!/usr/bin/env bash

# AWS Organizations Account Setup Script
# Creates three member accounts (dev, staging, prod) under the management account using
# AWS Organizations. Optionally creates per-account budgets with email alerts.
# Creates an IAM user in the management account that can assume OrganizationAccountAccessRole
# in each member account (policy and user defined in infra/policies/ and infra/roles/).
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
#
#   # Enable console sign-in for the org-admin IAM user (prompts for password)
#   ./scripts/setup/setup_accounts.sh --set-console-password

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
    echo "  --skip-iam-user               - Do not create the management-account IAM user for assuming member roles"
    echo "  --set-console-password        - Prompt to set or update console sign-in password (default)"
    echo "  --no-console-password         - Do not prompt for console password"
    echo "  -y, --yes                     - Skip confirmation prompt"
    echo "  --help                        - Show this help"
    echo ""
    echo "Environment variables (override options):"
    echo "  PROJECT_NAME, DEV_EMAIL, STAGING_EMAIL, PROD_EMAIL"
    echo "  BUDGET_ALERT_EMAIL, DEV_BUDGET_USD, STAGING_BUDGET_USD, PROD_BUDGET_USD"
    echo "  ORG_ACCESS_ROLE_NAME, OUT_JSON, POLL_SLEEP_SECONDS, POLL_MAX_MINUTES, SKIP_IAM_USER, SET_CONSOLE_PASSWORD"
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
ENV_SKIP_IAM_USER="${SKIP_IAM_USER:-}"
ENV_SET_CONSOLE_PASSWORD="${SET_CONSOLE_PASSWORD:-}"
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
SKIP_IAM_USER="0"
SET_CONSOLE_PASSWORD="1"
AUTO_CONFIRM="0"

DEV_EMAIL_SET=false
STAGING_EMAIL_SET=false
PROD_EMAIL_SET=false

# Apply project-based email defaults when not set from infra or CLI
apply_email_defaults() {
    if [[ -z "$DEV_EMAIL" ]]; then DEV_EMAIL="${PROJECT_NAME}+dev@example.com"; fi
    if [[ -z "$STAGING_EMAIL" ]]; then STAGING_EMAIL="${PROJECT_NAME}+staging@example.com"; fi
    if [[ -z "$PROD_EMAIL" ]]; then PROD_EMAIL="${PROJECT_NAME}+prod@example.com"; fi
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
[[ -n "$ENV_SKIP_IAM_USER" ]] && SKIP_IAM_USER="$ENV_SKIP_IAM_USER"
[[ -n "$ENV_SET_CONSOLE_PASSWORD" ]] && SET_CONSOLE_PASSWORD="$ENV_SET_CONSOLE_PASSWORD"
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
        --skip-iam-user)
            SKIP_IAM_USER=1
            shift
            ;;
        --set-console-password)
            SET_CONSOLE_PASSWORD=1
            shift
            ;;
        --no-console-password)
            SET_CONSOLE_PASSWORD=0
            shift
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

PROJECT_ROOT="$(get_project_root)"
cd "$PROJECT_ROOT"

print_header "Creating AWS Organizations member accounts (dev, staging, prod)"

print_step "Summary: Create member accounts (dev, staging, prod) under management account."
print_info "  Project: $PROJECT_NAME"
print_info "  Dev email:     $DEV_EMAIL"
print_info "  Staging email: $STAGING_EMAIL"
print_info "  Prod email:    $PROD_EMAIL"
print_info "  Output: infra/infra.yaml (environments)"
[[ -n "$OUT_JSON" ]] && print_info "  Also writing: $OUT_JSON"
if [ "$AUTO_CONFIRM" -eq 0 ]; then
    if [ ! -t 0 ]; then
        print_info "Not running in a terminal. Re-run with -y to proceed non-interactively."
        exit 0
    fi
    source "$SCRIPT_DIR/../utils/deploy_summary.sh"
    confirm_deployment "Proceed with creating AWS accounts?" || exit 0
fi

require_cmd aws
require_cmd date

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

# Deploy IAM policy and user in management account so an IAM user can assume member-account roles.
# Uses infra/policies/assume_org_access_role_policy.yaml and infra/roles/management_account_admin_user.yaml.
setup_management_account_iam_user() {
    local infra_dir policy_template user_template policy_stack user_stack param_file policy_arn

    infra_dir="$(dirname "$INFRA_YAML")"
    policy_template="${infra_dir}/policies/assume_org_access_role_policy.yaml"
    user_template="${infra_dir}/roles/management_account_admin_user.yaml"
    policy_stack="${PROJECT_NAME}-assume-org-access-policy"
    user_stack="${PROJECT_NAME}-management-admin-user"

    if [[ ! -f "$policy_template" ]]; then
        print_error "Policy template not found: $policy_template"
        return 1
    fi
    if [[ ! -f "$user_template" ]]; then
        print_error "User template not found: $user_template"
        return 1
    fi

    print_step "Deploying assume-org-access policy stack in management account..."
    param_file="$(mktemp)"
    trap "rm -f $param_file" RETURN
    cat > "$param_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "${PROJECT_NAME}"},
  {"ParameterKey": "DevAccountId", "ParameterValue": "${DEV_ACCOUNT_ID}"},
  {"ParameterKey": "StagingAccountId", "ParameterValue": "${STAGING_ACCOUNT_ID}"},
  {"ParameterKey": "ProdAccountId", "ParameterValue": "${PROD_ACCOUNT_ID}"},
  {"ParameterKey": "OrgAccessRoleName", "ParameterValue": "${ORG_ACCESS_ROLE_NAME}"}
]
EOF

    if aws cloudformation describe-stacks --stack-name "$policy_stack" --query 'Stacks[0].StackId' --output text >/dev/null 2>&1; then
        local update_out
        update_out="$(aws cloudformation update-stack \
            --stack-name "$policy_stack" \
            --template-body "file://${policy_template}" \
            --parameters "file://${param_file}" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1)" || true
        if echo "$update_out" | grep -q "No updates are to be performed"; then
            print_info "Policy stack already up to date."
        elif echo "$update_out" | grep -q "ValidationError\|Failed"; then
            print_warning "Policy stack update failed: $update_out"
        else
            aws cloudformation wait stack-update-complete --stack-name "$policy_stack" || true
        fi
    else
        aws cloudformation create-stack \
            --stack-name "$policy_stack" \
            --template-body "file://${policy_template}" \
            --parameters "file://${param_file}" \
            --capabilities CAPABILITY_NAMED_IAM
        aws cloudformation wait stack-create-complete --stack-name "$policy_stack" || {
            print_error "Policy stack creation failed."
            return 1
        }
    fi

    policy_arn="$(aws cloudformation describe-stacks --stack-name "$policy_stack" --query 'Stacks[0].Outputs[?OutputKey==`PolicyArn`].OutputValue' --output text)"
    if [[ -z "$policy_arn" ]]; then
        print_error "Could not get PolicyArn output from stack ${policy_stack}"
        return 1
    fi

    print_step "Deploying management account admin user stack..."
    param_file="$(mktemp)"
    trap "rm -f $param_file" RETURN
    cat > "$param_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "${PROJECT_NAME}"},
  {"ParameterKey": "AssumeOrgAccessPolicyArn", "ParameterValue": "${policy_arn}"}
]
EOF

    if aws cloudformation describe-stacks --stack-name "$user_stack" --query 'Stacks[0].StackId' --output text >/dev/null 2>&1; then
        local update_out
        update_out="$(aws cloudformation update-stack \
            --stack-name "$user_stack" \
            --template-body "file://${user_template}" \
            --parameters "file://${param_file}" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1)" || true
        if echo "$update_out" | grep -q "No updates are to be performed"; then
            print_info "User stack already up to date."
        elif echo "$update_out" | grep -q "ValidationError\|Failed"; then
            print_warning "User stack update failed: $update_out"
        else
            aws cloudformation wait stack-update-complete --stack-name "$user_stack" || true
        fi
    else
        aws cloudformation create-stack \
            --stack-name "$user_stack" \
            --template-body "file://${user_template}" \
            --parameters "file://${param_file}" \
            --capabilities CAPABILITY_NAMED_IAM
        aws cloudformation wait stack-create-complete --stack-name "$user_stack" || {
            print_error "User stack creation failed."
            return 1
        }
    fi

    local username
    username="$(aws cloudformation describe-stacks --stack-name "$user_stack" --query 'Stacks[0].Outputs[?OutputKey==`UserName`].OutputValue' --output text)"
    print_info "IAM user created/updated: ${username} (can assume ${ORG_ACCESS_ROLE_NAME} in dev, staging, prod)"
    print_info "Create access keys in the IAM console for this user, or: aws iam create-access-key --user-name ${username}"

    # Optional: set console sign-in password so the user can sign in to the AWS Console
    if [[ "${SET_CONSOLE_PASSWORD:-0}" -eq 1 ]]; then
        local console_password console_password_confirm change_choice has_profile
        has_profile=0
        if aws iam get-login-profile --user-name "$username" >/dev/null 2>&1; then
            has_profile=1
        fi
        if [[ $has_profile -eq 1 ]] && [[ -t 0 ]]; then
            read -p "User already has console access. Set new password? (y/N): " change_choice
            case "$change_choice" in
                y|Y|yes|YES) ;;
                *) print_info "Skipping console password update."; return 0 ;;
            esac
        fi
        if [[ ! -t 0 ]]; then
            print_warning "Not a terminal. Skipping console password. Re-run with --set-console-password in a terminal to set a password."
            return 0
        fi
        while true; do
            read -s -p "Console password for ${username}: " console_password
            echo
            read -s -p "Confirm password: " console_password_confirm
            echo
            if [[ "$console_password" != "$console_password_confirm" ]]; then
                print_error "Passwords do not match. Try again."
                continue
            fi
            if [[ ${#console_password} -lt 8 ]]; then
                print_error "Password must be at least 8 characters (per IAM default policy). Try again."
                continue
            fi
            break
        done
        if aws iam get-login-profile --user-name "$username" >/dev/null 2>&1; then
            aws iam update-login-profile --user-name "$username" --password "$console_password"
            print_info "Console sign-in password updated for ${username}."
        else
            aws iam create-login-profile --user-name "$username" --password "$console_password"
            print_info "Console sign-in enabled for ${username}. Sign in at https://console.aws.amazon.com/ with account ${MANAGEMENT_ACCOUNT_ID} and user ${username}, then use Switch Role to access dev/staging/prod."
        fi
    else
        print_info "To enable console sign-in, set a password in IAM for ${username}, or re-run without --no-console-password to be prompted."
    fi
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

if [[ "${SKIP_IAM_USER}" -eq 0 ]]; then
    print_step "Setting up IAM user in management account (can assume member-account roles)..."
    if setup_management_account_iam_user; then
        print_complete "Management account IAM user setup complete."
    else
        print_warning "Management account IAM user setup failed or skipped. Use --skip-iam-user to skip this step."
    fi
else
    print_info "Skipping IAM user setup (--skip-iam-user)."
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

print_info "To assume the role in a member account (as the new IAM user): aws sts assume-role --role-arn arn:aws:iam::<ACCOUNT_ID>:role/${ORG_ACCESS_ROLE_NAME} --role-session-name <name>"
print_info "Example dev: aws sts assume-role --role-arn arn:aws:iam::${DEV_ACCOUNT_ID}:role/${ORG_ACCESS_ROLE_NAME} --role-session-name dev"
