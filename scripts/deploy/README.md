# Deployment Scripts

This directory contains deployment scripts for different infrastructure tools.

## Available Scripts

### CloudFormation Deployment (`cloudformation.sh`)

Deploy AWS infrastructure using CloudFormation templates.

**Usage:**
```bash
./scripts/deploy/cloudformation.sh <environment> [action]
```

**Environments:** `dev`, `staging`, `prod`

**Actions:**
- `deploy` (default) - Deploy or update the stack
- `update` - Update an existing stack
- `delete` - Delete the stack
- `validate` - Validate the template
- `status` - Show stack status and outputs

**Examples:**
```bash
# Deploy to development
./scripts/deploy/cloudformation.sh dev

# Validate production template
./scripts/deploy/cloudformation.sh prod validate

# Check staging status
./scripts/deploy/cloudformation.sh staging status

# Delete development stack
./scripts/deploy/cloudformation.sh dev delete
```

### Terraform Deployment (`terraform.sh`)

Deploy AWS infrastructure using Terraform configurations.

**Usage:**
```bash
./scripts/deploy/terraform.sh <environment> [action]
```

**Environments:** `dev`, `staging`, `prod`

**Actions:**
- `deploy` (default) - Deploy the infrastructure
- `plan` - Show deployment plan
- `destroy` - Destroy the infrastructure
- `init` - Initialize Terraform
- `validate` - Validate Terraform configuration
- `output` - Show Terraform outputs

**Examples:**
```bash
# Deploy to development
./scripts/deploy/terraform.sh dev

# Plan staging deployment
./scripts/deploy/terraform.sh staging plan

# Show production outputs
./scripts/deploy/terraform.sh prod output

# Destroy development infrastructure
./scripts/deploy/terraform.sh dev destroy
```

### GitHub Actions IAM Role Deployment for Evaluations (`deploy_evals_github_action_role.sh`)

Deploy IAM policies and role for GitHub Actions OIDC authentication to enable evaluation workflows.

**Usage:**
```bash
./scripts/deploy/deploy_evals_github_action_role.sh <environment> [action] [options]
```

**Environments:** `dev` (default), `staging`, `prod`

**Actions:**
- `deploy` (default) - Deploy the stacks (policies and role)
- `update` - Update the stacks
- `delete` - Delete the stacks
- `validate` - Validate the templates
- `status` - Show stack status

**Required Options:**
- `--aws-account-id <id>` - AWS Account ID (12 digits)
- `--github-org <org>` - GitHub organization or username
- `--github-repo <repo>` - GitHub repository name

**Optional Options:**
- `--github-branch <branch>` - GitHub branch name (default: development)
- `--region <region>` - AWS region (default: us-east-1)
- `--include-lambda-policy` - Include Lambda invoke policy (for lambda mode evaluations)
- `--oidc-provider-arn <arn>` - ARN of existing OIDC provider (creates new if not provided)
- `--project-name <name>` - Project name (default: chat-template)

**Examples:**
```bash
# Deploy to development environment
./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template

# Deploy to staging with custom branch
./scripts/deploy/deploy_evals_github_action_role.sh staging deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template \
  --github-branch main

# Deploy with Lambda policy (for lambda mode)
./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template \
  --include-lambda-policy

# Check status
./scripts/deploy/deploy_evals_github_action_role.sh dev status

# Validate templates
./scripts/deploy/deploy_evals_github_action_role.sh dev validate \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template
```

**Note:** This script deploys the following stacks in order:
1. Secrets Manager Policy
2. S3 Evaluation Policy
3. Bedrock Evaluation Policy
4. Lambda Invoke Policy (optional)
5. GitHub Actions Role

After deployment, add the role ARN to your GitHub repository secrets as `AWS_ROLE_ARN`.

### Full Application Deployment (`deploy_all.sh`)

Deploy the complete RAG chat application infrastructure to a specific environment. This script orchestrates all deployment components in the correct order.

**Usage:**
```bash
./scripts/deploy/deploy_all.sh <environment> [options]
```

**Environments:** `dev`, `staging`, `prod`

**Required Options:**
- `--s3-app-config-uri <uri>` - S3 URI for app config file (e.g., `s3://bucket/key`)

