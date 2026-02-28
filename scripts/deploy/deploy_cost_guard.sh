#!/bin/bash

# Cost Guard Deployment Script
# Deploys near-realtime monitoring and enforcement resources:
# - DynamoDB state table
# - SNS warning/critical topics (+ optional email/SMS subscriptions)
# - Monitor/Enforcer/Pricing Lambda functions
# - EventBridge schedules for monitor and pricing sync

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

RESOURCE_DISPLAY_NAME="Cost Guard"

show_usage() {
    echo "Cost Guard Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Actions:"
    echo "  deploy    - Deploy the stack (default)"
    echo "  update    - Update the stack"
    echo "  delete    - Delete the stack"
    echo "  validate  - Validate the template"
    echo "  status    - Show stack status"
    echo ""
    echo "Options:"
    echo "  -y, --yes         - Skip confirmation prompts"
    echo "  --region <region> - Override region"
    echo "  --image-tag <tag> - Docker image tag (default: latest)"
}

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ORIGINAL_ARGS=("$@")
ENVIRONMENT=$1
shift

ACTION="deploy"
AUTO_CONFIRM=false
REGION_OVERRIDE=""
IMAGE_TAG=""

print_missing_hydrate_warnings() {
    local missing_file=$1
    if [ -s "$missing_file" ]; then
        print_warning "Some config_secrets were missing or empty; placeholders were hydrated with blank values."
        print_warning "Add them to infra/secrets/${ENVIRONMENT}_secrets.yaml (config_secrets) to fix:"
        while IFS= read -r key; do
            [ -n "$key" ] && print_warning "  - $key"
        done < "$missing_file"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        deploy|update|delete|validate|status)
            ACTION="$1"
            shift
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --region)
            REGION_OVERRIDE="$2"
            shift 2
            ;;
        --image-tag)
            IMAGE_TAG="$2"
            shift 2
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

if [ -z "$IMAGE_TAG" ]; then
    IMAGE_TAG="latest"
fi

validate_environment "$ENVIRONMENT" || exit 1

# Hydrate in a runtime-only scope for deploy/update, then re-run this script with hydration disabled.
if { [ "$ACTION" = "deploy" ] || [ "$ACTION" = "update" ]; } && [ "${HYDRATE_RUNTIME_ACTIVE:-false}" != "true" ]; then
    HYDRATE_SCRIPT="$(get_project_root)/scripts/utils/hydrate_configs.sh"
    if [ -f "$HYDRATE_SCRIPT" ]; then
        print_step "Hydrating config values for $ENVIRONMENT (runtime-only)"
        chmod +x "$HYDRATE_SCRIPT"
        HYDRATE_MISSING_FILE=""
        RUNTIME_CMD=(env HYDRATE_RUNTIME_ACTIVE=true "$0" "${ORIGINAL_ARGS[@]}")
        if HYDRATE_MISSING_FILE=$(mktemp 2>/dev/null); then
            if "$HYDRATE_SCRIPT" --run "$ENVIRONMENT" "$HYDRATE_MISSING_FILE" -- "${RUNTIME_CMD[@]}"; then
                print_missing_hydrate_warnings "$HYDRATE_MISSING_FILE"
                rm -f "$HYDRATE_MISSING_FILE"
                exit 0
            else
                status=$?
                print_missing_hydrate_warnings "$HYDRATE_MISSING_FILE"
                rm -f "$HYDRATE_MISSING_FILE"
                exit $status
            fi
        else
            "$HYDRATE_SCRIPT" --run "$ENVIRONMENT" -- "${RUNTIME_CMD[@]}"
            exit $?
        fi
    else
        print_warning "Hydrate script not found at $HYDRATE_SCRIPT; continuing without hydration"
    fi
fi

print_header "$RESOURCE_DISPLAY_NAME Deployment"

load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

if ! is_cost_control_enabled; then
    print_warning "cost_control.enabled is false. Nothing to deploy."
    exit 0
fi

PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
if [ -n "$REGION_OVERRIDE" ]; then
    AWS_REGION="$REGION_OVERRIDE"
fi

