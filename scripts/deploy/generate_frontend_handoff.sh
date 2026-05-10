#!/bin/bash
# Generate a .env-style handoff file for the frontend repo.
# Reads CloudFormation outputs from the cognito, frontend, rag_lambda,
# history_api_lambda, and library_api_lambda stacks, plus runtime values from
# the env secrets file, and writes infra/state/<env>.frontend.handoff.env atomically.
#
# Idempotent. Safe to invoke standalone or from deploy_all.sh. Skips with a
# warning when required upstream stacks are not yet deployed.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

show_usage() {
    echo "Usage: $0 <environment> [--profile <aws-profile>]"
    echo ""
    echo "Writes infra/state/<env>.frontend.handoff.env with NUXT_PUBLIC_* values"
    echo "for consumption by the frontend repo."
}

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

PROFILE_OVERRIDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --profile)
            PROFILE_OVERRIDE="$2"
            shift 2
            ;;
        --profile=*)
            PROFILE_OVERRIDE="${1#--profile=}"
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

load_infra_config || exit 1
validate_environment "$ENVIRONMENT" || exit 1

PROJECT_ROOT=$(get_project_root)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")

if [ -n "$PROFILE_OVERRIDE" ]; then
    export AWS_PROFILE="$PROFILE_OVERRIDE"
elif [ -z "${AWS_PROFILE:-}" ] && [ -z "${AWS_SESSION_TOKEN:-}" ]; then
    resolved=$(get_environment_profile "$ENVIRONMENT")
    [ "$resolved" != "null" ] && [ -n "$resolved" ] && export AWS_PROFILE="$resolved"
fi

