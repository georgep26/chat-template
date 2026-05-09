#!/bin/bash

# Document Ingestion Lambda deployment script.
# Builds/pushes the container image and deploys the EventBridge-triggered Lambda
# that processes objects uploaded to the S3 landing prefix.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"
source "$SCRIPT_DIR/../utils/config_parser.sh"
source "$SCRIPT_DIR/../utils/deploy_summary.sh"

RESOURCE_NAME="document_ingestion_lambda"
ECR_RESOURCE_NAME="document_ingestion_lambda_ecr"
RESOURCE_DISPLAY_NAME="Document Ingestion Lambda"

show_usage() {
    echo "Document Ingestion Lambda Deployment Script"
    echo ""
    echo "Usage: $0 <environment> [action] [options]"
    echo ""
    echo "Actions:"
    echo "  deploy    - Build/push image and deploy stack (default)"
    echo "  update    - Build/push image and update stack"
    echo "  build     - Build/push image only"
    echo "  validate  - Validate CloudFormation template"
    echo "  status    - Show stack status"
    echo "  delete    - Delete stack"
    echo ""
    echo "Options:"
    echo "  --image-tag <tag>        - Docker image tag (default: latest)"
    echo "  --lambda-image-uri <uri> - Use a prebuilt Lambda image URI"
    echo "  --skip-build             - Skip Docker build and use existing image tag"
    echo "  -y, --yes                - Skip confirmation prompt"
    echo ""
    echo "Requires the document_ingestion_lambda_ecr resource to be deployed first."
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
IMAGE_TAG="latest"
LAMBDA_IMAGE_URI=""
SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        deploy|update|build|validate|status|delete)
            ACTION="$1"
            shift
            ;;
        --image-tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --lambda-image-uri)
            LAMBDA_IMAGE_URI="$2"
            shift 2
            ;;
        --skip-build)
            SKIP_BUILD=true
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
load_infra_config || exit 1
validate_config "$ENVIRONMENT" || exit 1

PROJECT_NAME=$(get_project_name)
AWS_REGION=$(get_environment_region "$ENVIRONMENT")
PROJECT_ROOT=$(get_project_root)

if [ -z "$AWS_PROFILE" ] && [ -z "$AWS_SESSION_TOKEN" ]; then
    AWS_PROFILE=$(get_environment_profile "$ENVIRONMENT")
    [ "$AWS_PROFILE" = "null" ] && AWS_PROFILE=""
    [ -n "$AWS_PROFILE" ] && print_info "Using deployer profile: $AWS_PROFILE"
fi

STACK_NAME=$(get_resource_stack_name "$RESOURCE_NAME" "$ENVIRONMENT")
TEMPLATE_FILE=$(get_resource_template "$RESOURCE_NAME")
FUNCTION_NAME=$(get_resource_config "$RESOURCE_NAME" "function_name" "$ENVIRONMENT")
LAMBDA_MEMORY_SIZE=$(get_resource_config "$RESOURCE_NAME" "memory_size")
LAMBDA_TIMEOUT=$(get_resource_config "$RESOURCE_NAME" "timeout")
APP_CONFIG_S3_URI=$(get_resource_config "$RESOURCE_NAME" "s3_app_config_uri" "$ENVIRONMENT")
DOCUMENT_BUCKET_NAME=$(get_resource_config "$RESOURCE_NAME" "document_bucket_name" "$ENVIRONMENT")
LANDING_PREFIX=$(get_resource_config "$RESOURCE_NAME" "landing_prefix")
RAW_PREFIX=$(get_resource_config "$RESOURCE_NAME" "raw_prefix")
INDEX_PREFIX=$(get_resource_config "$RESOURCE_NAME" "index_prefix")
ARTIFACTS_PREFIX=$(get_resource_config "$RESOURCE_NAME" "artifacts_prefix")
CATALOG_PREFIX=$(get_resource_config "$RESOURCE_NAME" "catalog_prefix" "$ENVIRONMENT")
DEFAULT_TENANT_ID=$(get_resource_config "$RESOURCE_NAME" "default_tenant_id" "$ENVIRONMENT")
DEFAULT_COLLECTION_ID=$(get_resource_config "$RESOURCE_NAME" "default_collection_id")
PDF_TEXT_PROBE_PAGES=$(get_resource_config "$RESOURCE_NAME" "pdf_text_probe_pages")
ECR_REPO_NAME=$(get_resource_config "$ECR_RESOURCE_NAME" "repository_name" "$ENVIRONMENT")
ROLE_STACK_NAME=$(get_role_stack_name "rag_lambda_execution" "$ENVIRONMENT")

