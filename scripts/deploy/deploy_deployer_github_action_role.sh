#!/bin/bash

# GitHub Actions Deployer IAM Role Deployment Script
# Deploys the IAM role used by the deploy workflow (.github/workflows/deploy.yml) for OIDC.
# Reads infra/infra.yaml and uses the CLI role profile per environment. GitHub org/repo
# from github.github_repo; OIDC provider discovered in account when not passed.
# On successful deploy, by default writes the role ARN to the environment's secrets file
# (github_environment_secrets.AWS_DEPLOYER_ROLE_ARN and config_secrets.DEPLOYER_ROLE_ARN).
# Use --write-to-infra to also (or instead) write to infra/infra.yaml.
#
# Usage Examples:
#   ./scripts/deploy/deploy_deployer_github_action_role.sh dev deploy
#   ./scripts/deploy/deploy_deployer_github_action_role.sh dev deploy -y
#   ./scripts/deploy/deploy_deployer_github_action_role.sh dev status

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/github_repo.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

show_usage() {
    echo "GitHub Actions Deployer Role Deployment Script"
    echo ""
    echo "Reads infra/infra.yaml and uses CLI role profile per environment."
    echo "GitHub org/repo from github.github_repo; OIDC provider discovered if not passed."
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo "Environments: dev, staging, prod"
    echo "Actions: deploy (default), update, delete, validate, status"
    echo ""
    echo "Options (override infra when provided):"
    echo "  --github-org <org>       - Override GitHub org"
    echo "  --github-repo <repo>      - Override GitHub repo"
    echo "  --oidc-provider-arn <arn> - Override OIDC provider (default: discover in account)"
    echo "  --region <region>        - Override AWS region"
    echo "  --project-name <name>    - Override project name"
    echo "  -y, --yes                - Skip confirmation prompt"
    echo "  --write-to-infra         - Also write role ARN to infra/infra.yaml (default: write to env secrets file only)"
    echo ""
    echo "Example: $0 dev deploy -y"
}

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

ACTION="deploy"
if [[ $# -gt 0 && "$1" =~ ^(deploy|update|delete|validate|status)$ ]]; then
    ACTION=$1
    shift
fi

OIDC_PROVIDER_ARN=""
GITHUB_ORG=""
GITHUB_REPO=""
AUTO_CONFIRM=false
WRITE_TO_INFRA=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --write-to-infra)
            WRITE_TO_INFRA=true
            shift
            ;;
        --github-org)
            GITHUB_ORG="$2"
            shift 2
            ;;
        --github-repo)
            GITHUB_REPO="$2"
            shift 2
            ;;
        --region)
            AWS_REGION_OVERRIDE="$2"
            shift 2
            ;;
        --oidc-provider-arn)
            OIDC_PROVIDER_ARN="$2"
            shift 2
            ;;
        --project-name)
            PROJECT_NAME_OVERRIDE="$2"
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

validate_environment "$ENVIRONMENT" || exit 1

PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

PROJECT_NAME=$(get_project_name)
[ -n "${PROJECT_NAME_OVERRIDE:-}" ] && PROJECT_NAME="$PROJECT_NAME_OVERRIDE"
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
[ -n "${AWS_REGION_OVERRIDE:-}" ] && AWS_REGION="$AWS_REGION_OVERRIDE"
AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
[ -z "$GITHUB_ORG" ] && GITHUB_ORG=$(get_github_org 2>/dev/null || echo "")
[ -z "$GITHUB_REPO" ] && GITHUB_REPO=$(get_github_repo 2>/dev/null || echo "")
if [ -z "$GITHUB_ORG" ] || [ -z "$GITHUB_REPO" ]; then
    if resolve_github_org_repo; then
        [ -z "$GITHUB_ORG" ] && GITHUB_ORG="$RESOLVED_GITHUB_ORG"
        [ -z "$GITHUB_REPO" ] && GITHUB_REPO="$RESOLVED_GITHUB_REPO"
        print_info "GitHub org/repo from git remote: ${GITHUB_ORG}/${GITHUB_REPO}"
    fi
fi

ROLE_STACK=$(get_role_stack_name "deployer" "$ENVIRONMENT")
ROLE_TEMPLATE=$(get_role_template "deployer")

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

get_oidc_provider_arn() {
    aws_cmd iam list-open-id-connect-providers \
        --query "OpenIDConnectProviderList[?ends_with(Arn, 'token.actions.githubusercontent.com')].Arn" \
        --output text 2>/dev/null
}