aws_cmd() {
    if [ -n "${AWS_PROFILE:-}" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

stack_output() {
    local stack="$1"
    local key="$2"
    aws_cmd cloudformation describe-stacks \
        --stack-name "$stack" \
        --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
        --output text 2>/dev/null
}

print_step "Generating frontend handoff for $ENVIRONMENT"

cognito_stack=$(get_resource_stack_name "cognito" "$ENVIRONMENT")
frontend_stack=$(get_resource_stack_name "frontend" "$ENVIRONMENT")
lambda_stack=$(get_resource_stack_name "rag_lambda" "$ENVIRONMENT")
history_api_stack=$(get_resource_stack_name "history_api_lambda" "$ENVIRONMENT")
library_api_stack=$(get_resource_stack_name "library_api_lambda" "$ENVIRONMENT")

if ! aws_cmd cloudformation describe-stacks --stack-name "$cognito_stack" >/dev/null 2>&1; then
    print_warning "Cognito stack '$cognito_stack' not found; skipping handoff generation"
    exit 0
fi
if ! aws_cmd cloudformation describe-stacks --stack-name "$frontend_stack" >/dev/null 2>&1; then
    print_warning "Frontend stack '$frontend_stack' not found; skipping handoff generation"
    exit 0
fi
if ! aws_cmd cloudformation describe-stacks --stack-name "$lambda_stack" >/dev/null 2>&1; then
    print_warning "Lambda stack '$lambda_stack' not found; skipping handoff generation"
    exit 0
fi
if ! aws_cmd cloudformation describe-stacks --stack-name "$history_api_stack" >/dev/null 2>&1; then
    print_warning "History API Lambda stack '$history_api_stack' not found; skipping handoff generation"
    exit 0
fi
if ! aws_cmd cloudformation describe-stacks --stack-name "$library_api_stack" >/dev/null 2>&1; then
    print_warning "Library API Lambda stack '$library_api_stack' not found; skipping handoff generation"
    exit 0
fi

cognito_region=$(stack_output "$cognito_stack" "CognitoRegion")
user_pool_id=$(stack_output "$cognito_stack" "UserPoolId")
app_client_id=$(stack_output "$cognito_stack" "AppClientId")
cf_domain=$(stack_output "$frontend_stack" "CloudFrontDomainName")
function_url=$(stack_output "$lambda_stack" "LambdaFunctionUrl")
history_api_url=$(stack_output "$history_api_stack" "LambdaFunctionUrl")
library_api_url=$(stack_output "$library_api_stack" "LambdaFunctionUrl")

# Lambda template may name the output differently — fall back to live config.
if [ -z "$function_url" ] || [ "$function_url" = "None" ] || [ "$function_url" = "null" ]; then
    function_name="${PROJECT_NAME:-chat-template}-${ENVIRONMENT}-rag-chat"
    function_url=$(aws_cmd lambda get-function-url-config \
        --function-name "$function_name" \
        --query 'FunctionUrl' --output text 2>/dev/null || true)
fi
if [ -z "$history_api_url" ] || [ "$history_api_url" = "None" ] || [ "$history_api_url" = "null" ]; then
    history_api_name="${PROJECT_NAME:-chat-template}-${ENVIRONMENT}-history-api"
    history_api_url=$(aws_cmd lambda get-function-url-config \
        --function-name "$history_api_name" \
        --query 'FunctionUrl' --output text 2>/dev/null || true)
fi
if [ -z "$library_api_url" ] || [ "$library_api_url" = "None" ] || [ "$library_api_url" = "null" ]; then
    library_api_name="${PROJECT_NAME:-chat-template}-${ENVIRONMENT}-library-api"
    library_api_url=$(aws_cmd lambda get-function-url-config \
        --function-name "$library_api_name" \
        --query 'FunctionUrl' --output text 2>/dev/null || true)
fi

# Strip any trailing slash on function URLs for consistent assembly downstream.
function_url="${function_url%/}"
history_api_url="${history_api_url%/}"
library_api_url="${library_api_url%/}"

missing=()
[ -z "$cognito_region" ] || [ "$cognito_region" = "None" ] && missing+=("CognitoRegion")
[ -z "$user_pool_id" ] || [ "$user_pool_id" = "None" ] && missing+=("UserPoolId")
[ -z "$app_client_id" ] || [ "$app_client_id" = "None" ] && missing+=("AppClientId")
[ -z "$cf_domain" ] || [ "$cf_domain" = "None" ] && missing+=("CloudFrontDomainName")
[ -z "$function_url" ] || [ "$function_url" = "None" ] && missing+=("LambdaFunctionUrl")
[ -z "$history_api_url" ] || [ "$history_api_url" = "None" ] && missing+=("HistoryApiLambdaFunctionUrl")
[ -z "$library_api_url" ] || [ "$library_api_url" = "None" ] && missing+=("LibraryApiLambdaFunctionUrl")

if [ ${#missing[@]} -gt 0 ]; then
    print_warning "Skipping handoff file: missing stack outputs: ${missing[*]}"
    exit 0
fi

out_dir="$PROJECT_ROOT/infra/state"
mkdir -p "$out_dir"
out_file="$out_dir/${ENVIRONMENT}.frontend.handoff.env"
tmp_file=$(mktemp "${out_dir}/.${ENVIRONMENT}.frontend.handoff.env.XXXXXX")
chmod 644 "$tmp_file"

cat > "$tmp_file" <<EOF
# Generated by scripts/deploy/generate_frontend_handoff.sh — do not edit by hand
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Environment: ${ENVIRONMENT}
NUXT_PUBLIC_CHAT_API_BASE_URL=${function_url}
NUXT_PUBLIC_HISTORY_API_BASE_URL=${history_api_url}
NUXT_PUBLIC_LIBRARY_API_BASE_URL=${library_api_url}
NUXT_PUBLIC_COGNITO_REGION=${cognito_region}
NUXT_PUBLIC_COGNITO_USER_POOL_ID=${user_pool_id}
NUXT_PUBLIC_COGNITO_APP_CLIENT_ID=${app_client_id}
EOF

mv "$tmp_file" "$out_file"
print_complete "Wrote $out_file"
