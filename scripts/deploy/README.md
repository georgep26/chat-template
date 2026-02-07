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

After deployment, add the role ARN to your GitHub environment or repository secrets as `AWS_EVALS_ROLE_ARN`.

### GitHub Actions Deployer Role (`deploy_deployer_github_action_role.sh`)

Deploy the IAM role used by the **deploy workflow** (`.github/workflows/deploy.yml`) for OIDC authentication. This role has permissions to run the full deployment (network, S3, DB, knowledge base, Lambda, cost tags) scoped to the specified environment. Deploy one stack per environment; use the role ARN as the `AWS_DEPLOYER_ROLE_ARN` secret for the matching GitHub environment.

**Prerequisite:** Create the GitHub OIDC identity provider in AWS before using this script. See [docs/oidc_github_identity_provider_setup.md](../docs/oidc_github_identity_provider_setup.md).

**Usage:**
```bash
./scripts/deploy/deploy_deployer_github_action_role.sh <environment> [action] [options]
```

**Environments:** `dev` (default), `staging`, `prod`

**Actions:**
- `deploy` (default) - Deploy the stack
- `update` - Update the stack
- `delete` - Delete the stack
- `validate` - Validate the template
- `status` - Show stack status

**Required Options (for deploy/update):**
- `--aws-account-id <id>` - AWS Account ID (12 digits)
- `--github-org <org>` - GitHub organization or username
- `--github-repo <repo>` - GitHub repository name
- `--oidc-provider-arn <arn>` - ARN of GitHub OIDC identity provider (create first; see docs)

**Optional Options:**
- `--region <region>` - AWS region (default: us-east-1)
- `--project-name <name>` - Project name (default: chat-template)

**Examples:**
```bash
# Deploy to development environment
./scripts/deploy/deploy_deployer_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template \
  --oidc-provider-arn arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com

# Deploy to staging
./scripts/deploy/deploy_deployer_github_action_role.sh staging deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template \
  --oidc-provider-arn arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com

# Check status
./scripts/deploy/deploy_deployer_github_action_role.sh dev status

# Validate template
./scripts/deploy/deploy_deployer_github_action_role.sh dev validate
```

After deployment, add the role ARN to the **GitHub environment** secret `AWS_DEPLOYER_ROLE_ARN` (Repository Settings → Environments → &lt;env&gt; → Environment secrets) so the deploy workflow can assume this role. For evaluation workflows (run-evals), set `AWS_EVALS_ROLE_ARN` on the same or different environments as needed.

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

### Destroy All Resources (`destroy_all.sh`)

Delete all infrastructure for a given environment by calling the `delete` action on each deploy script in the correct dependency order (reverse of deploy).

**Usage:**
```bash
./scripts/deploy/destroy_all.sh <environment> [options]
```

**Environments:** `dev`, `staging`, `prod`

**Options:**
- `--region <region>` - AWS region (default: `us-east-1`)
- `--force`, `-y` - Auto-confirm each component (single prompt at start)
- `--skip-lambda` - Do not destroy Lambda stack
- `--skip-kb` - Do not destroy Knowledge Base stack
- `--skip-db` - Do not destroy Database stack
- `--skip-s3` - Do not destroy S3 bucket stack
- `--skip-network` - Do not destroy Network stack

**Destroy Order:** Lambda → Knowledge Base → Database → S3 Bucket → Network (dependencies removed first).

**Examples:**
```bash
# Destroy development (prompts per component)
./scripts/deploy/destroy_all.sh dev

# Destroy staging with custom region
./scripts/deploy/destroy_all.sh staging --region us-west-2

# Destroy with single confirmation
./scripts/deploy/destroy_all.sh dev --force

# Destroy only app components, keep network
./scripts/deploy/destroy_all.sh dev --skip-network
```