if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    if [ -z "$GITHUB_ORG" ] || [ -z "$GITHUB_REPO" ]; then
        print_error "GitHub org and repo are required. Set github.github_repo in infra/infra.yaml, run from a repo with origin pointing at GitHub, or use --github-org and --github-repo"
        exit 1
    fi
    if [ -z "$OIDC_PROVIDER_ARN" ]; then
        OIDC_PROVIDER_ARN=$(get_oidc_provider_arn) || true
        if [ -z "$OIDC_PROVIDER_ARN" ] || [ "$OIDC_PROVIDER_ARN" = "None" ]; then
            print_error "GitHub OIDC provider not found. Run setup_oidc_provider.sh first or pass --oidc-provider-arn"
            exit 1
        fi
        print_info "Using OIDC provider: $OIDC_PROVIDER_ARN"
    fi
    if [ "$AUTO_CONFIRM" = false ]; then
        print_header "Deployer role deployment for $ENVIRONMENT"
        print_step "Environment: $ENVIRONMENT | Region: $AWS_REGION | Stack: $ROLE_STACK"
        confirm_deployment "Proceed with $ACTION deployer role?" || exit 0
    fi
fi

# Function to validate template
validate_template() {
    local template_file=$1
    local stack_name=$2

    print_info "Validating CloudFormation template: $template_file"
    if aws_cmd cloudformation validate-template --template-body "file://$template_file" >/dev/null 2>&1; then
        print_info "Template validation successful: $stack_name"
    else
        print_error "Template validation failed: $template_file"
        exit 1
    fi
}

# Function to check stack status and print errors
check_stack_status() {
    local stack_name=$1
    local stack_status=$(aws_cmd cloudformation describe-stacks \
        --stack-name $stack_name \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null)

    if [ -z "$stack_status" ]; then
        return 1
    fi

    case "$stack_status" in
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED|ROLLBACK_FAILED)
            print_error "Stack $stack_name is in failed state: $stack_status"
            echo ""

            # Get stack status reason
            local status_reason=$(aws_cmd cloudformation describe-stacks \
                --stack-name $stack_name \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)

            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Stack Status Reason: $status_reason"
                echo ""
            fi

            # Get recent stack events with errors
            print_error "Recent stack events with errors:"
            echo ""
            aws_cmd cloudformation describe-stack-events \
                --stack-name $stack_name \
                --max-items 20 \
                --query 'StackEvents[?contains(ResourceStatus, `FAILED`) || contains(ResourceStatus, `ROLLBACK`)].{Time:Timestamp,Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}' \
                --output table 2>/dev/null || true

            echo ""
            print_error "For more details, check the AWS Console or run:"
            print_error "aws cloudformation describe-stack-events --stack-name $stack_name --region $AWS_REGION"
            echo ""
            return 1
            ;;
        CREATE_COMPLETE|UPDATE_COMPLETE)
            return 0
            ;;
        *)
            return 0
            ;;
    esac
}

# Function to get stack output
get_stack_output() {
    local stack_name=$1
    local output_key=$2
    aws_cmd cloudformation describe-stacks \
        --stack-name $stack_name \
        --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
        --output text 2>/dev/null
}

# Ensure the deployer profile exists in ~/.aws/config with credential_process pointing at assume_role_for_cli.sh.
# Only appends if the profile does not already exist.
ensure_deployer_profile_in_aws_config() {
    local config_file="${AWS_CONFIG_FILE:-$HOME/.aws/config}"
    local deployer_profile_name
    deployer_profile_name="$(get_environment_profile "$ENVIRONMENT")"
    [ "$deployer_profile_name" = "null" ] && deployer_profile_name="${PROJECT_NAME}-${ENVIRONMENT}-deployer"
    if [ -z "$deployer_profile_name" ]; then
        return 0
    fi
    if [ -f "$config_file" ] && grep -qE '^\s*\[profile\s+'"$(sed 's/[.[\*^$()+?{|]/\\&/g' <<< "$deployer_profile_name")"'\s*\]' "$config_file"; then
        print_info "AWS config profile [profile ${deployer_profile_name}] already exists; skipping."
        return 0
    fi
    local assume_script_path
    assume_script_path="$(cd "$SCRIPT_DIR/.." && pwd)/utils/assume_role_for_cli.sh"
    if [ ! -x "$assume_script_path" ]; then
        print_warning "assume_role_for_cli.sh not found at ${assume_script_path}; skipping deployer profile write."
        return 0
    fi
    # Use management-admin profile as source (same as CLI profiles).
    # The credential chain is: management-admin → OrgAccessRole → deployer role.
    # We must NOT use the CLI profile here — it's already inside the member account
    # and cannot assume OrgAccessRole (which is meant to be assumed from management).
    local source_profile="${PROJECT_NAME}-management-admin"
    mkdir -p "$(dirname "$config_file")"
    [ -f "$config_file" ] || touch "$config_file"
    {
        echo ""
        echo "# Added by deploy_deployer_github_action_role.sh for ${PROJECT_NAME} (deployer role in ${ENVIRONMENT})"
        echo "[profile ${deployer_profile_name}]"
        echo "region = ${AWS_REGION}"
        echo "credential_process = \"${assume_script_path}\" ${ENVIRONMENT} deployer ${source_profile}"
    } >> "$config_file"
    print_info "Appended [profile ${deployer_profile_name}] to ${config_file}. Use: aws --profile ${deployer_profile_name} <command>"
}

