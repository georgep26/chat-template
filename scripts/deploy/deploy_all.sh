#!/bin/bash
# Deploy enabled resources for an environment and deployment set.
#
# Resource order comes from infra/infra.yaml. Deployment sets can filter the
# enabled resource list without changing that order. Each resource key maps by
# convention to scripts/deploy/resources/<resource_key>.sh.

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
    echo "  --profile <aws-profile>   AWS profile to use for this deployment"
    echo "  --set <deployment-set>    Deployment set from infra.yaml (default: full)"
    echo "  --deployment-set <name>   Alias for --set"
    echo "  --config <path>           Path to infra.yaml"
    echo "  --dry-run                 Show the plan without deploying"
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
DEPLOYMENT_SET="full"

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
        --set|--deployment-set)
            DEPLOYMENT_SET="$2"
            shift 2
            ;;
        --set=*|--deployment-set=*)
            DEPLOYMENT_SET="${1#*=}"
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

print_header "Full Environment Deployment"

if [ -n "$CONFIG_PATH" ]; then
    load_infra_config "$CONFIG_PATH" || exit 1
else
    load_infra_config || exit 1
fi

validate_environment "$ENVIRONMENT" || exit 1
validate_config "$ENVIRONMENT" || exit 1
validate_deployment_set "$DEPLOYMENT_SET" || exit 1

PROJECT_ROOT=$(get_project_root)
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
ACCOUNT_KEY=$(get_environment_account_key "$ENVIRONMENT")
ACCOUNT_KIND=$(get_environment_account_kind "$ENVIRONMENT")
ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")
GLOBAL_SUFFIX=$(get_environment_global_suffix "$ENVIRONMENT")

if [ -n "$PROFILE_OVERRIDE" ]; then
    export AWS_PROFILE="$PROFILE_OVERRIDE"
else
    RESOLVED_PROFILE=$(get_environment_profile "$ENVIRONMENT")
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

echo ""
draw_box_top 76
draw_box_row_centered "DEPLOYMENT PLAN" 76
draw_box_separator 76
draw_box_row "Project:       $PROJECT_NAME" 76
draw_box_row "Environment:   $ENVIRONMENT" 76
draw_box_row "Deploy set:    $DEPLOYMENT_SET" 76
draw_box_row "Account key:   $ACCOUNT_KEY" 76
[ -n "$ACCOUNT_ID" ] && draw_box_row "Expected acct: $ACCOUNT_ID" 76
[ -n "$ACTIVE_ACCOUNT" ] && draw_box_row "Active acct:   $ACTIVE_ACCOUNT" 76
draw_box_row "Account kind:  $ACCOUNT_KIND" 76
draw_box_row "Region:        $AWS_REGION" 76
[ -n "${AWS_PROFILE:-}" ] && draw_box_row "AWS profile:   $AWS_PROFILE" 76
draw_box_row "Global suffix: $GLOBAL_SUFFIX" 76
draw_box_separator 76
draw_box_row "Selected enabled resources in deployment order:" 76

ENABLED_RESOURCES=()
while IFS= read -r resource; do
    [ -z "$resource" ] && continue
    ENABLED_RESOURCES+=("$resource")
    handler="$PROJECT_ROOT/scripts/deploy/resources/${resource}.sh"
    if [ -f "$handler" ]; then
        draw_box_row "  [DEPLOY] $resource" 76
    else
        draw_box_row "  [MISSING] $resource handler not found" 76
    fi
done < <(get_deployment_set_resources "$DEPLOYMENT_SET")

draw_box_bottom 76
echo ""

if [ "$ACCOUNT_KIND" = "management" ]; then
    print_warning "This environment targets the management account. AWS recommends keeping application workloads in member or sandbox accounts when possible."
fi

if [ -n "$ACCOUNT_ID" ] && [ -n "$ACTIVE_ACCOUNT" ] && [ "$ACCOUNT_ID" != "$ACTIVE_ACCOUNT" ]; then
    print_error "AWS profile/account mismatch. Expected $ACCOUNT_ID but active credentials resolve to $ACTIVE_ACCOUNT."
    exit 1
fi

