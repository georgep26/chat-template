#!/bin/bash

# Cognito Deployment Script
# Deploys a Cognito User Pool and SPA app client for frontend authentication.
# Writes deployed Cognito identifiers to the environment secrets file
# (config_secrets.COGNITO_*) for downstream Lambda/auth configuration.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

RESOURCE_NAME="cognito"
RESOURCE_DISPLAY_NAME="Cognito Auth"

show_usage() {
    echo "Cognito Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments: dev | staging | prod"
    echo "Actions: deploy (default) | update | delete | validate | status"
    echo ""
    echo "Options:"
    echo "  --user-pool-name <name>         - Override user pool name"
    echo "  --app-client-name <name>        - Override app client name"
    echo "  --callback-urls <csv>           - OAuth callback URLs (comma-separated)"
    echo "  --logout-urls <csv>             - OAuth logout URLs (comma-separated)"
    echo "  --domain-prefix <prefix>        - Optional Cognito hosted UI domain prefix"
    echo "  --generate-client-secret <bool> - true or false (SPA default: false)"
    echo "  --region <region>               - AWS region override"
    echo "  -y, --yes                       - Skip confirmation prompt"
    echo ""
    echo "Examples:"
    echo "  $0 dev deploy -y"
    echo "  $0 staging deploy --callback-urls https://staging.example.com/auth/callback --logout-urls https://staging.example.com/"
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
AWS_REGION_OVERRIDE=""
USER_POOL_NAME_OVERRIDE=""
APP_CLIENT_NAME_OVERRIDE=""
CALLBACK_URLS_OVERRIDE=""
LOGOUT_URLS_OVERRIDE=""
DOMAIN_PREFIX_OVERRIDE=""
GENERATE_CLIENT_SECRET_OVERRIDE=""

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
    case $1 in
        deploy|update|delete|validate|status)
            ACTION="$1"
            shift
            ;;
        --user-pool-name)
            USER_POOL_NAME_OVERRIDE="$2"
            shift 2
            ;;
        --app-client-name)
            APP_CLIENT_NAME_OVERRIDE="$2"
            shift 2
            ;;
        --callback-urls)
            CALLBACK_URLS_OVERRIDE="$2"
            shift 2
            ;;
        --logout-urls)
            LOGOUT_URLS_OVERRIDE="$2"
            shift 2
            ;;
        --domain-prefix)
            DOMAIN_PREFIX_OVERRIDE="$2"
            shift 2
            ;;
        --generate-client-secret)
            GENERATE_CLIENT_SECRET_OVERRIDE="$2"
            shift 2
            ;;
        --region)
            AWS_REGION_OVERRIDE="$2"
            shift 2
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
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

validate_environment "$ENVIRONMENT" || exit 1

# Hydrate in a runtime-only scope for deploy/update, then re-run this script with hydration disabled.
# This ensures summary/output reflects hydrated values while restoring files even on failure/cancel.
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

print_step "Loading configuration for $ENVIRONMENT environment"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
[ -n "$AWS_REGION_OVERRIDE" ] && AWS_REGION="$AWS_REGION_OVERRIDE"