# Function to deploy the role stack
deploy_stack() {
    local stack_name=$1
    local template_file=$2
    local param_file=$3

    print_info "Deploying stack: $stack_name"

    # Get current stack status before attempting update
    local initial_status=""
    if aws_cmd cloudformation describe-stacks --stack-name $stack_name >/dev/null 2>&1; then
        initial_status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $stack_name \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)
        print_info "Current stack status: $initial_status"
        print_warning "Stack $stack_name already exists. Attempting update..."

        local update_output=$(aws_cmd cloudformation update-stack \
            --stack-name $stack_name \
            --template-body "file://$template_file" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1)
        local result=$?

        # Check output for "No updates are to be performed" regardless of exit code
        if echo "$update_output" | grep -qi "No updates are to be performed"; then
            print_info "No updates needed for stack $stack_name (template and parameters unchanged)"
            return 0
        fi

        if [ $result -ne 0 ]; then
            print_error "Stack update failed: $update_output"
            return 1
        fi

        # Update was triggered successfully - verify it actually started
        print_info "Update command succeeded. Verifying update was triggered..."
        sleep 3

        local verify_status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $stack_name \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)

        local update_triggered=false
        if [ "$verify_status" = "UPDATE_IN_PROGRESS" ]; then
            update_triggered=true
            print_info "✓ Update confirmed: Stack transitioned to UPDATE_IN_PROGRESS"
        fi

        local event_status=$(aws_cmd cloudformation describe-stack-events \
            --stack-name $stack_name \
            --max-items 1 \
            --query 'StackEvents[0].ResourceStatus' \
            --output text 2>/dev/null)
        local event_type=$(aws_cmd cloudformation describe-stack-events \
            --stack-name $stack_name \
            --max-items 1 \
            --query 'StackEvents[0].ResourceType' \
            --output text 2>/dev/null)

        if [ "$event_status" = "UPDATE_IN_PROGRESS" ] && [ "$event_type" = "AWS::CloudFormation::Stack" ]; then
            update_triggered=true
        fi

        if [ "$update_triggered" = false ] && [ "$verify_status" != "$initial_status" ]; then
            sleep 5
            verify_status=$(aws_cmd cloudformation describe-stacks \
                --stack-name $stack_name \
                --query 'Stacks[0].StackStatus' \
                --output text 2>/dev/null)
            [ "$verify_status" = "UPDATE_IN_PROGRESS" ] && update_triggered=true
        fi

        if [ "$update_triggered" = false ]; then
            print_info "No updates needed for stack $stack_name"
            return 0
        fi
    else
        print_info "Creating new stack: $stack_name"
        aws_cmd cloudformation create-stack \
            --stack-name $stack_name \
            --template-body "file://$template_file" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM
    fi

    print_info "Waiting for stack $stack_name to complete..."

    local max_wait_time=1800
    local elapsed_time=0
    local poll_interval=10
    local stack_status=""

    while [ $elapsed_time -lt $max_wait_time ]; do
        stack_status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $stack_name \
            --query 'Stacks[0].StackStatus' \
            --output text 2>/dev/null)

        if [ -z "$stack_status" ]; then
            print_error "Stack $stack_name not found"
            return 1
        fi

        case "$stack_status" in
            CREATE_COMPLETE|UPDATE_COMPLETE)
                print_info "Stack $stack_name completed successfully"
                return 0
                ;;
            ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED|ROLLBACK_FAILED)
                print_error "Stack $stack_name entered failed state: $stack_status"
                check_stack_status $stack_name
                exit 1
                ;;
            CREATE_IN_PROGRESS|UPDATE_IN_PROGRESS|UPDATE_ROLLBACK_IN_PROGRESS|ROLLBACK_IN_PROGRESS|DELETE_IN_PROGRESS)
                if [ $((elapsed_time % 60)) -eq 0 ]; then
                    print_info "Stack $stack_name status: $stack_status (waiting...)"
                fi
                ;;
            *)
                print_warning "Stack $stack_name in unknown state: $stack_status"
                ;;
        esac

        sleep $poll_interval
        elapsed_time=$((elapsed_time + poll_interval))
    done

    print_error "Timeout waiting for stack $stack_name to complete (waited ${max_wait_time}s)"
    print_error "Current stack status: $stack_status"
    exit 1
}