**Troubleshooting destroy:**
- **S3 "bucket is not empty"**: The S3 script now empties versioned buckets (all object versions and delete markers) before deleting the stack. Re-run destroy for that environment.
- **Knowledge Base "Unable to delete data from vector store"**: The Knowledge Base template now sets `DataDeletionPolicy: RETAIN` on the data source so stack delete does not try to clear the vector store. For an *existing* stack that already failed to delete: update the Knowledge Base stack once (e.g. run `deploy_knowledge_base.sh <env> update`) so the data source gets this policy, then run `destroy_all.sh` again. Or set the data source's `dataDeletionPolicy` to `RETAIN` in the AWS Console / CLI and retry stack delete.

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

### AWS Organizations Accounts (`deploy_accounts.sh`)

Create three member accounts (dev, staging, prod) under the management account using AWS Organizations. Optionally create per-account budgets with email alerts. **Must be run from the management account** with Organizations permissions. See [docs/aws_organizations.md](../docs/aws_organizations.md) for background.

**Usage:**
```bash
./scripts/deploy/deploy_accounts.sh [options]
```

**Options:**
- `--project-name <name>` - Project name for account names (default: `chat-template`)
- `--dev-email <email>` - Email for dev account (default: `<project>+dev@example.com`)
- `--staging-email <email>` - Email for staging account
- `--prod-email <email>` - Email for prod account
- `--budget-alert-email <email>` - Enable monthly budgets and send alerts to this email
- `--dev-budget-usd <amount>` - Monthly budget limit for dev (default: 75)
- `--staging-budget-usd <amount>` - Monthly budget limit for staging (default: 150)
- `--prod-budget-usd <amount>` - Monthly budget limit for prod (default: 500)
- `--org-access-role-name <name>` - Role created in new accounts (default: `OrganizationAccountAccessRole`)
- `--out-json <path>` - Output JSON file (default: `accounts.json`)
- `--poll-sleep-seconds <n>` - Seconds between status polls (default: 15)
- `--poll-max-minutes <n>` - Max minutes to wait per account creation (default: 20)
- `--help` - Show help

All options can be set via environment variables (e.g. `PROJECT_NAME`, `DEV_EMAIL`, `BUDGET_ALERT_EMAIL`).

**Examples:**
```bash
# Create accounts with default project name and email pattern
./scripts/deploy/deploy_accounts.sh

# Custom project and emails
./scripts/deploy/deploy_accounts.sh --project-name myapp \
  --dev-email myapp+dev@example.com \
  --staging-email myapp+staging@example.com \
  --prod-email myapp+prod@example.com

# Enable budget alerts
BUDGET_ALERT_EMAIL=you@yourdomain.com ./scripts/deploy/deploy_accounts.sh
```

The script writes an `accounts.json` (or `--out-json` path) with project name, management account ID, dev/staging/prod account IDs and emails, and the org access role name. To assume the role in a member account: `aws sts assume-role --role-arn arn:aws:iam::<ACCOUNT_ID>:role/OrganizationAccountAccessRole --role-session-name <name>`.

## Prerequisites

### For AWS Organizations Accounts:
- AWS CLI installed and configured
- Run from the **management account** with `organizations:CreateAccount`, `organizations:DescribeOrganization`, `organizations:DescribeCreateAccountStatus`, `organizations:ListAccounts`; for budgets: `budgets:CreateBudget`, `budgets:CreateNotification`, `budgets:CreateSubscriber`, `budgets:DescribeBudget`

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

2. **Add Role ARNs to GitHub Secrets:**
   - Go to your GitHub repository → **Settings** → **Environments** → create `dev`, `staging`, `prod`
   - For each environment, add **environment secrets**:
     - `AWS_DEPLOYER_ROLE_ARN`: ARN from the deployer role (for the deploy workflow)
     - `AWS_EVALS_ROLE_ARN`: ARN from the evals role (for run-evals workflow)
   - Or use **Secrets and variables** → **Actions** for repository-level secrets

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