# Cost guard is an application-level control. Use deployer profile for local runs.
if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
    [ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
    [ -n "$AWS_PROFILE" ] && print_info "Using deployer profile: $AWS_PROFILE"
fi

STACK_NAME=$(get_cost_control_stack_name "$ENVIRONMENT")
TEMPLATE_FILE="$(get_project_root)/infra/resources/cost_guard_template.yaml"

PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

is_placeholder_value() {
    local value=$1
    [[ "$value" == *'${'* ]] || [[ "$value" == *'$('* ]]
}

is_valid_email() {
    local value=$1
    [[ "$value" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]
}

is_valid_e164_sms() {
    local value=$1
    [[ "$value" =~ ^\+[1-9][0-9]{7,14}$ ]]
}

# Trim whitespace/newlines so CloudFormation never sees invalid characters
trim_param_value() {
    local v=$1
    v=$(printf '%s' "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr -d '\n\r')
    echo -n "$v"
}

sanitize_notification_targets() {
    local field_name=$1
    local raw_value=$2
    local validator=$3

    raw_value=$(trim_param_value "$raw_value")
    if [ -z "$raw_value" ] || [ "$raw_value" = "null" ]; then
        echo ""
        return 0
    fi
    if is_placeholder_value "$raw_value"; then
        print_warning "Ignoring $field_name because it contains an unresolved placeholder: $raw_value"
        echo ""
        return 0
    fi
    if ! $validator "$raw_value"; then
        print_warning "Ignoring $field_name because format is invalid: $raw_value"
        echo ""
        return 0
    fi
    echo "$raw_value"
}

do_validate_template() {
    print_step "Validating CloudFormation template..."
    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi
    aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" >/dev/null
    print_complete "Template validation successful"
}

show_status() {
    print_step "Checking stack status: $STACK_NAME"
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        aws_cmd cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_info "Stack outputs:"
        aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

ensure_ecr_repo() {
    local repo_name=$1
    if aws_cmd ecr describe-repositories --repository-names "$repo_name" >/dev/null 2>&1; then
        return 0
    fi
    print_step "Creating ECR repository: $repo_name"
    aws_cmd ecr create-repository \
        --repository-name "$repo_name" \
        --image-tag-mutability MUTABLE \
        --image-scanning-configuration scanOnPush=true >/dev/null
}

build_and_push_image() {
    local repo_name=$1
    local account_id
    account_id=$(aws_cmd sts get-caller-identity --query Account --output text)
    local ecr_uri="${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com/${repo_name}"

    print_step "Logging into ECR"
    aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "${account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com" >/dev/null

    print_step "Building Docker image for cost guard Lambdas"
    docker build --platform linux/amd64 -f src/cost_guard/Dockerfile -t "cost-guard-lambda:${IMAGE_TAG}" .

    local image_uri="${ecr_uri}:${IMAGE_TAG}"
    print_step "Pushing Docker image: $image_uri"
    docker tag "cost-guard-lambda:${IMAGE_TAG}" "$image_uri"
    docker push "$image_uri" >/dev/null

    COST_GUARD_IMAGE_URI="$image_uri"
}

build_parameter_file() {
    local param_file=$1
    local image_uri=$2

    local budget_amount warning_percent hard_percent monitor_interval breach_count cooldown_mins pricing_hour
    local warning_email warning_sms critical_email critical_sms
    local cost_control_json

    budget_amount=$(get_cost_control_value "budget.amount_usd")
    warning_percent=$(get_cost_control_value "budget.warning_percent")
    hard_percent=$(get_cost_control_value "budget.hard_percent")
    monitor_interval=$(get_cost_control_value "monitor.interval_minutes")
    breach_count=$(get_cost_control_value "monitor.consecutive_breach_count")
    cooldown_mins=$(get_cost_control_value "monitor.cooldown_minutes")
    pricing_hour=$(get_cost_control_value "pricing.refresh_hour_utc")
    warning_email=$(get_cost_control_value "notifications.warning_email")
    warning_sms=$(get_cost_control_value "notifications.warning_sms")
    critical_email=$(get_cost_control_value "notifications.critical_email")
    critical_sms=$(get_cost_control_value "notifications.critical_sms")
    warning_email=$(sanitize_notification_targets "warning_email" "$warning_email" is_valid_email)
    warning_sms=$(sanitize_notification_targets "warning_sms" "$warning_sms" is_valid_e164_sms)
    critical_email=$(sanitize_notification_targets "critical_email" "$critical_email" is_valid_email)
    critical_sms=$(sanitize_notification_targets "critical_sms" "$critical_sms" is_valid_e164_sms)

    cost_control_json=$(get_cost_control_json "$ENVIRONMENT")

    COST_GUARD_CONFIG_JSON="$cost_control_json" \
    PROJECT_NAME="$PROJECT_NAME" \
    ENVIRONMENT="$ENVIRONMENT" \
    AWS_REGION="$AWS_REGION" \
    COST_GUARD_IMAGE_URI="$image_uri" \
    BUDGET_AMOUNT="$budget_amount" \
    WARNING_PERCENT="$warning_percent" \
    HARD_PERCENT="$hard_percent" \
    MONITOR_INTERVAL="$monitor_interval" \
    BREACH_COUNT="$breach_count" \
    COOLDOWN_MINS="$cooldown_mins" \
    PRICING_HOUR="$pricing_hour" \
    WARNING_EMAIL="$warning_email" \
    WARNING_SMS="$warning_sms" \
    CRITICAL_EMAIL="$critical_email" \
    CRITICAL_SMS="$critical_sms" \
    PARAM_FILE="$param_file" \
    python3 - << 'PY'
import json
import os

def safe_param(v):
    if v is None:
        return ""
    s = str(v).strip().replace("\r", "").replace("\n", "")
    return s

params = [
    {"ParameterKey": "ProjectName", "ParameterValue": os.environ["PROJECT_NAME"]},
    {"ParameterKey": "Environment", "ParameterValue": os.environ["ENVIRONMENT"]},
    {"ParameterKey": "AWSRegion", "ParameterValue": os.environ["AWS_REGION"]},
    {"ParameterKey": "CostGuardImageUri", "ParameterValue": os.environ["COST_GUARD_IMAGE_URI"]},
    {"ParameterKey": "CostGuardConfigJson", "ParameterValue": os.environ["COST_GUARD_CONFIG_JSON"]},
    {"ParameterKey": "BudgetAmountUsd", "ParameterValue": str(os.environ["BUDGET_AMOUNT"])},
    {"ParameterKey": "WarningPercent", "ParameterValue": str(os.environ["WARNING_PERCENT"])},
    {"ParameterKey": "HardPercent", "ParameterValue": str(os.environ["HARD_PERCENT"])},
    {"ParameterKey": "MonitorIntervalMinutes", "ParameterValue": str(os.environ["MONITOR_INTERVAL"])},
    {"ParameterKey": "ConsecutiveBreachCount", "ParameterValue": str(os.environ["BREACH_COUNT"])},
    {"ParameterKey": "CooldownMinutes", "ParameterValue": str(os.environ["COOLDOWN_MINS"])},
    {"ParameterKey": "PricingRefreshHourUtc", "ParameterValue": str(os.environ["PRICING_HOUR"])},
    {"ParameterKey": "WarningEmail", "ParameterValue": safe_param(os.environ.get("WARNING_EMAIL", ""))},
    {"ParameterKey": "WarningSms", "ParameterValue": safe_param(os.environ.get("WARNING_SMS", ""))},
    {"ParameterKey": "CriticalEmail", "ParameterValue": safe_param(os.environ.get("CRITICAL_EMAIL", ""))},
    {"ParameterKey": "CriticalSms", "ParameterValue": safe_param(os.environ.get("CRITICAL_SMS", ""))},
]

with open(os.environ["PARAM_FILE"], "w", encoding="utf-8") as f:
    json.dump(params, f)
PY
}

deploy_stack() {
    local ecr_repo_name="${PROJECT_NAME}-cost-guard-${ENVIRONMENT}"

    if ! command -v docker >/dev/null 2>&1; then
        print_error "docker is required for cost guard image deployment"
        exit 1
    fi

    print_step "Summary: Deploy cost guard stack and Lambda image"
    print_info "Stack Name: $STACK_NAME"
    print_info "ECR Repository: $ecr_repo_name"
    print_info "Region: $AWS_REGION"
    print_info "Image Tag: $IMAGE_TAG"

    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Proceed with cost guard deployment?" || exit 0
    fi

    ensure_ecr_repo "$ecr_repo_name"
    build_and_push_image "$ecr_repo_name"

    local param_file
    param_file=$(mktemp)
    trap "rm -f '$param_file'" EXIT
    build_parameter_file "$param_file" "$COST_GUARD_IMAGE_URI"

    local stack_exists=false
    local no_updates=false
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        stack_exists=true
        print_info "Stack exists. Updating..."
        local update_output
        update_output=$(aws_cmd cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1) || {
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                no_updates=true
                print_info "No updates needed for stack $STACK_NAME"
            else
                print_error "Stack update failed: $update_output"
                exit 1
            fi
        }
    else
        print_info "Creating new stack: $STACK_NAME"
        aws_cmd cloudformation create-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM >/dev/null
    fi

    if [ "$no_updates" = false ]; then
        if [ "$stack_exists" = true ]; then
            print_info "Waiting for stack update..."
            aws_cmd cloudformation wait stack-update-complete --stack-name "$STACK_NAME"
        else
            print_info "Waiting for stack creation..."
            aws_cmd cloudformation wait stack-create-complete --stack-name "$STACK_NAME"
        fi
    fi

    print_complete "Cost guard deployment finished"
    echo ""
    print_info "Stack outputs:"
    aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs'
}

delete_stack() {
    if ! aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME does not exist"
        return 0
    fi
    print_warning "This will delete cost guard resources for $ENVIRONMENT"
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Proceed with deleting cost guard stack?" || exit 0
    fi
    aws_cmd cloudformation delete-stack --stack-name "$STACK_NAME"
    aws_cmd cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
    print_complete "Deleted stack: $STACK_NAME"
}

case "$ACTION" in
    validate)
        do_validate_template
        ;;
    status)
        show_status
        ;;
    delete)
        delete_stack
        ;;
    deploy|update)
        deploy_stack
        ;;
    *)
        print_error "Unknown action: $ACTION"
        exit 1
        ;;
esac