# Use deployer profile for local runs. In CI, OIDC env credentials are used.
if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
    [ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
    [ -n "$AWS_PROFILE" ] && print_info "Using deployer profile: $AWS_PROFILE"
fi

STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME")

if [ -z "$STACK_NAME" ] || [ -z "$TEMPLATE_FILE" ]; then
    print_error "Cognito resource is not configured in infra/infra.yaml (resources.cognito)."
    exit 1
fi

USER_POOL_NAME=$(get_resource_config "$RESOURCE_NAME" "user_pool_name" "$ENVIRONMENT")
[ "$USER_POOL_NAME" = "null" ] && USER_POOL_NAME=""
[ -n "$USER_POOL_NAME_OVERRIDE" ] && USER_POOL_NAME="$USER_POOL_NAME_OVERRIDE"
[ -z "$USER_POOL_NAME" ] && USER_POOL_NAME="${PROJECT_NAME}-${ENVIRONMENT}-users"

APP_CLIENT_NAME=$(get_resource_config "$RESOURCE_NAME" "app_client_name" "$ENVIRONMENT")
[ "$APP_CLIENT_NAME" = "null" ] && APP_CLIENT_NAME=""
[ -n "$APP_CLIENT_NAME_OVERRIDE" ] && APP_CLIENT_NAME="$APP_CLIENT_NAME_OVERRIDE"
[ -z "$APP_CLIENT_NAME" ] && APP_CLIENT_NAME="${PROJECT_NAME}-${ENVIRONMENT}-spa-client"

CALLBACK_URLS=$(get_resource_config "$RESOURCE_NAME" "callback_urls" "$ENVIRONMENT")
[ "$CALLBACK_URLS" = "null" ] && CALLBACK_URLS=""
[ -n "$CALLBACK_URLS_OVERRIDE" ] && CALLBACK_URLS="$CALLBACK_URLS_OVERRIDE"
[ -z "$CALLBACK_URLS" ] && CALLBACK_URLS="http://localhost:5173/auth/callback"

LOGOUT_URLS=$(get_resource_config "$RESOURCE_NAME" "logout_urls" "$ENVIRONMENT")
[ "$LOGOUT_URLS" = "null" ] && LOGOUT_URLS=""
[ -n "$LOGOUT_URLS_OVERRIDE" ] && LOGOUT_URLS="$LOGOUT_URLS_OVERRIDE"
[ -z "$LOGOUT_URLS" ] && LOGOUT_URLS="http://localhost:5173/"

DOMAIN_PREFIX=$(get_resource_config "$RESOURCE_NAME" "domain_prefix" "$ENVIRONMENT")
[ "$DOMAIN_PREFIX" = "null" ] && DOMAIN_PREFIX=""
[ -n "$DOMAIN_PREFIX_OVERRIDE" ] && DOMAIN_PREFIX="$DOMAIN_PREFIX_OVERRIDE"

GENERATE_CLIENT_SECRET=$(get_resource_config "$RESOURCE_NAME" "generate_client_secret" "$ENVIRONMENT")
[ "$GENERATE_CLIENT_SECRET" = "null" ] && GENERATE_CLIENT_SECRET=""
[ -n "$GENERATE_CLIENT_SECRET_OVERRIDE" ] && GENERATE_CLIENT_SECRET="$GENERATE_CLIENT_SECRET_OVERRIDE"
[ -z "$GENERATE_CLIENT_SECRET" ] && GENERATE_CLIENT_SECRET="false"

GENERATE_CLIENT_SECRET=$(echo "$GENERATE_CLIENT_SECRET" | tr '[:upper:]' '[:lower:]')
case "$GENERATE_CLIENT_SECRET" in
    true|false) ;;
    *)
        print_error "Invalid --generate-client-secret value: $GENERATE_CLIENT_SECRET (expected true or false)"
        exit 1
        ;;
esac

PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

stack_exists() {
    aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1
}

write_cognito_outputs_to_secrets() {
    local user_pool_id=$1
    local app_client_id=$2
    local issuer_url=$3
    local hosted_ui_domain=$4

    if ! command -v yq >/dev/null 2>&1; then
        print_warning "yq not found; skipping write of Cognito outputs to secrets file"
        return 0
    fi

    local secrets_file
    secrets_file=$(get_environment_secrets_file "$ENVIRONMENT" 2>/dev/null || true)
    if [ -z "$secrets_file" ] || [ ! -f "$secrets_file" ]; then
        print_warning "Secrets file not found for $ENVIRONMENT; Cognito outputs not persisted"
        return 0
    fi

    yq -i ".config_secrets.COGNITO_REGION = \"${AWS_REGION}\"" "$secrets_file"
    yq -i ".config_secrets.COGNITO_USER_POOL_ID = \"${user_pool_id}\"" "$secrets_file"
    yq -i ".config_secrets.COGNITO_APP_CLIENT_ID = \"${app_client_id}\"" "$secrets_file"
    if [ -n "$issuer_url" ] && [ "$issuer_url" != "None" ] && [ "$issuer_url" != "null" ]; then
        yq -i ".config_secrets.COGNITO_ISSUER_URL = \"${issuer_url}\"" "$secrets_file"
    else
        yq -i '.config_secrets.COGNITO_ISSUER_URL = ""' "$secrets_file"
    fi
    if [ -n "$hosted_ui_domain" ] && [ "$hosted_ui_domain" != "None" ] && [ "$hosted_ui_domain" != "null" ]; then
        yq -i ".config_secrets.COGNITO_HOSTED_UI_DOMAIN = \"${hosted_ui_domain}\"" "$secrets_file"
    else
        yq -i '.config_secrets.COGNITO_HOSTED_UI_DOMAIN = ""' "$secrets_file"
    fi

    print_complete "Wrote Cognito IDs to $secrets_file (config_secrets)"
}

clear_cognito_outputs_from_secrets() {
    if ! command -v yq >/dev/null 2>&1; then
        return 0
    fi

    local secrets_file
    secrets_file=$(get_environment_secrets_file "$ENVIRONMENT" 2>/dev/null || true)
    if [ -z "$secrets_file" ] || [ ! -f "$secrets_file" ]; then
        return 0
    fi

    yq -i '.config_secrets.COGNITO_USER_POOL_ID = ""' "$secrets_file"
    yq -i '.config_secrets.COGNITO_APP_CLIENT_ID = ""' "$secrets_file"
    yq -i '.config_secrets.COGNITO_ISSUER_URL = ""' "$secrets_file"
    yq -i '.config_secrets.COGNITO_HOSTED_UI_DOMAIN = ""' "$secrets_file"
}

do_validate_template() {
    print_step "Validating CloudFormation template..."
    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi

    if aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" >/dev/null 2>&1; then
        print_complete "Template validation successful"
    else
        print_error "Template validation failed"
        exit 1
    fi
}

