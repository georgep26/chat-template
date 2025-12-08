# Infrastructure as Code

This directory contains Infrastructure as Code (IaC) templates and configurations for deploying the Python template project.

⚠️ **Important**: These are example templates that should be customized and overwritten for new projects.

## Structure

```
infra/
├── terraform/           # Terraform configurations
│   ├── main.tf         # Main Terraform configuration
│   ├── variables.tf    # Variable definitions
│   └── outputs.tf      # Output definitions
├── cloudformation/     # CloudFormation templates
│   ├── template.yaml   # Main CloudFormation template
│   ├── parameters.yaml # Parameter values for environments
│   ├── deploy.sh       # Deployment script
│   └── README.md       # CloudFormation documentation
├── policies/           # IAM managed policies
│   ├── secrets_manager_policy.yaml
│   ├── s3_policy.yaml
│   ├── lambda_policy.yaml
│   ├── bedrock_policy.yaml
│   └── README.md
├── roles/              # IAM roles
│   ├── lambda_execution_role.yaml
│   ├── github_actions_role.yaml
│   └── README.md
└── README.md           # This file
```

## Available IaC Options

### 1. Terraform

The Terraform configuration creates the following AWS resources:

- **S3 Bucket**: For storing application data
- **DynamoDB Table**: For application data storage

#### Prerequisites

- [Terraform](https://www.terraform.io/downloads.html) installed
- AWS CLI configured with appropriate credentials
- AWS account with necessary permissions

#### Usage

Deploy using the centralized deployment script:

1. Deploy to development environment:
   ```bash
   ./scripts/deploy/terraform.sh dev
   ```

2. Deploy to other environments:
   ```bash
   ./scripts/deploy/terraform.sh staging
   ./scripts/deploy/terraform.sh prod
   ```

#### Available Actions

- `deploy` (default): Deploy the infrastructure
- `plan`: Show deployment plan
- `destroy`: Destroy the infrastructure
- `init`: Initialize Terraform
- `validate`: Validate Terraform configuration
- `output`: Show Terraform outputs

#### Variables

You can customize the deployment by setting variables:

```bash
terraform apply -var="environment=prod" -var="aws_region=us-west-2"
```

Available variables:
- `aws_region`: AWS region (default: us-east-1)
- `project_name`: Project name (default: python-template)
- `environment`: Environment name (default: dev)

### 2. CloudFormation

The CloudFormation template creates the following AWS resources:

- **S3 Bucket**: For storing application data with encryption and versioning
- **DynamoDB Table**: For application data storage with point-in-time recovery
- **IAM Role**: For application execution with appropriate permissions
- **CloudWatch Log Group**: For application logging

#### Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- AWS account with necessary permissions
- Appropriate IAM permissions for CloudFormation operations

#### Usage

Deploy using the centralized deployment script:

1. Deploy to development environment:
   ```bash
   ./scripts/deploy/cloudformation.sh dev
   ```

2. Deploy to other environments:
   ```bash
   ./scripts/deploy/cloudformation.sh staging
   ./scripts/deploy/cloudformation.sh prod
   ```

#### Available Actions

- `deploy` (default): Deploy or update the stack
- `update`: Update an existing stack
- `delete`: Delete the stack
- `validate`: Validate the template
- `status`: Show stack status and outputs

## Choosing Between Terraform and CloudFormation

### Use Terraform when:
- You need multi-cloud support
- You prefer declarative configuration with state management
- You want advanced features like modules and workspaces
- You need complex dependency management

### Use CloudFormation when:
- You're working exclusively with AWS
- You want native AWS integration
- You prefer YAML/JSON configuration
- You need tight integration with AWS services

## Customization for New Projects

Before using these templates for a new project:

1. **Review and modify** resource configurations based on your requirements
2. **Update naming conventions** to match your organization's standards
3. **Adjust IAM permissions** according to your security policies
4. **Add or remove resources** as needed for your specific use case
5. **Update parameters and variables** to match your project needs

## Getting Started

1. Choose your preferred IaC tool (Terraform or CloudFormation)
2. Use the centralized deployment scripts in `scripts/deploy/`
3. Follow the usage instructions in the respective README files
4. Customize the templates for your project requirements
5. Deploy to your AWS environment

## Deployment Scripts

All deployment scripts are centralized in the `scripts/deploy/` directory:

- `cloudformation.sh` - CloudFormation deployment script
- `terraform.sh` - Terraform deployment script
- `README.md` - Detailed usage instructions for both scripts

This provides a consistent interface for deploying infrastructure regardless of the chosen tool.

## IAM Roles and Policies

### Policies (`policies/`)

Reusable IAM managed policies for common AWS service access patterns:
- **Secrets Manager Policy**: Access to database credentials
- **S3 Policy**: Upload evaluation results to S3
- **Lambda Policy**: Invoke Lambda functions
- **Bedrock Policy**: Invoke Bedrock models for evaluation

See `policies/README.md` for detailed documentation.

### Roles (`roles/`)

IAM roles for specific use cases:
- **Lambda Execution Role**: For RAG Lambda functions
- **GitHub Actions Role**: For CI/CD pipelines using OIDC authentication

See `roles/README.md` for detailed documentation.

### GitHub Actions OIDC Setup

To use OIDC authentication with GitHub Actions:

1. **Deploy the policies** (in order):
   ```bash
   # Deploy each policy stack
   aws cloudformation create-stack --stack-name chat-template-dev-secrets-manager-policy ...
   aws cloudformation create-stack --stack-name chat-template-dev-s3-policy ...
   aws cloudformation create-stack --stack-name chat-template-dev-bedrock-policy ...
   aws cloudformation create-stack --stack-name chat-template-dev-lambda-policy ...  # Optional
   ```

2. **Deploy the GitHub Actions role**:
   ```bash
   aws cloudformation create-stack --stack-name chat-template-dev-github-actions-role \
     --template-body file://infra/roles/github_actions_role.yaml \
     --parameters ... \
     --capabilities CAPABILITY_NAMED_IAM
   ```

3. **Add the role ARN to GitHub secrets**:
   - Go to repository Settings → Secrets and variables → Actions
   - Add secret: `AWS_ROLE_ARN` with the role ARN from the stack output

4. **Update the workflow** (already done in `.github/workflows/run-evals.yml`):
   - Uses `role-to-assume` instead of access keys
   - Requires `permissions: id-token: write`