if [ ${#ENABLED_RESOURCES[@]} -eq 0 ]; then
    print_warning "No enabled resources found for deployment"
    exit 0
fi

if [ "$DRY_RUN" = true ]; then
    print_complete "Dry run complete"
    exit 0
fi

if [ -z "$ACCOUNT_ID" ]; then
    print_error "No expected account_id is configured for '$ENVIRONMENT'."
    print_error "Create infra/secrets/accounts.secrets.yaml from template.accounts.secrets.yaml before deploying."
    exit 1
fi

if [ "$AUTO_CONFIRM" = false ]; then
    confirm_deployment "Deploy these resources?" || exit 0
fi

COMPLETED=()
FAILED=()

for resource in "${ENABLED_RESOURCES[@]}"; do
    handler="$PROJECT_ROOT/scripts/deploy/resources/${resource}.sh"
    if [ ! -f "$handler" ]; then
        print_error "Resource handler not found: $handler"
        FAILED+=("$resource")
        break
    fi

    chmod +x "$handler"
    print_step "Deploying resource: $resource"
    set +e
    "$handler" "$ENVIRONMENT" deploy -y
    rc=$?
    set -e

    if [ $rc -ne 0 ]; then
        print_error "Resource deployment failed: $resource (exit code $rc)"
        FAILED+=("$resource")
        break
    fi
    COMPLETED+=("$resource")
done

if [ ${#FAILED[@]} -gt 0 ]; then
    print_error "Deployment stopped because a resource failed"
    exit 1
fi

if is_cost_control_enabled && is_deployment_set_cost_control_enabled "$DEPLOYMENT_SET"; then
    cost_guard_script="$PROJECT_ROOT/scripts/deploy/deploy_cost_guard.sh"
    if [ -f "$cost_guard_script" ]; then
        print_step "Deploying cost guard"
        "$cost_guard_script" "$ENVIRONMENT" deploy -y
        COMPLETED+=("cost_guard")
    fi
fi

cost_tags_script="$PROJECT_ROOT/scripts/deploy/deploy_cost_analysis_tags.sh"
if is_deployment_set_cost_control_enabled "$DEPLOYMENT_SET" && [ -f "$cost_tags_script" ]; then
    print_step "Activating cost allocation tags"
    "$cost_tags_script" activate || print_warning "Cost allocation tag activation failed or tags are not available yet"
fi

if is_deployment_set_config_enabled "$DEPLOYMENT_SET"; then
    print_step "Hydrating and deploying application config"
    hydrate_script="$PROJECT_ROOT/scripts/utils/hydrate_configs.sh"
    sync_script="$PROJECT_ROOT/scripts/deploy/sync_app_config.sh"
    configs_script="$PROJECT_ROOT/scripts/deploy/deploy_configs.sh"
    hydrated_configs_script="$PROJECT_ROOT/scripts/deploy/deploy_hydrated_configs.sh"

    if [ -f "$hydrate_script" ] && [ -f "$sync_script" ] && [ -f "$configs_script" ] && [ -f "$hydrated_configs_script" ]; then
        chmod +x "$hydrated_configs_script"
        "$hydrate_script" --run "$ENVIRONMENT" -- "$hydrated_configs_script" "$ENVIRONMENT"
    else
        print_warning "Config hydration/sync/upload scripts are incomplete; skipping config deployment"
    fi
else
    print_info "Skipping config deployment for deployment set: $DEPLOYMENT_SET"
fi

# Generate the frontend handoff file if all upstream stacks (cognito, frontend,
# rag_lambda) are present. The script no-ops with a warning when any are missing,
# so partial deployment sets (e.g. --set pr) won't fail this step.
handoff_script="$PROJECT_ROOT/scripts/deploy/generate_frontend_handoff.sh"
if [ -f "$handoff_script" ]; then
    chmod +x "$handoff_script"
    "$handoff_script" "$ENVIRONMENT" || print_warning "Frontend handoff generation failed (non-fatal)"
fi

echo ""
draw_box_top 76
draw_box_row_centered "DEPLOYMENT COMPLETE" 76
draw_box_separator 76
draw_box_row "Environment: $ENVIRONMENT" 76
draw_box_row "Deploy set:  $DEPLOYMENT_SET" 76
draw_box_row "Completed:   ${#COMPLETED[@]} resource step(s)" 76
draw_box_bottom 76
echo ""

for resource in "${COMPLETED[@]}"; do
    print_info "Deployed: $resource"
done

print_complete "Full deployment completed"
