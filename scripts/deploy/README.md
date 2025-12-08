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

### GitHub Actions IAM Role Deployment (`deploy_github_action_role.sh`)

Deploy IAM policies and role for GitHub Actions OIDC authentication.

**Usage:**
```bash
./scripts/deploy/deploy_github_action_role.sh <environment> [action] [options]
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
./scripts/deploy/deploy_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template

# Deploy to staging with custom branch
./scripts/deploy/deploy_github_action_role.sh staging deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template \
  --github-branch main

# Deploy with Lambda policy (for lambda mode)
./scripts/deploy/deploy_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org myorg \
  --github-repo chat-template \
  --include-lambda-policy

# Check status
./scripts/deploy/deploy_github_action_role.sh dev status

# Validate templates
./scripts/deploy/deploy_github_action_role.sh dev validate \
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

## Safety Features

- **Confirmation prompts** for destructive operations
- **Template validation** before deployment
- **Environment validation** to prevent mistakes
- **Error handling** with clear error messages
- **Status checking** to verify deployments