[ "$APP_CONFIG_S3_URI" = "null" ] && APP_CONFIG_S3_URI="config/app_config.yaml"

cd "$PROJECT_ROOT"

aws_cmd() {
    if [ -n "$AWS_PROFILE" ]; then
        aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"
    else
        aws --region "$AWS_REGION" "$@"
    fi
}

get_lambda_role_arn() {
    if ! aws_cmd cloudformation describe-stacks --stack-name "$ROLE_STACK_NAME" >/dev/null 2>&1; then
        print_error "Lambda execution role stack not found: $ROLE_STACK_NAME"
        print_error "Deploy the RAG Lambda execution role before deploying document ingestion."
        return 1
    fi

    aws_cmd cloudformation describe-stacks \
        --stack-name "$ROLE_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`RoleArn`].OutputValue' \
        --output text
}

get_lambda_role_name() {
    aws_cmd cloudformation describe-stacks \
        --stack-name "$ROLE_STACK_NAME" \
        --query 'Stacks[0].Outputs[?OutputKey==`RoleName`].OutputValue' \
        --output text
}

ensure_ecr_repo() {
    if aws_cmd ecr describe-repositories --repository-names "$ECR_REPO_NAME" >/dev/null 2>&1; then
        return 0
    fi

    print_error "ECR repository not found: $ECR_REPO_NAME"
    print_error "Deploy document_ingestion_lambda_ecr before building or deploying this Lambda."
    return 1
}

get_ecr_repo_uri() {
    aws_cmd ecr describe-repositories \
        --repository-names "$ECR_REPO_NAME" \
        --query 'repositories[0].repositoryUri' \
        --output text
}

build_and_push_image() {
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker is required for image build/push and does not appear to be running."
        return 1
    fi

    ensure_ecr_repo

    local repo_uri
    repo_uri=$(get_ecr_repo_uri)
    LAMBDA_IMAGE_URI="${repo_uri}:${IMAGE_TAG}"

    print_step "Logging into ECR"
    aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "${repo_uri%/*}" >/dev/null

    print_step "Building document ingestion image"
    docker build --platform linux/amd64 \
        -f src/document_ingestion_lambda/Dockerfile \
        -t "${ECR_REPO_NAME}:${IMAGE_TAG}" .

    print_step "Pushing image: $LAMBDA_IMAGE_URI"
    docker tag "${ECR_REPO_NAME}:${IMAGE_TAG}" "$LAMBDA_IMAGE_URI"
    docker push "$LAMBDA_IMAGE_URI"
}

validate_template() {
    print_step "Validating CloudFormation template"
    aws_cmd cloudformation validate-template --template-body "file://$TEMPLATE_FILE" >/dev/null
    print_complete "Template validation successful"
}

show_status() {
    if aws_cmd cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1; then
        aws_cmd cloudformation describe-stacks \
            --stack-name "$STACK_NAME" \
            --query 'Stacks[0].{StackName:StackName,StackStatus:StackStatus,LastUpdatedTime:LastUpdatedTime,Outputs:Outputs}'
    else
        print_warning "Stack $STACK_NAME does not exist"
    fi
}

