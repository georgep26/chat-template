#!/bin/bash

# AWS Bedrock Knowledge Base Deployment Script
# This script deploys the knowledge base CloudFormation stack for different environments
# The knowledge base connects to the PostgreSQL database deployed via deploy_chat_template_db.sh
#
# Reads configuration from infra/infra.yaml and uses the deployer role profile per environment
# to assume into the target account. All command line flags are optional and override infra.yaml values.
#
# Usage Examples:
#   # Deploy to development environment (uses infra.yaml + deployer profile for dev)
#   ./scripts/deploy/deploy_knowledge_base.sh dev deploy
#   ./scripts/deploy/deploy_knowledge_base.sh dev deploy -y
#
#   # Override S3 bucket or other parameters
#   ./scripts/deploy/deploy_knowledge_base.sh staging deploy --s3-bucket my-kb-documents-bucket --s3-prefix staging-docs/
#   ./scripts/deploy/deploy_knowledge_base.sh prod deploy --region us-west-2
#
#   # Promote KB content from staging to prod (bucket-to-bucket transfer, no local download)
#   ./scripts/deploy/deploy_knowledge_base.sh prod deploy --promote-kb-from-env staging
#   # Or with explicit role ARN for cross-account:
#   ./scripts/deploy/deploy_knowledge_base.sh prod deploy --promote-kb-from-env staging --promotion-source-role-arn <arn>
#   # Use local temp folder for download then upload (optional, if streaming has issues):
#   ./scripts/deploy/deploy_knowledge_base.sh prod deploy --promote-kb-from-env staging --local-download
#   # Or specify a path for the temporary files:
#   ./scripts/deploy/deploy_knowledge_base.sh prod deploy --promote-kb-from-env staging --local-download /tmp/kb-promote
#
#   # Validate template before deployment
#   ./scripts/deploy/deploy_knowledge_base.sh dev validate
#
#   # Check stack status
#   ./scripts/deploy/deploy_knowledge_base.sh dev status
#
#   # Update existing stack
#   ./scripts/deploy/deploy_knowledge_base.sh dev update
#
#   # Delete stack (with confirmation prompt)
#   ./scripts/deploy/deploy_knowledge_base.sh dev delete
#
# Note: This script requires:
#       1. The database stack to be deployed first (via deploy_chat_template_db.sh)
#       2. An S3 bucket with documents for the knowledge base (deploy via deploy_s3_bucket.sh)
#       It runs sql/embeddings_table_setup.sql via RDS Data API before deploying the KB stack.
#       It will automatically retrieve DB stack outputs and S3 bucket name (if S3 stack exists).
#       After deployment, it syncs the data source to start ingestion.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

# Function to show usage
show_usage() {
    echo "AWS Bedrock Knowledge Base Deployment Script"
    echo ""
    echo "Reads infra/infra.yaml and uses deployer role profile per environment."
    echo "All options override infra.yaml values when provided."
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Environments: dev, staging, prod"
    echo "Actions: deploy (default), update, delete, validate, status"
    echo ""
    echo "Options (override infra.yaml when provided):"
    echo "  --db-stack-name <name>          - Database stack name"
    echo "  --embedding-model <model-id>    - Embedding model ID"
    echo "  --table-name <name>             - PostgreSQL table name for embeddings"
    echo "  --s3-bucket <bucket-name>       - S3 bucket name for knowledge base documents"
    echo "  --s3-prefix <prefix>            - S3 key prefix for documents"
    echo "  --region <region>               - AWS region"
    echo "  --promote-kb-from-env <env>     - Promote KB content from source env (e.g. staging)"
    echo "  --promotion-source-role-arn <arn> - Role ARN for reading source env (optional, only needed for cross-account)"
    echo "  --promotion-source-bucket <bucket> - Explicit source bucket (auto-detected if not provided)"
    echo "  --promotion-source-prefix <prefix> - Source prefix override (default: kb_sources/)"
    echo "  --local-download [path]            - For promotion: download to local path then upload to prod (default: temp dir)"
    echo "  -y, --yes                        - Skip confirmation prompt"
    echo ""
    echo "Example: $0 dev deploy -y"
    echo "Example: $0 prod deploy --promote-kb-from-env staging -y"
    echo "Example: $0 prod deploy --promote-kb-from-env staging --local-download -y  # Use temp dir for download then upload"
}

