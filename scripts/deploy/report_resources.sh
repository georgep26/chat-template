#!/bin/bash
# Report declared and likely deployed resources for an environment.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"

show_usage() {
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Options:"
    echo "  --profile <aws-profile>   AWS profile to use"
    echo "  --config <path>           Path to infra.yaml"
    echo "  --json                    Output compact JSON"
    echo "  -h, --help                Show this help"
}

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

CONFIG_PATH=""
PROFILE_OVERRIDE=""
JSON_OUTPUT=false

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
        --config)
            CONFIG_PATH="$2"
            shift 2
            ;;
        --config=*)
            CONFIG_PATH="${1#--config=}"
            shift
            ;;
        --json)
            JSON_OUTPUT=true
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

if [ -n "$CONFIG_PATH" ]; then
    load_infra_config "$CONFIG_PATH" || exit 1
else
    load_infra_config || exit 1
fi

validate_environment "$ENVIRONMENT" || exit 1

AWS_REGION=$(get_environment_region "$ENVIRONMENT")
ACCOUNT_KEY=$(get_environment_account_key "$ENVIRONMENT")
ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")
PROJECT_NAME=$(get_project_name)
PROFILE="$PROFILE_OVERRIDE"
if [ -z "$PROFILE" ]; then
    PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
    [ "$PROFILE" = "null" ] && PROFILE=""
fi

aws_report_cmd() {
    if [ -n "$PROFILE" ]; then
        aws --profile "$PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

ACTIVE_ACCOUNT=""
if command -v aws >/dev/null 2>&1; then
    ACTIVE_ACCOUNT=$(aws_report_cmd sts get-caller-identity --query Account --output text 2>/dev/null || true)
fi

if [ "$JSON_OUTPUT" = true ]; then
    echo "{"
    echo "  \"environment\": \"$ENVIRONMENT\","
    echo "  \"account_key\": \"$ACCOUNT_KEY\","
    echo "  \"expected_account_id\": \"$ACCOUNT_ID\","
    echo "  \"active_account_id\": \"$ACTIVE_ACCOUNT\","
    echo "  \"region\": \"$AWS_REGION\","
    echo "  \"resources\": ["
fi

if [ "$JSON_OUTPUT" = false ]; then
    print_header "Resource Report"
    print_info "Environment: $ENVIRONMENT"
    print_info "Account key: $ACCOUNT_KEY"
    [ -n "$ACCOUNT_ID" ] && print_info "Expected account: $ACCOUNT_ID"
    [ -n "$ACTIVE_ACCOUNT" ] && print_info "Active account: $ACTIVE_ACCOUNT"
    print_info "Region: $AWS_REGION"
    echo ""
fi

first_json=true
DECLARED_STACKS="|"
while IFS= read -r resource; do
    [ -z "$resource" ] && continue
    stack_name=$(get_resource_stack_name "$resource" "$ENVIRONMENT" 2>/dev/null || true)
    enabled=$(yq ".resources.${resource}.enabled // false" "$INFRA_CONFIG_PATH")
    ownership=$(yq ".resources.${resource}.ownership // \"owned\"" "$INFRA_CONFIG_PATH")
    [ "$ownership" = "null" ] && ownership="owned"
    status="NOT_CHECKED"

    if [ -n "$stack_name" ]; then
        DECLARED_STACKS="${DECLARED_STACKS}${stack_name}|"
    fi

    if [ -n "$stack_name" ] && command -v aws >/dev/null 2>&1 && [ -n "$ACTIVE_ACCOUNT" ]; then
        status=$(aws_report_cmd cloudformation describe-stacks \
            --stack-name "$stack_name" \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null || echo "NOT_FOUND")
    elif [ -z "$stack_name" ]; then
        status="NO_STACK"
    fi

    if [ "$JSON_OUTPUT" = true ]; then
        if [ "$first_json" = true ]; then
            first_json=false
        else
            echo ","
        fi
        printf '    {"key":"%s","enabled":%s,"ownership":"%s","stack_name":"%s","status":"%s"}' \
            "$resource" "$enabled" "$ownership" "$stack_name" "$status"
    else
        print_info "$resource"
        print_info "  enabled:   $enabled"
        print_info "  ownership: $ownership"
        [ -n "$stack_name" ] && print_info "  stack:     $stack_name"
        print_info "  status:    $status"
    fi
done < <(get_resource_list)

ORPHAN_STACKS=()
if command -v aws >/dev/null 2>&1 && [ -n "$ACTIVE_ACCOUNT" ]; then
    while IFS=$'\t' read -r stack_name stack_status; do
        [ -z "$stack_name" ] && continue
        if [[ "$stack_name" == "$PROJECT_NAME"* && "$stack_name" == *"-$ENVIRONMENT"* && "$DECLARED_STACKS" != *"|$stack_name|"* ]]; then
            ORPHAN_STACKS+=("$stack_name|$stack_status")
        fi
    done < <(aws_report_cmd cloudformation list-stacks \
        --stack-status-filter CREATE_COMPLETE CREATE_FAILED ROLLBACK_COMPLETE DELETE_FAILED UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE UPDATE_ROLLBACK_FAILED IMPORT_COMPLETE IMPORT_ROLLBACK_COMPLETE \
        --query 'StackSummaries[].[StackName,StackStatus]' \
        --output text 2>/dev/null || true)
fi

if [ "$JSON_OUTPUT" = true ]; then
    echo ""
    echo "  ],"
    echo "  \"orphan_stacks\": ["
    first_orphan=true
    for orphan in "${ORPHAN_STACKS[@]}"; do
        stack_name="${orphan%%|*}"
        stack_status="${orphan#*|}"
        if [ "$first_orphan" = true ]; then
            first_orphan=false
        else
            echo ","
        fi
        printf '    {"stack_name":"%s","status":"%s"}' "$stack_name" "$stack_status"
    done
    echo ""
    echo "  ]"
    echo "}"
else
    if [ ${#ORPHAN_STACKS[@]} -gt 0 ]; then
        echo ""
        print_warning "Likely orphan stacks:"
        for orphan in "${ORPHAN_STACKS[@]}"; do
            stack_name="${orphan%%|*}"
            stack_status="${orphan#*|}"
            print_warning "  $stack_name ($stack_status)"
        done
    fi
    echo ""
    print_complete "Resource report complete"
fi