show_status() {
    print_step "Checking stack status: $STACK_NAME"
    if stack_exists; then
        aws_cmd cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_info "Stack outputs:"
        aws_cmd cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

deploy_stack() {
    print_resource_summary "$RESOURCE_NAME" "$ENVIRONMENT" "$ACTION"
    print_info "User Pool Name: $USER_POOL_NAME"
    print_info "App Client Name: $APP_CLIENT_NAME"
    print_info "Callback URLs: $CALLBACK_URLS"
    print_info "Logout URLs: $LOGOUT_URLS"
    if [ -n "$DOMAIN_PREFIX" ]; then
        print_info "Hosted UI Domain Prefix: $DOMAIN_PREFIX"
    else
        print_info "Hosted UI Domain Prefix: (disabled)"
    fi
    print_info "Generate Client Secret: $GENERATE_CLIENT_SECRET"

    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment || exit 0
    fi

    print_step "Deploying CloudFormation stack: $STACK_NAME"
    local param_file
    param_file=$(mktemp)
    trap "rm -f $param_file" EXIT

    cat > "$param_file" <<EOF
[
  {"ParameterKey":"ProjectName","ParameterValue":"$PROJECT_NAME"},
  {"ParameterKey":"Environment","ParameterValue":"$ENVIRONMENT"},
  {"ParameterKey":"UserPoolName","ParameterValue":"$USER_POOL_NAME"},
  {"ParameterKey":"AppClientName","ParameterValue":"$APP_CLIENT_NAME"},
  {"ParameterKey":"CallbackUrls","ParameterValue":"$CALLBACK_URLS"},
  {"ParameterKey":"LogoutUrls","ParameterValue":"$LOGOUT_URLS"},
  {"ParameterKey":"CognitoDomainPrefix","ParameterValue":"$DOMAIN_PREFIX"},
  {"ParameterKey":"GenerateClientSecret","ParameterValue":"$GENERATE_CLIENT_SECRET"}
]
EOF

    local no_updates=false
    local stack_was_existing=false
    if stack_exists; then
        stack_was_existing=true
        print_info "Stack $STACK_NAME already exists. Updating..."
        local update_output
        update_output=$(aws_cmd cloudformation update-stack \
            --stack-name "$STACK_NAME" \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" 2>&1) || {
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_info "No updates needed for stack $STACK_NAME"
                no_updates=true
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
            --parameters "file://$param_file"
    fi

    if [ "$no_updates" = false ]; then
        print_info "Waiting for stack operation to complete..."
        local wait_cmd="stack-create-complete"
        [ "$stack_was_existing" = true ] && wait_cmd="stack-update-complete"
        if aws_cmd cloudformation wait "$wait_cmd" --stack-name "$STACK_NAME"; then
            print_complete "Stack deployment completed successfully"
        else
            print_error "Stack deployment failed"
            exit 1
        fi
    fi

    local user_pool_id app_client_id issuer_url hosted_ui_domain
    user_pool_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue | [0]' \
        --output text)
    app_client_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`AppClientId`].OutputValue | [0]' \
        --output text)
    issuer_url=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`IssuerUrl`].OutputValue | [0]' \
        --output text)
    hosted_ui_domain=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`HostedUiDomain`].OutputValue | [0]' \
        --output text 2>/dev/null || true)

    if [ -z "$user_pool_id" ] || [ "$user_pool_id" = "None" ] || [ -z "$app_client_id" ] || [ "$app_client_id" = "None" ]; then
        print_error "Failed to read Cognito outputs from stack"
        exit 1
    fi

    print_complete "Cognito deployed successfully"
    print_info "User Pool ID: $user_pool_id"
    print_info "App Client ID: $app_client_id"
    [ -n "$issuer_url" ] && [ "$issuer_url" != "None" ] && print_info "Issuer URL: $issuer_url"
    [ -n "$hosted_ui_domain" ] && [ "$hosted_ui_domain" != "None" ] && print_info "Hosted UI Domain: $hosted_ui_domain"

    write_cognito_outputs_to_secrets "$user_pool_id" "$app_client_id" "$issuer_url" "$hosted_ui_domain" || true
}

delete_stack() {
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete Cognito stack ($STACK_NAME)" || exit 0
    fi

    if stack_exists; then
        print_step "Deleting stack: $STACK_NAME"
        aws_cmd cloudformation delete-stack --stack-name "$STACK_NAME"
        aws_cmd cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
        print_complete "Stack deleted successfully"
        clear_cognito_outputs_from_secrets || true
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

case "$ACTION" in
    deploy|update)
        deploy_stack
        ;;
    delete)
        delete_stack
        ;;
    validate)
        do_validate_template
        ;;
    status)
        show_status
        ;;
    *)
        print_error "Unknown action: $ACTION"
        show_usage
        exit 1
        ;;
esac