# Check if environment is provided
if [ $# -lt 1 ]; then
    print_error "Environment is required"
    show_usage
    exit 1
fi

ENVIRONMENT=$1
shift

# Parse action (optional second arg)
ACTION="deploy"
if [[ $# -gt 0 && "$1" =~ ^(deploy|update|delete|validate|status)$ ]]; then
    ACTION=$1
    shift
fi

# Defaults (overridden by infra or flags)
DB_STACK_NAME=""
EMBEDDING_MODEL=""
TABLE_NAME=""
S3_BUCKET_NAME=""
S3_INCLUSION_PREFIX=""
AWS_REGION_OVERRIDE=""
AUTO_CONFIRM=false
PROMOTE_KB_FROM_ENV=""
PROMOTION_SOURCE_ROLE_ARN=""
PROMOTION_SOURCE_BUCKET=""
PROMOTION_SOURCE_PREFIX="kb_sources/"
PROMOTION_LOCAL_DOWNLOAD_PATH=""  # If set, download to this path (or temp dir) then sync to prod

# Parse options
while [[ $# -gt 0 ]]; do
    case $1 in
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        --db-stack-name)
            DB_STACK_NAME="$2"
            shift 2
            ;;
        --embedding-model)
            EMBEDDING_MODEL="$2"
            shift 2
            ;;
        --table-name)
            TABLE_NAME="$2"
            shift 2
            ;;
        --s3-bucket)
            S3_BUCKET_NAME="$2"
            shift 2
            ;;
        --s3-prefix)
            S3_INCLUSION_PREFIX="$2"
            shift 2
            ;;
        --region)
            AWS_REGION_OVERRIDE="$2"
            shift 2
            ;;
        --promote-kb-from-env)
            PROMOTE_KB_FROM_ENV="$2"
            shift 2
            ;;
        --promotion-source-role-arn)
            PROMOTION_SOURCE_ROLE_ARN="$2"
            shift 2
            ;;
        --promotion-source-bucket)
            PROMOTION_SOURCE_BUCKET="$2"
            shift 2
            ;;
        --promotion-source-prefix)
            PROMOTION_SOURCE_PREFIX="$2"
            shift 2
            ;;
        --local-download)
            # Optional path: if next arg is not another flag, use it as path; else use temp dir later
            if [[ $# -ge 2 && "$2" != -* ]]; then
                PROMOTION_LOCAL_DOWNLOAD_PATH="$2"
                shift 2
            else
                PROMOTION_LOCAL_DOWNLOAD_PATH="<temp>"  # Signal to create mktemp -d at run time
                shift
            fi
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

# Validate environment
validate_environment "$ENVIRONMENT" || exit 1

# Load configuration from infra.yaml
PROJECT_ROOT=$(get_project_root)
cd "$PROJECT_ROOT"
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

# Load from infra (overridable by flags)
PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
[ -n "${AWS_REGION_OVERRIDE:-}" ] && AWS_REGION="$AWS_REGION_OVERRIDE"
AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
[ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
# In GitHub Actions, credentials come from OIDC (env vars); do not use profile so aws uses them
if [ -n "${GITHUB_ACTIONS:-}" ] && [ "$GITHUB_ACTIONS" = "true" ]; then
    AWS_PROFILE=""
fi

# Get stack name and template from infra
STACK_NAME=$(get_resource_stack_name "rag_knowledge_base" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "rag_knowledge_base")

# Load defaults from infra.yaml (overridable by flags)
[ -z "$DB_STACK_NAME" ] && DB_STACK_NAME=$(get_resource_config "rag_knowledge_base" "db_stack_name" "$ENVIRONMENT")
[ -z "$EMBEDDING_MODEL" ] && EMBEDDING_MODEL=$(get_resource_config "rag_knowledge_base" "embedding_model_id" "$ENVIRONMENT")
[ -z "$TABLE_NAME" ] && TABLE_NAME=$(get_resource_config "rag_knowledge_base" "table_name" "$ENVIRONMENT")
[ -z "$S3_BUCKET_NAME" ] && S3_BUCKET_NAME=$(get_resource_config "rag_knowledge_base" "s3_bucket_name" "$ENVIRONMENT")
[ -z "$S3_INCLUSION_PREFIX" ] && S3_INCLUSION_PREFIX=$(get_resource_config "rag_knowledge_base" "s3_inclusion_prefix" "$ENVIRONMENT")

# AWS CLI helper (uses deployer profile and region from config)
aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

# AWS CLI helper for source environment (used during promotion)
# Falls back to current credentials if no source credentials are configured (same-account scenario)
aws_source_cmd() {
    if [ -n "$SOURCE_AWS_ACCESS_KEY_ID" ]; then
        AWS_ACCESS_KEY_ID="$SOURCE_AWS_ACCESS_KEY_ID" \
        AWS_SECRET_ACCESS_KEY="$SOURCE_AWS_SECRET_ACCESS_KEY" \
        AWS_SESSION_TOKEN="$SOURCE_AWS_SESSION_TOKEN" \
        aws --region "$AWS_REGION" "$@"
    elif [ -n "$SOURCE_AWS_PROFILE" ]; then
        aws --profile "$SOURCE_AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        # No source credentials configured - use current credentials (same-account scenario)
        aws_cmd "$@"
    fi
}

# Setup promotion source authentication (role assumption or profile)
# If no role ARN or profile is provided, assumes same-account scenario and uses current credentials
setup_promotion_source_auth() {
    SOURCE_AWS_PROFILE=""
    SOURCE_AWS_ACCESS_KEY_ID=""
    SOURCE_AWS_SECRET_ACCESS_KEY=""
    SOURCE_AWS_SESSION_TOKEN=""

    if [ -z "$PROMOTE_KB_FROM_ENV" ]; then
        return 0
    fi

    validate_environment "$PROMOTE_KB_FROM_ENV" || return 1

    if [ -n "$PROMOTION_SOURCE_ROLE_ARN" ]; then
        print_info "Assuming promotion source role (ARN set)."
        local assumed_creds
        assumed_creds=$(aws_cmd sts assume-role \
            --role-arn "$PROMOTION_SOURCE_ROLE_ARN" \
            --role-session-name "kb-promotion-${ENVIRONMENT}" \
            --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
            --output text 2>/dev/null)
        SOURCE_AWS_ACCESS_KEY_ID=$(echo "$assumed_creds" | awk '{print $1}')
        SOURCE_AWS_SECRET_ACCESS_KEY=$(echo "$assumed_creds" | awk '{print $2}')
        SOURCE_AWS_SESSION_TOKEN=$(echo "$assumed_creds" | awk '{print $3}')
        if [ -z "$SOURCE_AWS_ACCESS_KEY_ID" ] || [ "$SOURCE_AWS_ACCESS_KEY_ID" = "None" ]; then
            print_error "Failed to assume promotion source role (check ARN)."
            return 1
        fi
        return 0
    fi

    SOURCE_AWS_PROFILE=$(get_environment_profile "$PROMOTE_KB_FROM_ENV")
    [ "$SOURCE_AWS_PROFILE" = "null" ] && SOURCE_AWS_PROFILE=""
    if [ -n "$SOURCE_AWS_PROFILE" ]; then
        print_info "Using source deployer profile for promotion: $SOURCE_AWS_PROFILE"
        return 0
    fi

    # No role ARN or profile provided - assume same-account scenario
    # Use current credentials (will be validated during bucket sync)
    print_info "No source role ARN or profile provided. Assuming same-account scenario."
    print_info "Using current credentials for both source and target access."
    return 0
}

# Promote KB content from source environment to target (bucket-to-bucket transfer)
promote_kb_from_environment() {
    local source_bucket="$PROMOTION_SOURCE_BUCKET"
    local source_prefix="$PROMOTION_SOURCE_PREFIX"
    local target_bucket=""
    local target_prefix="$S3_INCLUSION_PREFIX"
    local source_stack_name
    local target_stack_name

    print_step "Promoting KB content from $PROMOTE_KB_FROM_ENV to $ENVIRONMENT"

    # Setup source authentication
    if ! setup_promotion_source_auth; then
        print_error "Failed to setup promotion source authentication"
        return 1
    fi

    # Resolve source bucket
    if [ -z "$source_bucket" ]; then
        print_info "Resolving source bucket from $PROMOTE_KB_FROM_ENV environment..."
        source_stack_name=$(get_resource_stack_name "s3_bucket" "$PROMOTE_KB_FROM_ENV")
        if [ -z "$source_stack_name" ]; then
            print_error "Failed to resolve source S3 stack name for environment: $PROMOTE_KB_FROM_ENV"
            return 1
        fi
        
        if ! source_bucket=$(aws_source_cmd cloudformation describe-stacks \
            --stack-name "$source_stack_name" \
            --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue | [0]' \
            --output text 2>/dev/null); then
            print_error "Failed to resolve source bucket from stack: $source_stack_name"
            return 1
        fi
    fi

    if [ -z "$source_bucket" ] || [ "$source_bucket" = "None" ]; then
        print_error "Could not resolve source bucket for promotion"
        return 1
    fi
    print_info "Source bucket: $source_bucket"
    print_info "Source prefix: $source_prefix"

    # Resolve target bucket
    print_info "Resolving target bucket from $ENVIRONMENT environment..."
    target_stack_name=$(get_resource_stack_name "s3_bucket" "$ENVIRONMENT")
    if [ -z "$target_stack_name" ]; then
        print_error "Failed to resolve target S3 stack name for environment: $ENVIRONMENT"
        return 1
    fi

    if ! target_bucket=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$target_stack_name" \
        --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue | [0]' \
        --output text 2>/dev/null); then
        print_error "Failed to resolve target bucket from stack: $target_stack_name"
        return 1
    fi

    if [ -z "$target_bucket" ] || [ "$target_bucket" = "None" ]; then
        print_error "Could not resolve target bucket for promotion"
        return 1
    fi
    print_info "Target bucket: $target_bucket"
    print_info "Target prefix: $target_prefix"

    # Perform bucket-to-bucket sync
    print_step "Syncing KB content from s3://${source_bucket}/${source_prefix} to s3://${target_bucket}/${target_prefix}"
    print_info "Note: --delete flag ensures prod matches staging exactly (staging files are NOT deleted)"

    # Check if source and target are in same account
    local source_account_id=""
    local target_account_id=""
    
    if [ -n "$SOURCE_AWS_ACCESS_KEY_ID" ]; then
        source_account_id=$(AWS_ACCESS_KEY_ID="$SOURCE_AWS_ACCESS_KEY_ID" \
            AWS_SECRET_ACCESS_KEY="$SOURCE_AWS_SECRET_ACCESS_KEY" \
            AWS_SESSION_TOKEN="$SOURCE_AWS_SESSION_TOKEN" \
            aws sts get-caller-identity --query 'Account' --output text 2>/dev/null || echo "")
    elif [ -n "$SOURCE_AWS_PROFILE" ]; then
        source_account_id=$(aws --profile "$SOURCE_AWS_PROFILE" sts get-caller-identity --query 'Account' --output text 2>/dev/null || echo "")
    fi
    
    target_account_id=$(aws_cmd sts get-caller-identity --query 'Account' --output text 2>/dev/null || echo "")
    
    if [ -n "$source_account_id" ] && [ -n "$target_account_id" ] && [ "$source_account_id" != "$target_account_id" ]; then
        print_info "Cross-account transfer detected (source and target accounts differ)."
        
        # Optional: use local directory for download then upload (--local-download)
        if [ -n "$PROMOTION_LOCAL_DOWNLOAD_PATH" ]; then
            local local_dir=""
            local cleanup_temp=false
            
            if [ "$PROMOTION_LOCAL_DOWNLOAD_PATH" = "<temp>" ]; then
                local_dir=$(mktemp -d 2>/dev/null || echo "")
                if [ -z "$local_dir" ]; then
                    print_error "Failed to create temporary directory for --local-download"
                    return 1
                fi
                cleanup_temp=true
                print_info "Using temporary directory for download: $local_dir"
            else
                local_dir="$PROMOTION_LOCAL_DOWNLOAD_PATH"
                mkdir -p "$local_dir" || { print_error "Failed to create directory: $local_dir"; return 1; }
                print_info "Using local directory for download: $local_dir"
            fi
            
            print_info "Downloading from staging (source credentials)..."
            if ! aws_source_cmd s3 sync "s3://${source_bucket}/${source_prefix}" "$local_dir/" --region "$AWS_REGION" 2>&1; then
                print_error "Failed to download KB content from staging to $local_dir"
                [ "$cleanup_temp" = true ] && rm -rf "$local_dir"
                return 1
            fi
            
            print_info "Uploading to prod (target credentials) with --delete..."
            if ! aws_cmd s3 sync "$local_dir/" "s3://${target_bucket}/${target_prefix}" --delete --region "$AWS_REGION" 2>&1; then
                print_error "Failed to upload KB content from $local_dir to prod"
                [ "$cleanup_temp" = true ] && rm -rf "$local_dir"
                return 1
            fi
            
            if [ "$cleanup_temp" = true ]; then
                rm -rf "$local_dir"
                print_info "Removed temporary directory"
            fi
            
            print_complete "KB content promotion completed successfully (local-download path)"
            print_info "Prod bucket now matches staging content exactly"
            return 0
        fi
        
        print_info "Using source credentials to read from staging, target credentials to write to prod"
        
        # For cross-account sync, we need to use source credentials to read and target credentials to write
        # Similar to Lambda promotion: pull with source creds, push with target creds
        # List all objects in source prefix using source credentials
        print_info "Listing objects in source bucket..."
        local source_objects_raw
        source_objects_raw=$(aws_source_cmd s3api list-objects-v2 \
            --bucket "$source_bucket" \
            --prefix "$source_prefix" \
            --query 'Contents[].Key' \
            --output text 2>/dev/null || echo "")
        
        # Filter out directory markers (keys ending with /) and the prefix itself
        local source_objects=""
        local filtered_count=0
        if [ -n "$source_objects_raw" ]; then
            for key in $source_objects_raw; do
                # Skip directory markers (keys ending with /) and keys that exactly match the prefix
                # Also normalize the prefix to handle trailing slash variations
                local normalized_prefix="${source_prefix%/}"
                local normalized_key="${key%/}"
                
                # Skip if: ends with /, matches prefix exactly, or is empty
                if [[ "$key" == */ ]]; then
                    continue  # Skip directory markers
                fi
                if [[ "$normalized_key" == "$normalized_prefix" ]]; then
                    continue  # Skip the prefix itself
                fi
                if [[ -z "$key" ]]; then
                    continue  # Skip empty keys
                fi
                
                # This is a valid object to copy
                if [ -z "$source_objects" ]; then
                    source_objects="$key"
                else
                    source_objects="$source_objects $key"
                fi
                filtered_count=$((filtered_count + 1))
            done
        fi
        
        if [ -z "$source_objects" ] || [ $filtered_count -eq 0 ]; then
            print_warning "No objects found in source bucket prefix: ${source_prefix}"
            print_info "Raw objects listed: ${source_objects_raw:-none}"
            source_objects=""  # Ensure it's empty string, not null
        else
            print_info "Found $filtered_count objects to copy"
            # Copy each object using presigned URLs: generate URL with source creds, copy with target creds
            print_info "Copying objects from staging to prod using presigned URLs..."
            local copied_count=0
            local failed_count=0
            local total_count=0
            
            for object_key in $source_objects; do
                total_count=$((total_count + 1))
                # Remove leading prefix to get relative path
                local relative_key="${object_key#${source_prefix}}"
                # Remove leading slash if present
                relative_key="${relative_key#/}"
                local target_key="${target_prefix}${relative_key}"
                
                # Generate presigned URL using source credentials (valid for 1 hour)
                local presigned_url
                presigned_url=$(aws_source_cmd s3 presign \
                    "s3://${source_bucket}/${object_key}" \
                    --expires-in 3600 2>/dev/null || echo "")
                
                if [ -z "$presigned_url" ]; then
                    print_warning "Failed to generate presigned URL for: $object_key"
                    failed_count=$((failed_count + 1))
                    continue
                fi
                
                # AWS CLI s3 cp does not support presigned URLs as source when copying to S3.
                # Stream from presigned URL to target bucket (no local file): curl | aws s3 cp -
                if curl -sfL --max-time 300 "$presigned_url" | aws_cmd s3 cp - "s3://${target_bucket}/${target_key}" >/dev/null 2>&1; then
                    copied_count=$((copied_count + 1))
                    if [ $((copied_count % 10)) -eq 0 ]; then
                        print_info "  Copied $copied_count/$total_count objects..."
                    fi
                else
                    print_warning "Failed to copy: $object_key"
                    failed_count=$((failed_count + 1))
                fi
            done
            
            print_info "Copied $copied_count/$total_count objects"
            if [ $failed_count -gt 0 ]; then
                print_warning "Failed to copy $failed_count objects"
                if [ $failed_count -eq $total_count ]; then
                    print_error "All copy operations failed. Check bucket policies and permissions."
                    return 1
                fi
            fi
        fi
        
        # Delete objects in target that don't exist in source (--delete equivalent)
        print_info "Removing objects from prod that don't exist in staging..."
        local target_objects
        target_objects=$(aws_cmd s3api list-objects-v2 \
            --bucket "$target_bucket" \
            --prefix "$target_prefix" \
            --query 'Contents[].Key' \
            --output text 2>/dev/null || echo "")
        
        if [ -n "$target_objects" ]; then
            for target_key in $target_objects; do
                # Check if this object exists in source
                local relative_key="${target_key#${target_prefix}}"
                local source_key="${source_prefix}${relative_key}"
                local exists_in_source=false
                
                if [ -n "$source_objects" ]; then
                    for src_key in $source_objects; do
                        if [ "$src_key" = "$source_key" ]; then
                            exists_in_source=true
                            break
                        fi
                    done
                fi
                
                if [ "$exists_in_source" = false ]; then
                    print_info "Deleting: $target_key (not in staging)"
                    aws_cmd s3 rm "s3://${target_bucket}/${target_key}" >/dev/null 2>&1 || true
                fi
            done
        fi
    else
        # Same account - use simple sync with target credentials
        print_info "Same-account transfer - using target credentials for sync"
        if ! aws_cmd s3 sync \
            "s3://${source_bucket}/${source_prefix}" \
            "s3://${target_bucket}/${target_prefix}" \
            --delete \
            --region "$AWS_REGION" 2>&1; then
            print_error "Failed to sync KB content from staging to prod"
            return 1
        fi
    fi

    print_complete "KB content promotion completed successfully"
    print_info "Prod bucket now matches staging content exactly"
    return 0
}

# Write knowledge_base_id to env secrets file under config_secrets.KNOWLEDGE_BASE_ID.
# If the secrets file does not exist (e.g. in GitHub Actions where secrets are not checked out), skip the write and warn.
write_kb_outputs_to_secrets() {
    local kb_id=$1
    if [ -z "$kb_id" ] || [ "$kb_id" = "None" ]; then
        return 1
    fi
    ensure_config_loaded || return 1
    local secrets_file
    secrets_file=$(get_environment_secrets_file "$ENVIRONMENT" 2>/dev/null)
    if [ -n "$secrets_file" ] && [ -f "$secrets_file" ]; then
        yq -i ".config_secrets.KNOWLEDGE_BASE_ID = \"${kb_id}\"" "$secrets_file"
        print_complete "Wrote KNOWLEDGE_BASE_ID to $secrets_file (config_secrets)"
    else
        print_warning "Secrets file not found for $ENVIRONMENT (e.g. expected in CI when secrets are not present); knowledge_base_id not written to config_secrets"
    fi
    return 0
}

# For deploy/update: show summary and confirm
if [[ "$ACTION" == "deploy" || "$ACTION" == "update" ]]; then
    print_resource_summary "rag_knowledge_base" "$ENVIRONMENT" "$ACTION"
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_deployment "Proceed with $ACTION?" || exit 0
    fi
fi

# Check if template file exists
if [ ! -f "$TEMPLATE_FILE" ]; then
    print_error "Template file not found: $TEMPLATE_FILE"
    exit 1
fi

# Function to get DB stack outputs
get_db_stack_outputs() {
    print_info "Retrieving database stack outputs from: $DB_STACK_NAME" >&2
    
    # Check if DB stack exists
    if ! aws_cmd cloudformation describe-stacks --stack-name "$DB_STACK_NAME" >/dev/null 2>&1; then
        print_error "Database stack $DB_STACK_NAME does not exist in region $AWS_REGION" >&2
        print_error "Please deploy the database stack first using deploy_chat_template_db.sh" >&2
        return 1
    fi
    
    # Get DB cluster identifier
    local db_cluster_id=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$DB_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`DBClusterIdentifier`].OutputValue' \
        --output text 2>/dev/null)
    
    # Get database name
    local db_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$DB_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`DatabaseName`].OutputValue' \
        --output text 2>/dev/null)
    
    # Get secret ARN from secret stack
    local secret_stack_name=$(get_resource_stack_name "chat_db" "$ENVIRONMENT" "secret_stack_name")
    local secret_arn=""
    
    if aws_cmd cloudformation describe-stacks --stack-name "$secret_stack_name" >/dev/null 2>&1; then
        secret_arn=$(aws_cmd cloudformation describe-stacks \
            --stack-name "$secret_stack_name" \
            --query 'Stacks[0].Outputs[?OutputKey==`SecretArn`].OutputValue' \
            --output text 2>/dev/null)
    fi

    # If secret ARN not from stack (e.g. stack still in progress or no stack), try Secrets Manager by name
    if [ -z "$secret_arn" ] || [ "$secret_arn" == "None" ]; then
        # Names match db_secret_template: ${ProjectName}-${SecretName}-${Environment}
        local secret_name1="${PROJECT_NAME}-db-connection-${ENVIRONMENT}"
        local secret_name2="${PROJECT_NAME}-chat-template-db-connection-${ENVIRONMENT}"
        local secret_name3="python-template-db-connection-${ENVIRONMENT}"
        local secret_name4="python-template-chat-template-db-connection-${ENVIRONMENT}"

        for name in "$secret_name1" "$secret_name2" "$secret_name3" "$secret_name4"; do
            if aws_cmd secretsmanager describe-secret --secret-id "$name" >/dev/null 2>&1; then
                secret_arn=$(aws_cmd secretsmanager describe-secret --secret-id "$name" \
                    --query 'ARN' --output text 2>/dev/null)
                break
            fi
        done
    fi
    
    if [ -z "$db_cluster_id" ] || [ "$db_cluster_id" == "None" ]; then
        print_error "Could not retrieve DB cluster identifier from stack $DB_STACK_NAME" >&2
        return 1
    fi
    
    if [ -z "$db_name" ] || [ "$db_name" == "None" ]; then
        print_error "Could not retrieve database name from stack $DB_STACK_NAME" >&2
        return 1
    fi
    
    if [ -z "$secret_arn" ] || [ "$secret_arn" == "None" ]; then
        print_error "Could not retrieve secret ARN. Make sure the database secret stack is deployed." >&2
        return 1
    fi
    
    echo "$db_cluster_id|$db_name|$secret_arn"
}

# Function to get S3 bucket name from S3 bucket stack
get_s3_bucket_name() {
    local s3_stack_name=$(get_resource_stack_name "s3_bucket" "$ENVIRONMENT")
    print_info "Retrieving S3 bucket name from stack: $s3_stack_name" >&2
    
    # Check if S3 stack exists
    if ! aws_cmd cloudformation describe-stacks --stack-name "$s3_stack_name" >/dev/null 2>&1; then
        print_warning "S3 bucket stack $s3_stack_name does not exist in region $AWS_REGION" >&2
        print_warning "You can deploy it using deploy_s3_bucket.sh or provide --s3-bucket parameter" >&2
        return 1
    fi
    
    # Get bucket name from stack outputs
    local bucket_name=$(aws_cmd cloudformation describe-stacks \
        --stack-name "$s3_stack_name" \
        --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
        --output text 2>/dev/null)
    
    if [ -z "$bucket_name" ] || [ "$bucket_name" == "None" ]; then
        print_warning "Could not retrieve bucket name from stack $s3_stack_name" >&2
        return 1
    fi
    
    echo "$bucket_name"
}

# Seconds to wait for Aurora to spin up from 0 ACU before retrying SQL (3 minutes)
DB_STARTUP_WAIT_SECONDS=180

# Function to run embeddings table setup SQL via RDS Data API
# Requires: db_cluster_id, db_name, secret_arn (from get_db_stack_outputs)
# The caller (user or CI) must have rds-data:ExecuteStatement and rds:DescribeDBClusters IAM permissions.
# If the first query fails (e.g. Aurora at 0 ACU is starting), waits DB_STARTUP_WAIT_SECONDS then retries once.
run_embeddings_sql_via_data_api() {
    local db_cluster_id="$1"
    local db_name="$2"
    local secret_arn="$3"
    local sql_file="$PROJECT_ROOT/sql/embeddings_table_setup.sql"

    if [ ! -f "$sql_file" ]; then
        print_error "SQL file not found: $sql_file"
        return 1
    fi

    print_info "Resolving cluster ARN for RDS Data API..."
    local db_cluster_arn
    db_cluster_arn=$(aws_cmd rds describe-db-clusters \
        --db-cluster-identifier "$db_cluster_id" \
        --query 'DBClusters[0].DBClusterArn' \
        --output text 2>/dev/null)

    if [ -z "$db_cluster_arn" ] || [ "$db_cluster_arn" == "None" ]; then
        print_error "Could not resolve cluster ARN for $db_cluster_id. Ensure the cluster has Data API enabled (EnableHttpEndpoint)."
        return 1
    fi

    print_info "Running embeddings table setup via RDS Data API (sql/embeddings_table_setup.sql)..."
    # Strip full-line comments and empty lines, strip inline -- comments, normalize whitespace, split by ;
    local content
    content=$(grep -v '^[[:space:]]*--' "$sql_file" | grep -v '^[[:space:]]*$' | sed 's/--.*$//' | tr '\n' ' ' | sed 's/;[[:space:]]*/;/g')

    local waited_for_db=false
    local attempt=1

    while true; do
        local count=0
        local failed_statement=0

        while IFS= read -r -d ';' statement; do
            statement=$(echo "$statement" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            [ -z "$statement" ] && continue
            count=$((count + 1))
            print_info "Executing statement $count..."
            if ! aws_cmd rds-data execute-statement \
                --resource-arn "$db_cluster_arn" \
                --secret-arn "$secret_arn" \
                --database "$db_name" \
                --sql "$statement" >/dev/null 2>&1; then
                failed_statement=$count
                break
            fi
        done < <(printf '%s;' "$content")

        if [ "$failed_statement" -eq 0 ]; then
            print_info "Embeddings table setup completed ($count statements)."
            return 0
        fi

        if [ "$waited_for_db" = true ]; then
            print_error "Failed to execute statement $failed_statement after waiting for DB to start."
            return 1
        fi

        print_warning "Statement $failed_statement failed (database may be starting from 0 ACU). Waiting ${DB_STARTUP_WAIT_SECONDS}s for Aurora to become available..."
        sleep "$DB_STARTUP_WAIT_SECONDS"
        waited_for_db=true
        print_info "Retrying embeddings table setup..."
    done
}

# Function to validate template
validate_template() {
    print_info "Validating CloudFormation template..."
    if aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" >/dev/null 2>&1; then
        print_info "Template validation successful"
    else
        print_error "Template validation failed"
        exit 1
    fi
}

# Function to check stack status and detect errors
check_stack_status() {
    local stack_status=$(aws_cmd cloudformation describe-stacks \
        --stack-name $STACK_NAME \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null)
    
    if [ -z "$stack_status" ]; then
        return 1
    fi
    
    # Check for failure/rollback states
    case "$stack_status" in
        ROLLBACK_COMPLETE|CREATE_FAILED|UPDATE_ROLLBACK_COMPLETE|UPDATE_ROLLBACK_FAILED|DELETE_FAILED)
            print_error "Stack is in failed state: $stack_status"
            echo ""
            
            # Get stack status reason
            local status_reason=$(aws_cmd cloudformation describe-stacks \
                --stack-name $STACK_NAME \
                --query 'Stacks[0].StackStatusReason' \
                --output text 2>/dev/null)
            
            if [ -n "$status_reason" ] && [ "$status_reason" != "None" ]; then
                print_error "Status Reason: $status_reason"
                echo ""
            fi
            
            # Get recent stack events with errors
            print_error "Recent stack events with errors:"
            echo ""
            aws_cmd cloudformation describe-stack-events \
                --stack-name $STACK_NAME \
                --max-items 20 \
                --query 'StackEvents[?contains(ResourceStatus, `FAILED`) || contains(ResourceStatus, `ROLLBACK`)].{Time:Timestamp,Resource:LogicalResourceId,Status:ResourceStatus,Reason:ResourceStatusReason}' \
                --output table 2>/dev/null || true
            
            echo ""
            print_error "For more details, check the AWS Console or run:"
            print_error "aws cloudformation describe-stack-events --stack-name $STACK_NAME --region $AWS_REGION"
            
            return 1
            ;;
        CREATE_COMPLETE|UPDATE_COMPLETE)
            return 0
            ;;
        *)
            # Other states (IN_PROGRESS, etc.) - not an error yet
            return 0
            ;;
    esac
}

