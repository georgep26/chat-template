#!/bin/bash

# Frontend (CloudFront + S3) Deployment Script
# Deploys the frontend static site stack (S3 bucket + CloudFront) per environment.
# Uses the CLI admin profile for local runs so deployment targets the correct sub-account.
# Does NOT build or upload the frontend app; that is done from the chat-template-frontend repo.
# All configuration is read from infra/infra.yaml
#
# Usage Examples:
#   ./scripts/deploy/deploy_frontend.sh dev deploy
#   ./scripts/deploy/deploy_frontend.sh dev deploy -y
#   ./scripts/deploy/deploy_frontend.sh staging validate
#   ./scripts/deploy/deploy_frontend.sh prod status
#   ./scripts/deploy/deploy_frontend.sh dev delete -y

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

RESOURCE_NAME="frontend"
RESOURCE_DISPLAY_NAME="Frontend (CloudFront + S3)"

show_usage() {
    echo "Frontend (CloudFront + S3) Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments: dev | staging | prod"
    echo "Actions: deploy (default) | update | delete | validate | status"
    echo ""
    echo "Options:"
    echo "  -y, --yes   - Skip confirmation prompt"
    echo ""
    echo "Note: Infrastructure is deployed from this repo. App build and upload is done from chat-template-frontend repo (see docs/cloudfront_frontend_backend_handoff.md)."
}

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift
ACTION="deploy"
AUTO_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case $1 in
        deploy|update|delete|validate|status)
            ACTION="$1"
            shift
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

print_header "$RESOURCE_DISPLAY_NAME Deployment"
validate_environment "$ENVIRONMENT" || exit 1
print_step "Loading configuration for $ENVIRONMENT environment"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
    print_info "Using CLI admin profile: $AWS_PROFILE"
fi
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME")
PRICE_CLASS=$(get_resource_config "$RESOURCE_NAME" "price_class" "$ENVIRONMENT")
[ "$PRICE_CLASS" = "null" ] || [ -z "$PRICE_CLASS" ] && PRICE_CLASS="PriceClass_100"

PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

do_validate_template() {
    print_step "Validating CloudFormation template..."
    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi
    if [ -n "$AWS_PROFILE" ]; then
        print_info "Testing AWS credentials with profile: $AWS_PROFILE"
        if ! aws_cmd sts get-caller-identity >/dev/null 2>&1; then
            print_error "Failed to get AWS credentials using profile '$AWS_PROFILE'"
            exit 1
        fi
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
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
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
    print_info "Stack Name: $STACK_NAME"
    print_info "Price Class: $PRICE_CLASS"
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment || exit 0
    fi
    print_step "Deploying CloudFormation stack: $STACK_NAME"
    aws_cmd cloudformation deploy \
        --stack-name "$STACK_NAME" \
        --template-file "$TEMPLATE_FILE" \
        --parameter-overrides "ProjectName=${PROJECT_NAME}" "EnvironmentName=${ENVIRONMENT}" "PriceClass=${PRICE_CLASS}" \
        --no-fail-on-empty-changeset
    print_complete "Stack deployment completed"
    local domain_name
    domain_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomainName'].OutputValue" \
        --output text 2>/dev/null)
    if [ -n "$domain_name" ] && [ "$domain_name" != "None" ]; then
        echo ""
        print_info "CloudFront URL: https://${domain_name}"
        print_info "Backend handoff: update infra/secrets/${ENVIRONMENT}_secrets.yaml with COGNITO_CALLBACK_URLS, COGNITO_LOGOUT_URLS, FUNCTION_URL_CORS_ALLOW_ORIGINS using https://${domain_name}"
    fi
}

empty_versioned_bucket() {
    local bucket_name=$1
    if [ -z "$bucket_name" ] || [ "$bucket_name" = "None" ]; then
        return 0
    fi
    print_warning "Emptying bucket (all versions and delete markers): $bucket_name"
    BUCKET_FOR_EMPTY="$bucket_name" REGION_FOR_EMPTY="$AWS_REGION" AWS_PROFILE_FOR_EMPTY="$AWS_PROFILE" python3 << 'PYTHON_SCRIPT'
import subprocess
import json
import os
bucket = os.environ["BUCKET_FOR_EMPTY"]
region = os.environ["REGION_FOR_EMPTY"]
profile = os.environ.get("AWS_PROFILE_FOR_EMPTY", "")
next_key = None
next_version = None
total_deleted = 0
while True:
    cmd = ["aws", "s3api", "list-object-versions", "--bucket", bucket, "--region", region, "--output", "json"]
    if profile:
        cmd += ["--profile", profile]
    if next_key:
        cmd += ["--key-marker", next_key]
        if next_version:
            cmd += ["--version-id-marker", next_version]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr or "list-object-versions failed", file=__import__("sys").stderr)
        raise SystemExit(1)
    data = json.loads(out.stdout)
    objects = [{"Key": v["Key"], "VersionId": v["VersionId"]} for v in data.get("Versions", [])]
    objects += [{"Key": d["Key"], "VersionId": d["VersionId"]} for d in data.get("DeleteMarkers", [])]
    if objects:
        delete_payload = {"Objects": objects, "Quiet": True}
        del_cmd = ["aws", "s3api", "delete-objects", "--bucket", bucket, "--region", region, "--delete", json.dumps(delete_payload)]
        if profile:
            del_cmd += ["--profile", profile]
        del_out = subprocess.run(del_cmd, capture_output=True, text=True)
        if del_out.returncode != 0:
            print(del_out.stderr or "delete-objects failed", file=__import__("sys").stderr)
            raise SystemExit(1)
        total_deleted += len(objects)
    if not data.get("IsTruncated", False):
        break
    next_key = data.get("NextKeyMarker", "")
    next_version = data.get("NextVersionIdMarker") or ""
if total_deleted:
    print(f"Deleted {total_deleted} object version(s) and/or delete marker(s).")
PYTHON_SCRIPT
}

delete_stack() {
    print_warning "This will delete the frontend stack (S3 bucket + CloudFront distribution) and all bucket contents."
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete frontend stack ($STACK_NAME)" || exit 0
    fi
    local bucket_name
    bucket_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" \
        --output text 2>/dev/null)
    if [ -n "$bucket_name" ] && [ "$bucket_name" != "None" ]; then
        empty_versioned_bucket "$bucket_name" || true
        print_warning "Removing current objects: $bucket_name"
        aws_cmd s3 rm "s3://$bucket_name" --recursive 2>/dev/null || true
    fi
    print_step "Deleting CloudFormation stack: $STACK_NAME"
    if aws_cmd cloudformation delete-stack --stack-name "$STACK_NAME"; then
        print_info "Stack deletion initiated"
        print_info "Waiting for deletion to complete..."
        if aws_cmd cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" 2>/dev/null; then
            print_complete "Stack deleted successfully"
        else
            print_warning "Stack deletion may still be in progress"
        fi
    else
        print_error "Failed to initiate stack deletion"
        exit 1
    fi
}

case $ACTION in
    validate)
        do_validate_template
        ;;
    status)
        show_status
        ;;
    deploy|update)
        do_validate_template
        deploy_stack
        ;;
    delete)
        delete_stack
        ;;
    *)
        print_error "Invalid action: $ACTION"
        show_usage
        exit 1
        ;;
esac

print_complete "$RESOURCE_DISPLAY_NAME operation completed successfully"