# Function to show stack status
show_status() {
    print_info "Checking stack status..."
    echo ""

    if aws_cmd cloudformation describe-stacks --stack-name $ROLE_STACK >/dev/null 2>&1; then
        local status=$(aws_cmd cloudformation describe-stacks \
            --stack-name $ROLE_STACK \
            --query 'Stacks[0].StackStatus' \
            --output text)
        print_info "$ROLE_STACK: $status"
    else
        print_warning "$ROLE_STACK: Does not exist"
    fi
}

# Function to validate template
validate_all_templates() {
    print_header "Validating CloudFormation template"
    validate_template "$ROLE_TEMPLATE" "$ROLE_STACK"
    print_info "Template validated successfully"
}

# Function to delete stack
delete_stacks() {
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete deployer role stack ($ROLE_STACK)" || return 0
    fi

    print_step "Deleting stack..."

    if aws_cmd cloudformation describe-stacks --stack-name $ROLE_STACK >/dev/null 2>&1; then
        print_info "Deleting stack: $ROLE_STACK"
        aws_cmd cloudformation delete-stack --stack-name $ROLE_STACK
        aws_cmd cloudformation wait stack-delete-complete --stack-name $ROLE_STACK
        print_info "Stack deleted"
    else
        print_warning "Stack $ROLE_STACK does not exist"
    fi
}

# Function to deploy the role stack
deploy_all_stacks() {
    print_step "Deploying GitHub Actions deployer IAM role"

    local temp_dir=$(mktemp -d)
    trap "rm -rf $temp_dir" EXIT

    print_info "Deploying deployer role..."
    local role_params_file="$temp_dir/role_params.json"
    cat > "$role_params_file" <<EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "GitHubOrg", "ParameterValue": "$GITHUB_ORG"},
  {"ParameterKey": "GitHubRepo", "ParameterValue": "$GITHUB_REPO"},
  {"ParameterKey": "OIDCProviderArn", "ParameterValue": "$OIDC_PROVIDER_ARN"}
]
EOF

    deploy_stack "$ROLE_STACK" "$ROLE_TEMPLATE" "$role_params_file"
    local ROLE_ARN=$(get_stack_output "$ROLE_STACK" "RoleArn")

    # Write role ARN: default to environment secrets file; optionally to infra.yaml
    if [ -n "$ROLE_ARN" ] && command -v yq &>/dev/null; then
        local secrets_file
        secrets_file=$(get_environment_secrets_file "$ENVIRONMENT" 2>/dev/null)
        if [ -n "$secrets_file" ] && [ -f "$secrets_file" ]; then
            yq -i ".github_environment_secrets.AWS_DEPLOYER_ROLE_ARN = \"${ROLE_ARN}\"" "$secrets_file"
            yq -i ".config_secrets.DEPLOYER_ROLE_ARN = \"${ROLE_ARN}\"" "$secrets_file"
            print_info "Updated $secrets_file with AWS_DEPLOYER_ROLE_ARN and DEPLOYER_ROLE_ARN for $ENVIRONMENT"
        elif [ "$WRITE_TO_INFRA" = false ]; then
            print_warning "Secrets file not found for $ENVIRONMENT; role ARN not persisted. Add it to your env secrets file or run with --write-to-infra."
        fi
        if [ "$WRITE_TO_INFRA" = true ] && [ -n "${INFRA_CONFIG_PATH:-}" ] && [ -f "$INFRA_CONFIG_PATH" ]; then
            yq -i ".environments.${ENVIRONMENT}.github_actions_deployer_role_arn = \"${ROLE_ARN}\"" "$INFRA_CONFIG_PATH"
            print_info "Updated infra/infra.yaml with github_actions_deployer_role_arn for $ENVIRONMENT"
        fi
    fi

    # Ensure deployer profile exists in ~/.aws/config (uses assume_role_for_cli.sh)
    ensure_deployer_profile_in_aws_config

    echo ""
    print_step "Deployment Summary"
    echo ""
    print_info "GitHub Actions Deployer Role ARN: $ROLE_ARN"
    echo ""
    print_warning "IMPORTANT: Add this role ARN to your GitHub environment secret:"
    print_warning "  Secret name: AWS_DEPLOYER_ROLE_ARN"
    print_warning "  Secret value: $ROLE_ARN"
    echo ""
    print_info "Go to: Repository Settings → Environments → $ENVIRONMENT → Environment secrets"
    print_info "Add or update AWS_DEPLOYER_ROLE_ARN so the deploy workflow can assume this role."
}

# Main execution
case $ACTION in
    deploy)
        deploy_all_stacks
        ;;
    update)
        deploy_all_stacks
        ;;
    validate)
        validate_all_templates
        ;;
    status)
        show_status
        ;;
    delete)
        delete_stacks
        ;;
    *)
        print_error "Unknown action: $ACTION"
        show_usage
        exit 1
        ;;
esac
