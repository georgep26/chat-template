#!/bin/bash
# Tear down resources for an environment.
#
# Declared resources are deleted in reverse infra.yaml order by calling
# scripts/deploy/resources/<resource_key>.sh <env> delete.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

show_usage() {
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Options:"
    echo "  -y, --yes                 Skip confirmation prompts"
    echo "  --profile <aws-profile>   AWS profile to use for teardown"
    echo "  --config <path>           Path to infra.yaml"
    echo "  --dry-run                 Show the plan without deleting"
    echo "  --sweep                   Report likely orphaned resources after declared teardown"
    echo "  -h, --help                Show this help"
}

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

AUTO_CONFIRM=false
CONFIG_PATH=""
PROFILE_OVERRIDE=""
DRY_RUN=false
SWEEP=false

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
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
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --sweep)
            SWEEP=true
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

print_header "Environment Teardown"

if [ -n "$CONFIG_PATH" ]; then
    load_infra_config "$CONFIG_PATH" || exit 1
else
    load_infra_config || exit 1
fi

validate_environment "$ENVIRONMENT" || exit 1
validate_config "$ENVIRONMENT" || exit 1

PROJECT_ROOT=$(get_project_root)
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
ACCOUNT_KEY=$(get_environment_account_key "$ENVIRONMENT")
ACCOUNT_KIND=$(get_environment_account_kind "$ENVIRONMENT")
ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")

if [ -n "$PROFILE_OVERRIDE" ]; then
    export AWS_PROFILE="$PROFILE_OVERRIDE"
else
    RESOLVED_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
    [ "$RESOLVED_PROFILE" = "null" ] && RESOLVED_PROFILE=""
    if [ -n "$RESOLVED_PROFILE" ] && [ -z "${AWS_SESSION_TOKEN:-}" ]; then
        export AWS_PROFILE="$RESOLVED_PROFILE"
    fi
fi

ACTIVE_ACCOUNT=""
if command -v aws >/dev/null 2>&1; then
    set +e
    ACTIVE_ACCOUNT=$(aws ${AWS_PROFILE:+--profile "$AWS_PROFILE"} --region "$AWS_REGION" sts get-caller-identity --query Account --output text 2>/dev/null)
    set -e
fi

RESOURCES=()
while IFS= read -r resource; do
    [ -n "$resource" ] && RESOURCES+=("$resource")
done < <(get_resource_list)

echo ""
draw_box_top 76
draw_box_row_centered "TEARDOWN PLAN" 76
draw_box_separator 76
draw_box_row "Project:       $PROJECT_NAME" 76
draw_box_row "Environment:   $ENVIRONMENT" 76
draw_box_row "Account key:   $ACCOUNT_KEY" 76
[ -n "$ACCOUNT_ID" ] && draw_box_row "Expected acct: $ACCOUNT_ID" 76
[ -n "$ACTIVE_ACCOUNT" ] && draw_box_row "Active acct:   $ACTIVE_ACCOUNT" 76
draw_box_row "Account kind:  $ACCOUNT_KIND" 76
draw_box_row "Region:        $AWS_REGION" 76
[ -n "${AWS_PROFILE:-}" ] && draw_box_row "AWS profile:   $AWS_PROFILE" 76
draw_box_separator 76
draw_box_row "Declared resources in teardown order:" 76

for (( i=${#RESOURCES[@]}-1; i>=0; i-- )); do
    resource="${RESOURCES[$i]}"
    ownership=$(yq ".resources.${resource}.ownership // \"owned\"" "$INFRA_CONFIG_PATH")
    [ "$ownership" = "null" ] && ownership="owned"
    if [ "$ownership" = "imported" ]; then
        draw_box_row "  [SKIP]   $resource (imported)" 76
    else
        draw_box_row "  [DELETE] $resource" 76
    fi
done

draw_box_bottom 76
echo ""

if [ -n "$ACCOUNT_ID" ] && [ -n "$ACTIVE_ACCOUNT" ] && [ "$ACCOUNT_ID" != "$ACTIVE_ACCOUNT" ]; then
    print_error "AWS profile/account mismatch. Expected $ACCOUNT_ID but active credentials resolve to $ACTIVE_ACCOUNT."
    exit 1
fi

if [ "$DRY_RUN" = true ]; then
    print_complete "Dry run complete"
    exit 0
fi

if [ -z "$ACCOUNT_ID" ]; then
    print_error "No expected account_id is configured for '$ENVIRONMENT'."
    print_error "Create infra/secrets/accounts.secrets.yaml from template.accounts.secrets.yaml before teardown."
    exit 1
fi

if [ "$AUTO_CONFIRM" = false ]; then
    confirm_destructive_action "$ENVIRONMENT" "delete declared chat-template resources" || exit 0
fi

COMPLETED=()
SKIPPED=()
FAILED=()

for (( i=${#RESOURCES[@]}-1; i>=0; i-- )); do
    resource="${RESOURCES[$i]}"
    ownership=$(yq ".resources.${resource}.ownership // \"owned\"" "$INFRA_CONFIG_PATH")
    [ "$ownership" = "null" ] && ownership="owned"

    if [ "$ownership" = "imported" ]; then
        print_info "Skipping imported resource: $resource"
        SKIPPED+=("$resource")
        continue
    fi

    handler="$PROJECT_ROOT/scripts/deploy/resources/${resource}.sh"
    if [ ! -f "$handler" ]; then
        print_warning "Resource handler not found, cannot delete declared resource: $handler"
        FAILED+=("$resource")
        continue
    fi

    chmod +x "$handler"
    print_step "Deleting resource: $resource"
    set +e
    "$handler" "$ENVIRONMENT" delete -y
    rc=$?
    set -e

    if [ $rc -ne 0 ]; then
        print_error "Resource delete failed: $resource (exit code $rc)"
        FAILED+=("$resource")
    else
        COMPLETED+=("$resource")
    fi
done

if [ "$SWEEP" = true ]; then
    report_script="$PROJECT_ROOT/scripts/deploy/report_resources.sh"
    if [ -f "$report_script" ]; then
        "$report_script" "$ENVIRONMENT"
    else
        print_warning "Resource report script not found; orphan sweep report skipped"
    fi
fi

echo ""
draw_box_top 76
draw_box_row_centered "TEARDOWN COMPLETE" 76
draw_box_separator 76
draw_box_row "Deleted: ${#COMPLETED[@]}" 76
draw_box_row "Skipped: ${#SKIPPED[@]}" 76
draw_box_row "Failed:  ${#FAILED[@]}" 76
draw_box_bottom 76
echo ""

if [ ${#FAILED[@]} -gt 0 ]; then
    print_error "Some resources failed to delete:"
    for resource in "${FAILED[@]}"; do
        print_error "  - $resource"
    done
    exit 1
fi

print_complete "Teardown completed"