**Optional Options:**
- `--local-app-config-path <path>` - Local app config file to upload to S3
- `--master-password <password>` - Master database password (required if DB doesn't exist)
- `--master-username <username>` - Master database username (default: `postgres`)
- `--region <region>` - AWS region (default: `us-east-1`)
- `--skip-network` - Skip network deployment (use existing VPC)
- `--skip-s3` - Skip S3 bucket deployment (use existing bucket)
- `--skip-db` - Skip database deployment (use existing DB)
- `--skip-kb` - Skip knowledge base deployment (use existing KB)
- `--skip-lambda` - Skip Lambda deployment (use existing Lambda)
- `--vpc-id <vpc-id>` - VPC ID (for Lambda, if not using auto-detection)
- `--subnet-ids <id1,id2,...>` - Subnet IDs (for Lambda, if not using auto-detection)
- `--security-group-ids <id1,...>` - Security group IDs (for Lambda, if not using auto-detection)

**Deployment Order:**
1. Network (VPC, subnets, security groups) - optional, but included by default
2. S3 Bucket (for knowledge base documents)
3. Database (Aurora PostgreSQL) - requires Network
4. Knowledge Base (AWS Bedrock) - requires Database and S3
5. Lambda Function - requires Database, Knowledge Base, and optionally Network

**Examples:**
```bash
# Deploy to development environment
./scripts/deploy/deploy_all.sh dev --s3-app-config-uri s3://my-bucket/config/app_config.yml

# Deploy to staging with custom region
./scripts/deploy/deploy_all.sh staging --s3-app-config-uri s3://my-bucket/config/app_config.yml --region us-west-2

# Deploy to production with all options
./scripts/deploy/deploy_all.sh prod --s3-app-config-uri s3://my-bucket/config/app_config.yml \
  --master-password MySecurePass123 --region us-east-1

# Deploy with local app config file
./scripts/deploy/deploy_all.sh dev --s3-app-config-uri s3://my-bucket/config/app_config.yml \
  --local-app-config-path config/app_config.yml

# Deploy skipping network (use existing VPC)
./scripts/deploy/deploy_all.sh dev --s3-app-config-uri s3://my-bucket/config/app_config.yml \
  --skip-network
```

**Note:** The script will deploy all components in order. If a component already exists, it will be updated (or show 'no updates needed' if already up to date). This is expected behavior for infrastructure that doesn't change frequently (like network and S3).

### Individual Component Scripts

The following scripts are used by `deploy_all.sh` and can also be run individually:

- `deploy_network.sh` - Deploy VPC, subnets, security groups, and VPC endpoints
- `deploy_s3_bucket.sh` - Deploy S3 bucket for knowledge base documents
- `deploy_chat_template_db.sh` - Deploy Aurora Serverless v2 PostgreSQL database
- `deploy_knowledge_base.sh` - Deploy AWS Bedrock Knowledge Base
- `deploy_rag_lambda.sh` - Deploy RAG Lambda function

See each script's help (`--help` or no arguments) for detailed usage information.

### Cost Allocation Tags Activation (`deploy_cost_analysis_tags.sh`)

Activate cost allocation tags in AWS Cost Explorer to enable cost analysis and filtering by tags.

**Usage:**
```bash
./scripts/deploy/deploy_cost_analysis_tags.sh [action] [options]
```

**Actions:**
- `activate` (default) - Activate cost allocation tags
- `list` - List status of default cost allocation tags (Name, Environment, Project)
- `status` - Check status of specific tags

**Options:**
- `--tags <tag1,tag2,...>` - Specific tags to activate/check (default: `Name,Environment,Project`)
- `--region <region>` - AWS region (default: `us-east-1`, but Cost Explorer is global)

**Examples:**
```bash
# Activate all default tags (Name, Environment, Project)
./scripts/deploy/deploy_cost_analysis_tags.sh activate

# Activate specific tags
./scripts/deploy/deploy_cost_analysis_tags.sh activate --tags Name,Environment

# List default cost allocation tags
./scripts/deploy/deploy_cost_analysis_tags.sh list

# Check status of specific tags
./scripts/deploy/deploy_cost_analysis_tags.sh status --tags Name,Environment,Project
```

**Note:** 
- Cost allocation tags must be activated before they can be used in Cost Explorer
- It may take up to 24 hours for activated tags to appear in Cost Explorer
- Tags will only appear in the list once resources are tagged with them
- Cost Explorer is a global service, so region doesn't matter for tag activation

**Default Tags:**
The script activates these tags by default (matching your infrastructure tags):
- `Name` - Resource name tag
- `Environment` - Environment tag (dev, staging, prod)
- `Project` - Project identifier tag

## Prerequisites

### For CloudFormation:
- AWS CLI installed and configured
- Appropriate AWS permissions for CloudFormation operations

### For Terraform:
- Terraform installed
- AWS CLI configured with appropriate credentials
- AWS account with necessary permissions

### For GitHub Actions Role:
- AWS CLI installed and configured
- Appropriate AWS permissions for CloudFormation and IAM operations
- AWS Account ID
- GitHub organization/repository information

### For Full Application Deployment:
- AWS CLI installed and configured
- Appropriate AWS permissions for CloudFormation, IAM, ECR, Lambda, RDS, S3, and Bedrock operations
- Docker installed and running (for Lambda deployment)
- S3 bucket URI for app configuration file
- Database master password (if deploying new database)

### For Cost Allocation Tags:
- AWS CLI installed and configured
- Appropriate AWS permissions for Cost Explorer operations (`ce:UpdateCostAllocationTagsStatus`, `ce:ListCostAllocationTags`)
- Billing and Cost Management access in AWS account

## Features

Both deployment scripts include:

- **Environment-specific deployments** (dev, staging, prod)
- **Colored output** for better readability
- **Error handling** and validation
- **Safety prompts** for destructive operations
- **Status checking** and monitoring
- **Automatic workspace management** (Terraform)
- **Template validation** before deployment

## Getting Started

### Quick Start (Full Application Deployment)

1. **Ensure prerequisites** are met (AWS CLI, Docker, etc.)
2. **Prepare your app config file** and upload it to S3 (or use `--local-app-config-path`)
3. **Run the deployment script:**
   ```bash
   ./scripts/deploy/deploy_all.sh dev --s3-app-config-uri s3://my-bucket/config/app_config.yml
   ```
4. **Monitor the deployment** - the script will show progress for each component

### Individual Component Deployment

1. **Choose your infrastructure tool** (CloudFormation or Terraform)
2. **Ensure prerequisites** are met
3. **Run the appropriate script** with your desired environment
4. **Monitor the deployment** using the status commands

## Customization

These scripts are designed as examples and should be customized for your specific project needs:

- Update resource names and configurations
- Modify environment-specific parameters
- Add additional validation steps
- Include project-specific deployment steps
- Update AWS regions and account settings

## GitHub Actions Deployment

A GitHub Actions workflow (`.github/workflows/deploy.yml`) is available for automated deployments. This workflow uses OIDC authentication and can be triggered manually.

### Prerequisites

1. **Deploy GitHub Actions IAM Role** (if not already done):
   ```bash
   ./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
     --aws-account-id YOUR_ACCOUNT_ID \
     --github-org YOUR_ORG \
     --github-repo chat-template
   ```

2. **Add Role ARN to GitHub Secrets:**
   - Go to your GitHub repository
   - Navigate to **Settings** → **Secrets and variables** → **Actions**
   - Add a secret named `AWS_ROLE_ARN` with the role ARN from step 1

3. **Create GitHub Environments** (optional but recommended):
   - Go to **Settings** → **Environments**
   - Create environments: `dev`, `staging`, `prod`
   - Add the `AWS_ROLE_ARN` secret to each environment

### Using the Workflow

1. **Go to Actions tab** in your GitHub repository
2. **Select "Deploy Application"** workflow
3. **Click "Run workflow"**
4. **Fill in the required inputs:**
   - Environment: `dev`, `staging`, or `prod`
   - S3 App Config URI: `s3://your-bucket/config/app_config.yml`
   - (Optional) Local App Config Path: `config/app_config.yml`
   - (Optional) Master Password: Database password (if deploying new DB)
   - (Optional) Region: AWS region (default: `us-east-1`)
   - (Optional) Skip flags: Check to skip specific components

5. **Click "Run workflow"** to start deployment

The workflow will:
- Checkout the code
- Configure AWS credentials using OIDC
- Run the `deploy_all.sh` script with your inputs
- Show deployment progress and results

### Workflow Inputs

- **environment** (required): `dev`, `staging`, or `prod`
- **s3_app_config_uri** (required): S3 URI for app config file
- **local_app_config_path** (optional): Local config file to upload
- **master_password** (optional): Database password
- **region** (optional): AWS region (default: `us-east-1`)
- **skip_network** (optional): Skip network deployment
- **skip_s3** (optional): Skip S3 bucket deployment
- **skip_db** (optional): Skip database deployment
- **skip_kb** (optional): Skip knowledge base deployment
- **skip_lambda** (optional): Skip Lambda deployment

## Safety Features

- **Confirmation prompts** for destructive operations
- **Template validation** before deployment
- **Environment validation** to prevent mistakes
- **Error handling** with clear error messages
- **Status checking** to verify deployments
- **Idempotent deployments** - safe to run multiple times
