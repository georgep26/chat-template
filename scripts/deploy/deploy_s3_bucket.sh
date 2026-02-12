#!/bin/bash

# S3 Bucket Deployment Script for Knowledge Base Documents
# This script deploys the S3 bucket CloudFormation stack.
# Uses the CLI admin profile for local runs. In CI (GitHub Actions), env credentials
# via OIDC are detected automatically and no profile override is applied.
# All configuration is read from infra.yaml
#
# Usage Examples:
#   # Deploy to development environment
#   ./scripts/deploy/deploy_s3_bucket.sh dev deploy
#
#   # Deploy with auto-confirmation
#   ./scripts/deploy/deploy_s3_bucket.sh dev deploy -y
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_s3_bucket.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_s3_bucket.sh dev status
#
#   # Delete stack
#   ./scripts/deploy/deploy_s3_bucket.sh dev delete

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Script Configuration
# =============================================================================

RESOURCE_NAME="s3_bucket"
RESOURCE_DISPLAY_NAME="S3 Bucket"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "S3 Bucket Deployment Script for Knowledge Base Documents"
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
    echo "  -y, --yes   - Skip confirmation prompt"
    echo ""
    echo "Note: All configuration is read from infra/infra.yaml"
}

# =============================================================================
# Argument Parsing
# =============================================================================

if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

# Default values
ACTION="deploy"
AUTO_CONFIRM=false

# Parse arguments
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

# =============================================================================
# Configuration Loading
# =============================================================================

print_header "$RESOURCE_DISPLAY_NAME Deployment"

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration
print_step "Loading configuration for $ENVIRONMENT environment"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Get values from config
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
# Use CLI admin profile by default for local runs. In CI, env creds (OIDC) are used automatically.
if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
    print_info "Using CLI admin profile: $AWS_PROFILE"
fi
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME")

# Get config values (with variable substitution)
BUCKET_NAME=$(get_resource_config "$RESOURCE_NAME" "bucket_name" "$ENVIRONMENT")
ENABLE_VERSIONING=$(get_resource_config "$RESOURCE_NAME" "enable_versioning")
ENABLE_LIFECYCLE=$(get_resource_config "$RESOURCE_NAME" "enable_lifecycle")

# Convert boolean to CloudFormation format
[ "$ENABLE_VERSIONING" = "true" ] && VERSIONING_STATUS="Enabled" || VERSIONING_STATUS="Suspended"

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

# =============================================================================
# AWS CLI Helper
# =============================================================================

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# =============================================================================
# Template Validation
# =============================================================================

