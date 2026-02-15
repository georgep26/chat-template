#!/bin/bash

# Environment Teardown Script
# This script tears down all resources, roles, and policies for an environment in reverse dependency order
# Resources and roles are deleted in reverse order of how they are defined in infra.yaml
# If a resource or role is disabled but its stack exists, it will still be deleted
# Management account roles are only deleted when running in the management account
# OrganizationAccountAccessRole and other default AWS roles are never deleted
# The CLI role stack is deleted last using OrganizationAccountAccessRole (not the CLI profile) to avoid invalid token errors
#
# Usage Examples:
#   # Teardown dev environment
#   ./scripts/deploy/teardown.sh dev
#
#   # Teardown with auto-confirmation (DANGEROUS!)
#   ./scripts/deploy/teardown.sh dev -y
#
#   # Dry run to see what would be deleted
#   ./scripts/deploy/teardown.sh dev --dry-run

set -e

# Get script directory and source utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# =============================================================================
# Usage
# =============================================================================

show_usage() {
    echo "Environment Teardown Script"
    echo ""
    echo "Usage: $0 <environment> [options]"
    echo ""
    echo "Environments:"
    echo "  dev       - Development environment"
    echo "  staging   - Staging environment"
    echo "  prod      - Production environment"
    echo ""
    echo "Options:"
    echo "  -y, --yes     - Skip all confirmation prompts (DANGEROUS!)"
    echo "  --dry-run     - Show what would be deleted without deleting"
    echo ""
    echo "Note: Resources and roles are deleted in reverse order of infra.yaml"
    echo "      If a stack exists, it will be deleted even if disabled in config"
    echo "      Management account roles are only deleted when in management account"
    echo "      OrganizationAccountAccessRole and default AWS roles are never deleted"
    echo "      The CLI role is deleted last using OrganizationAccountAccessRole (profile: <project>-management-admin)"
    echo ""
    echo "Examples:"
    echo "  $0 dev"
    echo "  $0 staging --dry-run"
    echo "  TEARDOWN_ORG_SOURCE_PROFILE=my-org-profile $0 prod   # override profile used to delete CLI role"
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
AUTO_CONFIRM=false
DRY_RUN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
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

# =============================================================================
# Configuration Loading
# =============================================================================

print_header "Environment Teardown"

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration
print_step "Loading configuration for $ENVIRONMENT environment"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Get values from config
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
AWS_PROFILE=$(get_environment_cli_profile_name "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""

ACCOUNT_ID=$(get_environment_account_id "$ENVIRONMENT")

# Change to project root
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"

# Display AWS configuration being used
print_info "AWS Configuration:"
print_info "  Account ID: $ACCOUNT_ID"
print_info "  Region: $AWS_REGION"
if [ -n "$AWS_PROFILE" ]; then
    print_info "  Profile: $AWS_PROFILE"
else
    print_info "  Profile: (default/credentials)"
fi
echo ""

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
# Helper Functions
# =============================================================================

# Get management account ID from config
get_management_account_id() {
    ensure_config_loaded || return 1
    yq '.project.management_account_id' "$INFRA_CONFIG_PATH"
}

# Check if current account is management account
is_management_account() {
    local mgmt_account_id=$(get_management_account_id)
    [ "$ACCOUNT_ID" = "$mgmt_account_id" ]
}

# Assume OrganizationAccountAccessRole in the environment's account and export credentials.
# Use this before deleting the CLI role stack so we don't use the CLI role we're about to delete.
# Requires a source profile that can assume the org role (default: <project>-management-admin).
# Override source profile with TEARDOWN_ORG_SOURCE_PROFILE if needed.
assume_org_role_for_cli_deletion() {
    local org_role_name region account_id source_profile
    org_role_name=$(yq -r ".environments.${ENVIRONMENT}.org_role_name // \"OrganizationAccountAccessRole\"" "$INFRA_CONFIG_PATH")
    account_id="$ACCOUNT_ID"
    region="$AWS_REGION"
    source_profile="${TEARDOWN_ORG_SOURCE_PROFILE:-${PROJECT_NAME}-management-admin}"

    print_info "Assuming ${org_role_name} in account ${account_id} (profile: ${source_profile}) to delete CLI role stack..."
    local creds_json
    creds_json="$(aws --profile "$source_profile" --region "$region" sts assume-role \
        --role-arn "arn:aws:iam::${account_id}:role/${org_role_name}" \
        --role-session-name "teardown-cli-delete-${ENVIRONMENT}" \
        --query 'Credentials' \
        --output json 2>/dev/null)" || {
        print_error "Failed to assume ${org_role_name} in account ${account_id}."
        print_error "Ensure profile '${source_profile}' exists and can assume OrganizationAccountAccessRole."
        print_info "Override source profile with: TEARDOWN_ORG_SOURCE_PROFILE=your-profile $0 $ENVIRONMENT"
        return 1
    }
    export AWS_ACCESS_KEY_ID
    export AWS_SECRET_ACCESS_KEY
    export AWS_SESSION_TOKEN
    AWS_ACCESS_KEY_ID="$(echo "$creds_json" | yq -r '.AccessKeyId')"
    AWS_SECRET_ACCESS_KEY="$(echo "$creds_json" | yq -r '.SecretAccessKey')"
    AWS_SESSION_TOKEN="$(echo "$creds_json" | yq -r '.SessionToken')"
    unset AWS_PROFILE
    print_complete "Using org-role credentials for CLI role stack deletion"
    return 0
}

# Get OIDC provider ARN if it exists
get_oidc_provider_arn() {
    # List all OIDC providers and filter for GitHub Actions provider
    local all_providers=$(aws_cmd iam list-open-id-connect-providers \
        --query 'OpenIDConnectProviderList[*].Arn' \
        --output text 2>/dev/null)
    
    if [ -z "$all_providers" ] || [ "$all_providers" = "None" ]; then
        return 1
    fi
    
    # Check each provider ARN for GitHub Actions pattern
    for arn in $all_providers; do
        if [[ "$arn" == *"token.actions.githubusercontent.com"* ]]; then
            echo "$arn"
            return 0
        fi
    done
    
    return 1
}

# Check if OIDC provider exists
check_oidc_provider_exists() {
    local arn=$(get_oidc_provider_arn 2>/dev/null)
    [ -n "$arn" ] && [ "$arn" != "None" ] && [ "$arn" != "" ]
}

# Determine if a role should be deleted
should_delete_role() {
    local role=$1
    
    # Skip oidc_provider (handled separately, no stack)
    if [ "$role" = "oidc_provider" ]; then
        return 1
    fi
    
    # Management account roles should only be deleted in management account
    if [ "$role" = "assume_org_access_policy" ] || [ "$role" = "management_admin_user" ]; then
        if ! is_management_account; then
            return 1
        fi
    fi
    
    return 0
}

# =============================================================================
# Stack Functions
# =============================================================================

check_stack_exists() {
    local stack_name=$1
    aws_cmd cloudformation describe-stacks --stack-name "$stack_name" >/dev/null 2>&1
}

# Delete all images from an ECR repository
delete_ecr_images() {
    local repo_name=$1
    
    if [ -z "$repo_name" ] || [ "$repo_name" = "null" ]; then
        return 0
    fi
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would delete all images from ECR repository: $repo_name"
        return 0
    fi
    
    print_info "Deleting all images from ECR repository: $repo_name"
    
    # Check if repository exists
    if ! aws_cmd ecr describe-repositories --repository-names "$repo_name" >/dev/null 2>&1; then
        print_info "Repository $repo_name does not exist, skipping image deletion"
        return 0
    fi
    
    # Get all image IDs
    local image_ids=$(aws_cmd ecr list-images \
        --repository-name "$repo_name" \
        --query 'imageIds[*]' \
        --output json 2>/dev/null)
    
    if [ -n "$image_ids" ] && [ "$image_ids" != "[]" ] && [ "$image_ids" != "null" ]; then
        print_info "Found images to delete, removing them..."
        if aws_cmd ecr batch-delete-image \
            --repository-name "$repo_name" \
            --image-ids "$image_ids" >/dev/null 2>&1; then
            print_complete "All images deleted from repository: $repo_name"
        else
            print_error "Failed to delete images from repository: $repo_name"
            return 1
        fi
    else
        print_info "No images found in repository: $repo_name"
    fi
    
    return 0
}

# Disable deletion protection for an RDS cluster
disable_db_deletion_protection() {
    local stack_name=$1
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would disable deletion protection for database cluster in stack: $stack_name"
        return 0
    fi
    
    # Get cluster identifier from stack output
    local cluster_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterIdentifier`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -z "$cluster_id" ] || [ "$cluster_id" = "None" ]; then
        print_info "Could not find cluster identifier in stack outputs, skipping deletion protection disable"
        return 0
    fi
    
    print_info "Disabling deletion protection for database cluster: $cluster_id"
    
    # Check current deletion protection status
    local deletion_protection=$(aws_cmd rds describe-db-clusters \
        --db-cluster-identifier "$cluster_id" \
        --query 'DBClusters[0].DeletionProtection' \
        --output text 2>/dev/null)
    
    if [ "$deletion_protection" = "false" ]; then
        print_info "Deletion protection is already disabled for cluster: $cluster_id"
        return 0
    fi
    
    # Disable deletion protection
    if aws_cmd rds modify-db-cluster \
        --db-cluster-identifier "$cluster_id" \
        --no-deletion-protection \
        --apply-immediately >/dev/null 2>&1; then
        print_complete "Deletion protection disabled for cluster: $cluster_id"
        # Wait a moment for the change to propagate
        sleep 2
        return 0
    else
        print_error "Failed to disable deletion protection for cluster: $cluster_id"
        return 1
    fi
}

# Empty an S3 bucket (all versions and delete markers)
empty_s3_bucket() {
    local bucket_name=$1
    
    if [ -z "$bucket_name" ] || [ "$bucket_name" = "null" ]; then
        return 0
    fi
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would empty S3 bucket: $bucket_name"
        return 0
    fi
    
    print_info "Emptying S3 bucket (all versions and delete markers): $bucket_name"
    
    # Check if bucket exists
    if ! aws_cmd s3api head-bucket --bucket "$bucket_name" >/dev/null 2>&1; then
        print_info "Bucket $bucket_name does not exist, skipping"
        return 0
    fi
    
    # Use Python to handle versioned buckets properly (same approach as deploy_s3_bucket.sh)
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
        # If bucket doesn't exist or is already empty, that's okay
        if "NoSuchBucket" in out.stderr or "does not exist" in out.stderr:
            break
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
    
    local python_exit=$?
    if [ $python_exit -eq 0 ]; then
        # Also try recursive delete as a fallback for any remaining objects
        print_info "Removing any remaining objects..."
        aws_cmd s3 rm "s3://$bucket_name" --recursive >/dev/null 2>&1 || true
        print_complete "S3 bucket emptied: $bucket_name"
        return 0
    else
        print_error "Failed to empty S3 bucket: $bucket_name"
        return 1
    fi
}

delete_stack() {
    local stack_name=$1
    local resource_name=$2
    
    if ! check_stack_exists "$stack_name"; then
        print_info "Stack $stack_name does not exist, skipping"
        return 0
    fi
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would delete stack: $stack_name"
        return 0
    fi
    
    print_step "Deleting stack: $stack_name"
    
    if aws_cmd cloudformation delete-stack --stack-name "$stack_name"; then
        print_info "Deletion initiated for $stack_name"
        print_info "Waiting for deletion to complete..."
        
        if aws_cmd cloudformation wait stack-delete-complete --stack-name "$stack_name" 2>/dev/null; then
            print_complete "$resource_name deleted successfully"
        else
            # Check if it's actually deleted or failed
            if check_stack_exists "$stack_name"; then
                local status=$(aws_cmd cloudformation describe-stacks \
                    --stack-name "$stack_name" \
                    --query 'Stacks[0].StackStatus' \
                    --output text 2>/dev/null)
                print_error "$resource_name deletion failed with status: $status"
                return 1
            else
                print_complete "$resource_name deleted successfully"
            fi
        fi
    else
        print_error "Failed to initiate deletion for $stack_name"
        return 1
    fi
}

# Delete OIDC provider
delete_oidc_provider() {
    local arn=$(get_oidc_provider_arn)
    
    if [ -z "$arn" ] || [ "$arn" = "None" ] || [ "$arn" = "" ]; then
        print_info "OIDC provider does not exist, skipping"
        return 0
    fi
    
    if [ "$DRY_RUN" = true ]; then
        print_info "[DRY RUN] Would delete OIDC provider: $arn"
        return 0
    fi
    
    print_step "Deleting OIDC provider: $arn"
    
    if aws_cmd iam delete-open-id-connect-provider --open-id-connect-provider-arn "$arn" 2>/dev/null; then
        print_complete "OIDC provider deleted successfully"
        return 0
    else
        # Check if it was already deleted
        if ! check_oidc_provider_exists; then
            print_complete "OIDC provider deleted successfully"
            return 0
        else
            print_error "Failed to delete OIDC provider"
            return 1
        fi
    fi
}

# =============================================================================
# Scan for Existing Stacks
# =============================================================================

RESOURCE_STACKS_TO_DELETE=()
ROLE_STACKS_TO_DELETE=()
CLI_ROLE_STACK_TO_DELETE=""
POLICY_STACKS_TO_DELETE=()
OIDC_PROVIDER_ARN=""

print_step "Scanning for existing stacks..."

# Get all resources (not just enabled ones) and check if their stacks exist
# Collect into array first, then reverse
RESOURCE_ARRAY=()
while IFS= read -r resource; do
    [ -z "$resource" ] && continue
    RESOURCE_ARRAY+=("$resource")
done < <(get_all_resources)

print_info "Checking ${#RESOURCE_ARRAY[@]} resource(s) for existing stacks..."

# Reverse the array for teardown order
for ((i=${#RESOURCE_ARRAY[@]}-1; i>=0; i--)); do
    resource="${RESOURCE_ARRAY[$i]}"
    
    stack_name=$(get_resource_stack_name "$resource" "$ENVIRONMENT" 2>/dev/null) || true
    if [ -n "$stack_name" ] && [ "$stack_name" != "null" ]; then
        print_info "  Checking resource '$resource' -> stack: $stack_name"
        if check_stack_exists "$stack_name"; then
            print_info "    ✓ Stack exists: $stack_name"
            RESOURCE_STACKS_TO_DELETE+=("$resource:$stack_name")
        else
            print_info "    ✗ Stack does not exist: $stack_name"
        fi
    else
        print_info "  Resource '$resource' has no stack_name defined, skipping"
    fi
    
    # Check for secondary stacks (like secret_stack_name)
    secret_stack=$(get_resource_stack_name "$resource" "$ENVIRONMENT" "secret_stack_name" 2>/dev/null) || true
    if [ -n "$secret_stack" ] && [ "$secret_stack" != "null" ]; then
        print_info "  Checking secret stack for '$resource' -> stack: $secret_stack"
        if check_stack_exists "$secret_stack"; then
            print_info "    ✓ Secret stack exists: $secret_stack"
            RESOURCE_STACKS_TO_DELETE+=("${resource}_secret:$secret_stack")
        else
            print_info "    ✗ Secret stack does not exist: $secret_stack"
        fi
    fi
done

# Scan for role stacks
print_step "Scanning for role stacks..."

# Get all roles (in reverse order for teardown)
# Collect into array first, then reverse
ROLE_ARRAY=()
while IFS= read -r role; do
    [ -z "$role" ] && continue
    ROLE_ARRAY+=("$role")
done < <(get_role_list)

print_info "Checking ${#ROLE_ARRAY[@]} role(s) for existing stacks..."

# Reverse the array for teardown order
for ((i=${#ROLE_ARRAY[@]}-1; i>=0; i--)); do
    role="${ROLE_ARRAY[$i]}"
    
    # Check if this role should be deleted
    if ! should_delete_role "$role"; then
        print_info "  Role '$role' excluded from deletion (skipping)"
        continue
    fi
    
    # Get stack name for this role
    stack_name=$(get_role_stack_name "$role" "$ENVIRONMENT" 2>/dev/null) || true
    if [ -n "$stack_name" ] && [ "$stack_name" != "null" ]; then
        print_info "  Checking role '$role' -> stack: $stack_name"
        if check_stack_exists "$stack_name"; then
            print_info "    ✓ Stack exists: $stack_name"
            # CLI role should be deleted last (other roles depend on it for authentication)
            if [ "$role" = "cli" ]; then
                CLI_ROLE_STACK_TO_DELETE="$role:$stack_name"
            else
                ROLE_STACKS_TO_DELETE+=("$role:$stack_name")
            fi
        else
            print_info "    ✗ Stack does not exist: $stack_name"
        fi
    else
        print_info "  Role '$role' has no stack_name defined, skipping"
    fi
done

# Scan for evals policy stacks (these are separate stacks not in infra.yaml)
print_step "Scanning for evals policy stacks..."
EVALS_POLICY_STACKS=(
    "${PROJECT_NAME}-${ENVIRONMENT}-evals-secrets-manager-policy"
    "${PROJECT_NAME}-${ENVIRONMENT}-evals-s3-evaluation-policy"
    "${PROJECT_NAME}-${ENVIRONMENT}-evals-bedrock-evaluation-policy"
    "${PROJECT_NAME}-${ENVIRONMENT}-evals-lambda-invoke-policy"
)

for policy_stack in "${EVALS_POLICY_STACKS[@]}"; do
    print_info "  Checking policy stack: $policy_stack"
    if check_stack_exists "$policy_stack"; then
        print_info "    ✓ Stack exists: $policy_stack"
        POLICY_STACKS_TO_DELETE+=("$policy_stack")
    else
        print_info "    ✗ Stack does not exist: $policy_stack"
    fi
done

# Check for OIDC provider
print_step "Scanning for OIDC provider..."
print_info "Searching for GitHub Actions OIDC provider..."
if check_oidc_provider_exists; then
    OIDC_PROVIDER_ARN=$(get_oidc_provider_arn 2>/dev/null)
    print_info "  ✓ Found OIDC provider: $OIDC_PROVIDER_ARN"
else
    print_info "  ✗ No OIDC provider found"
    print_info "    (Searched for providers containing 'token.actions.githubusercontent.com')"
    OIDC_PROVIDER_ARN=""
fi

# Print scanning summary
echo ""
print_info "Scanning Summary:"
print_info "  Resource stacks found: ${#RESOURCE_STACKS_TO_DELETE[@]}"
print_info "  Role stacks found: ${#ROLE_STACKS_TO_DELETE[@]}"
print_info "  Policy stacks found: ${#POLICY_STACKS_TO_DELETE[@]}"
if [ -n "$CLI_ROLE_STACK_TO_DELETE" ]; then
    print_info "  CLI role stack found: Yes (will be deleted last)"
else
    print_info "  CLI role stack found: No"
fi
if [ -n "$OIDC_PROVIDER_ARN" ] && [ "$OIDC_PROVIDER_ARN" != "None" ] && [ "$OIDC_PROVIDER_ARN" != "" ]; then
    print_info "  OIDC provider found: Yes"
else
    print_info "  OIDC provider found: No"
fi
echo ""

# =============================================================================
# Teardown Summary
# =============================================================================

TOTAL_TO_DELETE=$((${#RESOURCE_STACKS_TO_DELETE[@]} + ${#ROLE_STACKS_TO_DELETE[@]} + ${#POLICY_STACKS_TO_DELETE[@]}))
if [ -n "$CLI_ROLE_STACK_TO_DELETE" ]; then
    TOTAL_TO_DELETE=$((TOTAL_TO_DELETE + 1))
fi
if [ -n "$OIDC_PROVIDER_ARN" ] && [ "$OIDC_PROVIDER_ARN" != "None" ] && [ "$OIDC_PROVIDER_ARN" != "" ]; then
    TOTAL_TO_DELETE=$((TOTAL_TO_DELETE + 1))
fi

if [ $TOTAL_TO_DELETE -eq 0 ]; then
    print_info "No resources found to delete for environment: $ENVIRONMENT"
    exit 0
fi

echo ""
print_warning "The following resources will be PERMANENTLY DELETED:"
echo ""

# Show resources
if [ ${#RESOURCE_STACKS_TO_DELETE[@]} -gt 0 ]; then
    print_warning "Resources (CloudFormation stacks):"
    for item in "${RESOURCE_STACKS_TO_DELETE[@]}"; do
        resource="${item%%:*}"
        stack="${item##*:}"
        print_warning "  - $resource ($stack)"
    done
    echo ""
fi

# Show roles
if [ ${#ROLE_STACKS_TO_DELETE[@]} -gt 0 ] || [ -n "$CLI_ROLE_STACK_TO_DELETE" ]; then
    print_warning "Roles (CloudFormation stacks):"
    for item in "${ROLE_STACKS_TO_DELETE[@]}"; do
        role="${item%%:*}"
        stack="${item##*:}"
        print_warning "  - $role ($stack)"
    done
    if [ -n "$CLI_ROLE_STACK_TO_DELETE" ]; then
        role="${CLI_ROLE_STACK_TO_DELETE%%:*}"
        stack="${CLI_ROLE_STACK_TO_DELETE##*:}"
        print_warning "  - $role ($stack) [deleted last]"
    fi
    echo ""
fi

# Show policy stacks
if [ ${#POLICY_STACKS_TO_DELETE[@]} -gt 0 ]; then
    print_warning "Policy stacks (CloudFormation stacks):"
    for stack in "${POLICY_STACKS_TO_DELETE[@]}"; do
        print_warning "  - $stack"
    done
    echo ""
fi

# Show OIDC provider
if [ -n "$OIDC_PROVIDER_ARN" ] && [ "$OIDC_PROVIDER_ARN" != "None" ] && [ "$OIDC_PROVIDER_ARN" != "" ]; then
    print_warning "OIDC Provider:"
    print_warning "  - GitHub OIDC Provider ($OIDC_PROVIDER_ARN)"
    echo ""
fi

# Show exclusions
EXCLUSIONS_SHOWN=false
if ! is_management_account; then
    print_info "Note: Management account roles are excluded (not in management account)"
    EXCLUSIONS_SHOWN=true
fi
if [ "$EXCLUSIONS_SHOWN" = true ]; then
    echo ""
fi

# =============================================================================
# Confirmation
# =============================================================================

if [ "$DRY_RUN" = true ]; then
    print_info "DRY RUN MODE - No resources will be deleted"
    echo ""
else
    if [ "$AUTO_CONFIRM" = false ]; then
        echo ""
        print_warning "This action is IRREVERSIBLE!"
        print_warning "All resources, roles, and policies will be PERMANENTLY DELETED!"
        print_warning "All data in these resources will be PERMANENTLY LOST!"
        echo ""
        confirm_destructive_action "$ENVIRONMENT" "teardown" || exit 0
    else
        print_warning "Auto-confirm enabled - proceeding with teardown"
    fi
fi

# =============================================================================
# Teardown Execution
# =============================================================================

TEARDOWN_STEPS=0

# Delete resources first (in reverse order)
if [ ${#RESOURCE_STACKS_TO_DELETE[@]} -gt 0 ]; then
    echo ""
    print_step "Deleting Resources..."
    
    for item in "${RESOURCE_STACKS_TO_DELETE[@]}"; do
        resource="${item%%:*}"
        stack="${item##*:}"
        
        TEARDOWN_STEPS=$((TEARDOWN_STEPS + 1))
        
        echo ""
        print_step "Step $TEARDOWN_STEPS: Deleting resource $resource"
        
        # Delete ECR images before deleting ECR repository stack
        if [ "$resource" = "rag_lambda_ecr" ]; then
            print_info "ECR repository detected, deleting images first..."
            repo_name=$(get_resource_config "rag_lambda_ecr" "repository_name" "$ENVIRONMENT" 2>/dev/null) || true
            if [ -n "$repo_name" ] && [ "$repo_name" != "null" ]; then
                if ! delete_ecr_images "$repo_name"; then
                    print_error "Failed to delete ECR images. Aborting teardown."
                    exit 1
                fi
            fi
        fi
        
        # Disable deletion protection before deleting database stack
        if [ "$resource" = "chat_db" ]; then
            print_info "Database detected, disabling deletion protection first..."
            if ! disable_db_deletion_protection "$stack"; then
                print_error "Failed to disable database deletion protection. Aborting teardown."
                exit 1
            fi
        fi
        
        # Empty S3 bucket before deleting S3 bucket stack
        if [ "$resource" = "s3_bucket" ]; then
            print_info "S3 bucket detected, emptying bucket first..."
            # Try to get bucket name from config first
            bucket_name=$(get_resource_config "s3_bucket" "bucket_name" "$ENVIRONMENT" 2>/dev/null) || true
            # If not found in config, try to get from stack outputs
            if [ -z "$bucket_name" ] || [ "$bucket_name" = "null" ]; then
                bucket_name=$(aws_cmd cloudformation describe-stacks \
                    --stack-name "$stack" \
                    --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
                    --output text 2>/dev/null) || true
            fi
            if [ -n "$bucket_name" ] && [ "$bucket_name" != "null" ]; then
                if ! empty_s3_bucket "$bucket_name"; then
                    print_error "Failed to empty S3 bucket. Aborting teardown."
                    exit 1
                fi
            fi
        fi
        
        if ! delete_stack "$stack" "$resource"; then
            print_error "Failed to delete stack: $stack. Aborting teardown."
            exit 1
        fi
    done
fi

# Delete roles after resources (in reverse order, but CLI role last)
if [ ${#ROLE_STACKS_TO_DELETE[@]} -gt 0 ] || [ -n "$CLI_ROLE_STACK_TO_DELETE" ]; then
    echo ""
    print_step "Deleting Roles..."
    
    # Delete all roles except CLI first
    if [ ${#ROLE_STACKS_TO_DELETE[@]} -gt 0 ]; then
        for item in "${ROLE_STACKS_TO_DELETE[@]}"; do
            role="${item%%:*}"
            stack="${item##*:}"
            
            TEARDOWN_STEPS=$((TEARDOWN_STEPS + 1))
            
            echo ""
            print_step "Step $TEARDOWN_STEPS: Deleting role $role"
            
            if ! delete_stack "$stack" "$role"; then
                print_error "Failed to delete role stack: $stack. Aborting teardown."
                exit 1
            fi
        done
    fi
fi

# Delete policy stacks after roles (following deploy script pattern: role first, then policies)
if [ ${#POLICY_STACKS_TO_DELETE[@]} -gt 0 ]; then
    echo ""
    print_step "Deleting Policy Stacks..."
    
    # Delete policy stacks in reverse order
    for ((i=${#POLICY_STACKS_TO_DELETE[@]}-1; i>=0; i--)); do
        stack="${POLICY_STACKS_TO_DELETE[$i]}"
        
        TEARDOWN_STEPS=$((TEARDOWN_STEPS + 1))
        
        echo ""
        print_step "Step $TEARDOWN_STEPS: Deleting policy stack $stack"
        
        if ! delete_stack "$stack" "policy"; then
            print_error "Failed to delete policy stack: $stack. Aborting teardown."
            exit 1
        fi
    done
fi

# Delete OIDC provider (before CLI role)
if [ -n "$OIDC_PROVIDER_ARN" ] && [ "$OIDC_PROVIDER_ARN" != "None" ] && [ "$OIDC_PROVIDER_ARN" != "" ]; then
    echo ""
    print_step "Deleting OIDC Provider..."
    
    TEARDOWN_STEPS=$((TEARDOWN_STEPS + 1))
    
    echo ""
    print_step "Step $TEARDOWN_STEPS: Deleting OIDC provider"
    
    if ! delete_oidc_provider; then
        print_error "Failed to delete OIDC provider. Aborting teardown."
        exit 1
    fi
fi

# Delete CLI role last (very last thing - use org-role credentials so we don't delete the role we're using)
if [ -n "$CLI_ROLE_STACK_TO_DELETE" ]; then
    echo ""
    print_step "Deleting CLI Role (last step)..."
    
    role="${CLI_ROLE_STACK_TO_DELETE%%:*}"
    stack="${CLI_ROLE_STACK_TO_DELETE##*:}"
    
    TEARDOWN_STEPS=$((TEARDOWN_STEPS + 1))
    
    echo ""
    print_step "Step $TEARDOWN_STEPS: Deleting CLI role $role (using OrganizationAccountAccessRole)"
    
    # Switch to org-role credentials; otherwise we'd be using the CLI role to delete itself (invalid token)
    if ! assume_org_role_for_cli_deletion; then
        print_error "Cannot delete CLI role stack without org-role credentials. Aborting teardown."
        exit 1
    fi
    
    if ! delete_stack "$stack" "$role"; then
        print_error "Failed to delete CLI role stack: $stack. Aborting teardown."
        exit 1
    fi
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
echo ""
draw_box_top 64
if [ "$DRY_RUN" = true ]; then
    draw_box_row_centered "DRY RUN COMPLETE" 64
else
    draw_box_row_centered "TEARDOWN COMPLETE" 64
fi
draw_box_separator 64
draw_box_row "Environment: $ENVIRONMENT" 64
draw_box_row "Steps executed: $TEARDOWN_STEPS" 64
draw_box_bottom 64
echo ""

if [ "$DRY_RUN" = true ]; then
    print_complete "Dry run completed - no resources were deleted"
else
    print_complete "All teardown steps completed successfully!"
fi

print_complete "Environment teardown completed"