deploy_stack() {
    local role_arn
    local role_name
    local config_bucket=""
    role_arn=$(get_lambda_role_arn)
    role_name=$(get_lambda_role_name)

    if [ -z "$LAMBDA_IMAGE_URI" ]; then
        print_error "Lambda image URI is empty."
        return 1
    fi

    if [ -z "$role_arn" ] || [ -z "$role_name" ]; then
        print_error "Lambda execution role outputs are incomplete for stack: $ROLE_STACK_NAME"
        return 1
    fi

    if [[ "$APP_CONFIG_S3_URI" =~ ^s3://([^/]+) ]]; then
        config_bucket="${BASH_REMATCH[1]}"
    elif [ -n "$APP_CONFIG_S3_URI" ] && [ "$APP_CONFIG_S3_URI" != "null" ]; then
        print_error "Invalid s3_app_config_uri: $APP_CONFIG_S3_URI (expected s3://bucket/key)"
        return 1
    fi

    if [ "$AUTO_CONFIRM" = false ]; then
        print_resource_summary "$RESOURCE_NAME" "$ENVIRONMENT" "deploy"
        confirm_deployment "Deploy document ingestion Lambda?" || exit 0
    fi

    print_step "Deploying stack: $STACK_NAME"
    aws_cmd cloudformation deploy \
        --stack-name "$STACK_NAME" \
        --template-file "$TEMPLATE_FILE" \
        --capabilities CAPABILITY_NAMED_IAM \
        --parameter-overrides \
            ProjectName="$PROJECT_NAME" \
            Environment="$ENVIRONMENT" \
            AWSRegion="$AWS_REGION" \
            FunctionName="$FUNCTION_NAME" \
            LambdaImageUri="$LAMBDA_IMAGE_URI" \
            LambdaMemorySize="$LAMBDA_MEMORY_SIZE" \
            LambdaTimeout="$LAMBDA_TIMEOUT" \
            LambdaExecutionRoleArn="$role_arn" \
            LambdaExecutionRoleName="$role_name" \
            AppConfigPath="$APP_CONFIG_S3_URI" \
            ConfigS3Bucket="$config_bucket" \
            DocumentBucketName="$DOCUMENT_BUCKET_NAME" \
            LandingPrefix="$LANDING_PREFIX" \
            RawPrefix="$RAW_PREFIX" \
            IndexPrefix="$INDEX_PREFIX" \
            ArtifactsPrefix="$ARTIFACTS_PREFIX" \
            CatalogPrefix="$CATALOG_PREFIX" \
            DefaultTenantId="$DEFAULT_TENANT_ID" \
            DefaultCollectionId="$DEFAULT_COLLECTION_ID" \
            PdfTextProbePages="$PDF_TEXT_PROBE_PAGES"

    print_complete "Document ingestion Lambda deployed"
}

delete_stack() {
    if [ "$AUTO_CONFIRM" = false ]; then
        confirm_destructive_action "$ENVIRONMENT" "delete $STACK_NAME" || exit 0
    fi

    print_step "Deleting stack: $STACK_NAME"
    aws_cmd cloudformation delete-stack --stack-name "$STACK_NAME"
    aws_cmd cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
    print_complete "Stack deleted"
}

case "$ACTION" in
    validate)
        validate_template
        ;;
    status)
        show_status
        ;;
    build)
        build_and_push_image
        print_complete "Image pushed: $LAMBDA_IMAGE_URI"
        ;;
    deploy|update)
        validate_template
        if [ "$SKIP_BUILD" = false ] && [ -z "$LAMBDA_IMAGE_URI" ]; then
            build_and_push_image
        elif [ -z "$LAMBDA_IMAGE_URI" ]; then
            ensure_ecr_repo
            LAMBDA_IMAGE_URI="$(get_ecr_repo_uri):${IMAGE_TAG}"
        fi
        deploy_stack
        ;;
    delete)
        delete_stack
        ;;
esac
