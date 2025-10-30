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

## Prerequisites

### For CloudFormation:
- AWS CLI installed and configured
- Appropriate AWS permissions for CloudFormation operations

### For Terraform:
- Terraform installed
- AWS CLI configured with appropriate credentials
- AWS account with necessary permissions

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