do_validate_template() {
    print_step "Validating CloudFormation template..."
    
    if [ ! -f "$TEMPLATE_FILE" ]; then
        print_error "Template file not found: $TEMPLATE_FILE"
        exit 1
    fi
    
    # Test AWS credentials before validation (catches credential_process errors early)
    if [ -n "$AWS_PROFILE" ]; then
        print_info "Testing AWS credentials with profile: $AWS_PROFILE"
        local caller_identity
        if caller_identity=$(aws_cmd sts get-caller-identity 2>&1); then
            local assumed_arn
            assumed_arn=$(echo "$caller_identity" | grep -o '"Arn"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
            print_info "Authenticated as: $assumed_arn"
        else
            print_error "Failed to get AWS credentials using profile '$AWS_PROFILE'"
            echo "$caller_identity" | sed 's/^/  /'
            print_info ""
            print_info "Common issues:"
            print_info "  - yq is not installed (required by credential_process): brew install yq"
            print_info "  - Source profile cannot assume OrganizationAccountAccessRole"
            print_info "  - Deployer role does not exist yet (run deploy_deployer_github_action_role.sh first)"
            print_info "  - Try --use-cli-role to use the CLI admin profile instead"
            exit 1
        fi
    fi
    
    local validation_output
    validation_output=$(aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" 2>&1)
    local validation_exit=$?
    
    if [ $validation_exit -eq 0 ]; then
        print_complete "Template validation successful"
    else
        print_error "Template validation failed"
        echo "$validation_output" | sed 's/^/  /'
        exit 1
    fi
}

# =============================================================================
# Stack Status
# =============================================================================

check_stack_status() {
    local status=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null)
    
    if [ -z "$status" ]; then
        return 1
    fi
    
    case "$status" in
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED)
            print_error "Stack is in failed state: $status"
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

# =============================================================================
# Deploy Stack
# =============================================================================

deploy_stack() {
    # Show deploy summary
    print_resource_summary "$RESOURCE_NAME" "$ENVIRONMENT" "$ACTION"
    print_info "Bucket Name: $BUCKET_NAME"
    print_info "Versioning: $VERSIONING_STATUS"
    print_info "Lifecycle Rules: $ENABLE_LIFECYCLE"
    
    # Confirm deployment
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment || exit 0
    fi
    
    print_step "Deploying CloudFormation stack: $STACK_NAME"
    
    # Create parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    cat > "$param_file" << EOF
[
  {"ParameterKey": "ProjectName", "ParameterValue": "$PROJECT_NAME"},
  {"ParameterKey": "Environment", "ParameterValue": "$ENVIRONMENT"},
  {"ParameterKey": "BucketName", "ParameterValue": "$BUCKET_NAME"},
  {"ParameterKey": "EnableVersioning", "ParameterValue": "$VERSIONING_STATUS"},
  {"ParameterKey": "EnableLifecycleRules", "ParameterValue": "$ENABLE_LIFECYCLE"}
]
EOF
    
    # Check if stack exists
    local stack_exists=false
    local no_updates=false
    
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        stack_exists=true
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
            --parameters "file://$param_file" || {
            print_error "Stack creation failed"
            exit 1
        }
    fi
    
    # Wait for stack operation
    if [ "$no_updates" = false ]; then
        print_info "Waiting for stack operation to complete..."
        
        local wait_cmd="stack-create-complete"
        [ "$stack_exists" = true ] && wait_cmd="stack-update-complete"
        
        if aws_cmd cloudformation wait "$wait_cmd" --stack-name "$STACK_NAME" 2>/dev/null; then
            print_complete "Stack operation completed successfully"
        else
            if ! check_stack_status; then
                print_error "Stack deployment failed"
                exit 1
            fi
        fi
    fi
    
    # Verify final status
    if ! check_stack_status; then
        print_error "Stack is in a failed state"
        exit 1
    fi
    
    # Get and display bucket name
    local actual_bucket=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -n "$actual_bucket" ] && [ "$actual_bucket" != "None" ]; then
        # Create kb_sources folder
        print_info "Creating kb_sources folder in bucket..."
        if aws_cmd s3api put-object \
            --bucket "$actual_bucket" \
            --key "kb_sources/" \
            --content-length 0 >/dev/null 2>&1; then
            print_complete "Created kb_sources folder"
        else
            if aws_cmd s3 ls "s3://$actual_bucket/kb_sources/" >/dev/null 2>&1; then
                print_info "kb_sources folder already exists"
            fi
        fi
        
        # Create config folder and upload app_config.yaml
        # Read from local config/<env>/app_config.yaml, upload to s3://bucket/config/app_config.yaml
        local config_path="config/${ENVIRONMENT}/app_config.yaml"
        local s3_config_key="config/app_config.yaml"
        
        if [ -f "$PROJECT_ROOT/$config_path" ]; then
            print_info "Uploading app config to s3://$actual_bucket/$s3_config_key..."
            if aws_cmd s3 cp "$PROJECT_ROOT/$config_path" "s3://$actual_bucket/$s3_config_key" >/dev/null 2>&1; then
                print_complete "Uploaded app config: s3://$actual_bucket/$s3_config_key"
            else
                print_warning "Failed to upload app config (continuing anyway)"
            fi
        else
            print_warning "App config file not found: $config_path (skipping upload)"
        fi
        
        print_complete "$RESOURCE_DISPLAY_NAME deployment finished"
        echo ""
        print_info "Bucket Name: $actual_bucket"
        print_info "Upload documents to: s3://$actual_bucket/kb_sources/"
        print_info "App config: s3://$actual_bucket/$s3_config_key"
    fi
}

# =============================================================================
# Delete Stack
# =============================================================================

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
    print_warning "This will delete:"
    print_warning "  - S3 Bucket (and all its contents)"
    print_warning "  - Bucket policies"
    echo ""
    print_warning "WARNING: This will permanently delete all objects in the bucket!"
    
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete" || exit 0
    fi
    
    # Get bucket name from stack outputs
    local bucket_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
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

# =============================================================================
# Main Execution
# =============================================================================

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