# Function to show stack status
show_status() {
    print_info "Checking stack status: $STACK_NAME"
    if aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME >/dev/null 2>&1; then
        aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,CreationTime:CreationTime,LastUpdatedTime:LastUpdatedTime}'
        echo ""
        print_info "Stack outputs:"
        aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].Outputs'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

# Function to deploy stack
deploy_stack() {
    print_info "Deploying CloudFormation stack: $STACK_NAME"
    
    # Promote KB content from source environment if requested
    if [ -n "$PROMOTE_KB_FROM_ENV" ]; then
        print_step "Promoting KB content from $PROMOTE_KB_FROM_ENV to $ENVIRONMENT"
        if ! promote_kb_from_environment; then
            print_error "KB content promotion failed. Aborting deployment."
            exit 1
        fi
        print_info "KB content promotion completed. Proceeding with stack deployment..."
    fi
    
    # Get DB stack outputs
    local db_outputs=$(get_db_stack_outputs)
    if [ $? -ne 0 ] || [ -z "$db_outputs" ]; then
        print_error "Failed to retrieve database stack outputs"
        exit 1
    fi
    
    local db_cluster_id=$(echo "$db_outputs" | cut -d'|' -f1)
    local db_name=$(echo "$db_outputs" | cut -d'|' -f2)
    local secret_arn=$(echo "$db_outputs" | cut -d'|' -f3)
    
    print_info "Using DB Cluster ID: $db_cluster_id"
    print_info "Using Database Name: $db_name"
    print_info "Using Secret ARN (set)."
    
    # Run embeddings table setup SQL via RDS Data API before deploying the KB stack
    if ! run_embeddings_sql_via_data_api "$db_cluster_id" "$db_name" "$secret_arn"; then
        print_error "Embeddings table setup failed. Fix the error above and retry."
        exit 1
    fi
    
    # Get S3 bucket name - try auto-detection first, then use default pattern
    if [ -z "$S3_BUCKET_NAME" ]; then
        # Try to auto-detect from S3 bucket stack first
        print_info "S3 bucket name not provided, attempting to retrieve from S3 bucket stack..."
        local retrieved_bucket=$(get_s3_bucket_name)
        if [ $? -eq 0 ] && [ -n "$retrieved_bucket" ]; then
            S3_BUCKET_NAME="$retrieved_bucket"
            print_info "Auto-detected S3 bucket from stack: $S3_BUCKET_NAME"
        else
            # Use default bucket name pattern from infra.yaml if available
            local default_bucket=$(get_resource_config "s3_bucket" "bucket_name" "$ENVIRONMENT")
            if [ -n "$default_bucket" ] && [ "$default_bucket" != "null" ]; then
                S3_BUCKET_NAME="$default_bucket"
                print_info "Using S3 bucket name from infra.yaml: $S3_BUCKET_NAME"
            else
                print_error "S3 bucket name is required. Provide --s3-bucket or deploy s3_bucket resource first."
                exit 1
            fi
        fi
    fi
    
    print_info "Using S3 Bucket: $S3_BUCKET_NAME"
    print_info "Using S3 Prefix: $S3_INCLUSION_PREFIX"
    
    # Create a temporary parameters file
    local param_file=$(mktemp)
    trap "rm -f $param_file" EXIT
    
    # Build parameters JSON file matching knowledge_base_template.yaml parameters
    {
        echo "["
        printf '  {\n    "ParameterKey": "ProjectName",\n    "ParameterValue": "%s"\n  }' "$PROJECT_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "Environment",\n    "ParameterValue": "%s"\n  }' "$ENVIRONMENT"
        echo ","
        printf '  {\n    "ParameterKey": "DBClusterIdentifier",\n    "ParameterValue": "%s"\n  }' "$db_cluster_id"
        echo ","
        printf '  {\n    "ParameterKey": "DatabaseName",\n    "ParameterValue": "%s"\n  }' "$db_name"
        echo ","
        printf '  {\n    "ParameterKey": "DBSecretArn",\n    "ParameterValue": "%s"\n  }' "$secret_arn"
        echo ","
        printf '  {\n    "ParameterKey": "TableName",\n    "ParameterValue": "%s"\n  }' "$TABLE_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "EmbeddingModelId",\n    "ParameterValue": "%s"\n  }' "$EMBEDDING_MODEL"
        echo ","
        printf '  {\n    "ParameterKey": "S3BucketName",\n    "ParameterValue": "%s"\n  }' "$S3_BUCKET_NAME"
        echo ","
        printf '  {\n    "ParameterKey": "S3InclusionPrefix",\n    "ParameterValue": "%s"\n  }' "$S3_INCLUSION_PREFIX"
        echo ""
        echo "]"
    } > "$param_file"
    
    # Check if stack exists
    local stack_operation_result=0
    local no_updates=false
    
    if aws_cmd cloudformation describe-stacks --stack-name $STACK_NAME >/dev/null 2>&1; then
        print_warning "Stack $STACK_NAME already exists. Updating..."
        local update_output=$(aws_cmd cloudformation update-stack \
            --stack-name $STACK_NAME \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM 2>&1)
        stack_operation_result=$?
        
        # Check if the error is "No updates are to be performed"
        if [ $stack_operation_result -ne 0 ]; then
            if echo "$update_output" | grep -q "No updates are to be performed"; then
                print_info "No updates needed for stack $STACK_NAME. Stack is already up to date."
                no_updates=true
                stack_operation_result=0  # Treat as success
            else
                print_error "Stack update failed:"
                echo "$update_output"
            fi
        fi
    else
        print_info "Creating new stack: $STACK_NAME"
        aws_cmd cloudformation create-stack \
            --stack-name $STACK_NAME \
            --template-body "file://$TEMPLATE_FILE" \
            --parameters "file://$param_file" \
            --capabilities CAPABILITY_NAMED_IAM
        stack_operation_result=$?
    fi
    
    if [ $stack_operation_result -eq 0 ]; then
        if [ "$no_updates" = false ]; then
            print_info "Stack operation initiated successfully"
            print_info "Waiting for stack to be ready..."
            
            # Wait for stack to be in a stable state
            print_info "Waiting for stack to reach CREATE_COMPLETE or UPDATE_COMPLETE state..."
            
            # Try waiting for create first, then update
            local wait_result=0
            aws_cmd cloudformation wait stack-create-complete --stack-name $STACK_NAME 2>/dev/null
            wait_result=$?
            
            if [ $wait_result -ne 0 ]; then
                # If create wait failed, try update wait
                aws_cmd cloudformation wait stack-update-complete --stack-name $STACK_NAME 2>/dev/null
                wait_result=$?
            fi
            
            # Check stack status after wait
            if ! check_stack_status; then
                print_error "Stack deployment failed. See errors above."
                exit 1
            fi
            
            if [ $wait_result -eq 0 ]; then
                print_info "Stack operation completed successfully"
            else
                # Wait command timed out or failed, but check if stack is actually in a good state
                if ! check_stack_status; then
                    print_error "Stack deployment failed. See errors above."
                    exit 1
                fi
                print_warning "Stack operation may still be in progress, but current status is valid."
            fi
        else
            # No updates needed, but verify stack is in good state
            if ! check_stack_status; then
                print_error "Stack is in a failed state. See errors above."
                exit 1
            fi
            print_info "Stack is up to date and ready."
        fi
        
        # Get Knowledge Base ID
        local kb_id=$(aws_cmd cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBaseId`].OutputValue' \
            --output text 2>/dev/null)
        
        # Get Data Source ID
        # CloudFormation !Ref on AWS::Bedrock::DataSource returns "KBId|DataSourceId",
        # so we split on '|' and take the second part.
        local raw_data_source_id=$(aws_cmd cloudformation describe-stacks \
            --stack-name $STACK_NAME \
            --query 'Stacks[0].Outputs[?OutputKey==`DataSourceId`].OutputValue' \
            --output text 2>/dev/null)
        local data_source_id="${raw_data_source_id##*|}"
        
        if [ -n "$kb_id" ] && [ "$kb_id" != "None" ]; then
            print_info "Knowledge Base ID: $kb_id"
            print_info "You can use this ID in your application configuration."
            write_kb_outputs_to_secrets "$kb_id" || true

            # Sync the data source to start ingestion
            if [ -n "$data_source_id" ] && [ "$data_source_id" != "None" ] && [ "$data_source_id" != "$kb_id" ]; then
                print_info "Data Source ID: $data_source_id"
                print_step "Starting data source sync to ingest documents..."
                
                local ingestion_output
                ingestion_output=$(aws_cmd bedrock-agent start-ingestion-job \
                    --knowledge-base-id "$kb_id" \
                    --data-source-id "$data_source_id" \
                    2>&1)
                local ingestion_result=$?
                
                if [ $ingestion_result -ne 0 ]; then
                    print_error "Failed to start ingestion job (exit code: $ingestion_result)"
                    print_error "$ingestion_output"
                    print_info "You can start it manually with:"
                    print_info "aws bedrock-agent start-ingestion-job --knowledge-base-id $kb_id --data-source-id $data_source_id --region $AWS_REGION"
                else
                    # Parse job ID from JSON response (try jq, then grep)
                    # API field is "ingestionJobId" not "jobId"
                    local ingestion_job_id=""
                    if command -v jq &> /dev/null; then
                        ingestion_job_id=$(echo "$ingestion_output" | jq -r '.ingestionJob.ingestionJobId // empty' 2>/dev/null)
                    fi
                    if [ -z "$ingestion_job_id" ]; then
                        ingestion_job_id=$(echo "$ingestion_output" | grep -o '"ingestionJobId"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
                    fi
                    
                    if [ -n "$ingestion_job_id" ]; then
                        print_info "Ingestion job started: $ingestion_job_id"
                        
                        # Poll until ingestion completes
                        local ingestion_status="STARTING"
                        local poll_interval=10
                        local elapsed=0
                        local max_wait=1800  # 30 minutes
                        
                        while [ $elapsed -lt $max_wait ]; do
                            local job_output=$(aws_cmd bedrock-agent get-ingestion-job \
                                --knowledge-base-id "$kb_id" \
                                --data-source-id "$data_source_id" \
                                --ingestion-job-id "$ingestion_job_id" \
                                2>/dev/null)
                            
                            # Parse status
                            if command -v jq &> /dev/null; then
                                ingestion_status=$(echo "$job_output" | jq -r '.ingestionJob.status // empty' 2>/dev/null)
                            else
                                ingestion_status=$(echo "$job_output" | grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
                            fi
                            
                            case "$ingestion_status" in
                                COMPLETE)
                                    print_info "Ingestion job completed successfully."
                                    # Print stats if available
                                    if command -v jq &> /dev/null; then
                                        local docs_scanned=$(echo "$job_output" | jq -r '.ingestionJob.statistics.numberOfDocumentsScanned // "N/A"' 2>/dev/null)
                                        local docs_indexed=$(echo "$job_output" | jq -r '.ingestionJob.statistics.numberOfNewDocumentsIndexed // "N/A"' 2>/dev/null)
                                        local docs_modified=$(echo "$job_output" | jq -r '.ingestionJob.statistics.numberOfModifiedDocumentsIndexed // "N/A"' 2>/dev/null)
                                        local docs_failed=$(echo "$job_output" | jq -r '.ingestionJob.statistics.numberOfDocumentsFailed // "N/A"' 2>/dev/null)
                                        print_info "  Documents scanned: $docs_scanned"
                                        print_info "  Documents indexed (new): $docs_indexed"
                                        print_info "  Documents indexed (modified): $docs_modified"
                                        print_info "  Documents failed: $docs_failed"
                                    fi
                                    break
                                    ;;
                                FAILED)
                                    print_error "Ingestion job failed."
                                    if command -v jq &> /dev/null; then
                                        local fail_reasons=$(echo "$job_output" | jq -r '.ingestionJob.failureReasons // [] | .[]' 2>/dev/null)
                                        if [ -n "$fail_reasons" ]; then
                                            echo "$fail_reasons" | while IFS= read -r reason; do
                                                print_error "  Reason: $reason"
                                            done
                                        fi
                                    fi
                                    break
                                    ;;
                                IN_PROGRESS|STARTING)
                                    if [ $((elapsed % 30)) -eq 0 ]; then
                                        print_info "Ingestion in progress... (${elapsed}s elapsed)"
                                    fi
                                    ;;
                                *)
                                    print_warning "Unexpected ingestion status: $ingestion_status"
                                    ;;
                            esac
                            
                            sleep $poll_interval
                            elapsed=$((elapsed + poll_interval))
                        done
                        
                        if [ $elapsed -ge $max_wait ]; then
                            print_warning "Timed out waiting for ingestion job after ${max_wait}s."
                            print_info "Monitor with: aws bedrock-agent get-ingestion-job --knowledge-base-id $kb_id --data-source-id $data_source_id --ingestion-job-id $ingestion_job_id --region $AWS_REGION"
                        fi
                    else
                        print_info "Ingestion job started (could not parse job ID from response)."
                    fi
                fi
            else
                print_warning "Could not determine Data Source ID from stack outputs (raw value: ${raw_data_source_id:-empty}). Skipping sync."
            fi
        fi
    else
        print_error "Stack operation failed to initiate"
        exit 1
    fi
}

# Function to delete stack
delete_stack() {
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete Knowledge Base stack ($STACK_NAME)" || exit 0
    fi
    aws_cmd cloudformation delete-stack --stack-name $STACK_NAME
    if [ $? -eq 0 ]; then
        print_info "Stack deletion initiated"
        print_info "This may take several minutes to complete."
    else
        print_error "Failed to initiate stack deletion"
        exit 1
    fi
}

# Main execution
case $ACTION in
    validate)
        validate_template
        ;;
    status)
        show_status
        ;;
    deploy|update)
        validate_template
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

print_info "Knowledge Base operation completed successfully"

