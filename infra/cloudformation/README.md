# CloudFormation Templates

This directory contains AWS CloudFormation templates for deploying the Python template project infrastructure.

## Structure

```
cloudformation/
├── template.yaml      # Main CloudFormation template
├── parameters.yaml    # Parameter values for different environments
└── README.md          # This file
```

**Note:** Deployment scripts are located in `scripts/deploy/` directory.

## Template Overview

The CloudFormation template creates the following AWS resources:

- **S3 Bucket**: For storing application data with encryption and versioning
- **DynamoDB Table**: For application data storage with point-in-time recovery
- **IAM Role**: For application execution with appropriate permissions
- **CloudWatch Log Group**: For application logging

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- AWS account with necessary permissions
- Appropriate IAM permissions for CloudFormation operations

## Usage

### Quick Start

Deploy using the centralized deployment script:

1. Deploy to development environment:
   ```bash
   ./scripts/deploy/cloudformation.sh dev
   ```

2. Deploy to staging environment:
   ```bash
   ./scripts/deploy/cloudformation.sh staging
   ```

3. Deploy to production environment:
   ```bash
   ./scripts/deploy/cloudformation.sh prod
   ```

### Manual Deployment

1. Navigate to the cloudformation directory:
   ```bash
   cd infra/cloudformation
   ```

2. Validate the template:
   ```bash
   aws cloudformation validate-template --template-body file://template.yaml
   ```

3. Deploy the stack:
   ```bash
   aws cloudformation create-stack \
     --stack-name python-template-dev \
     --template-body file://template.yaml \
     --parameters ParameterKey=Environment,ParameterValue=dev \
     --capabilities CAPABILITY_NAMED_IAM
   ```

### Available Actions

The deployment script supports the following actions:

- `deploy` (default): Deploy or update the stack
- `update`: Update an existing stack
- `delete`: Delete the stack
- `validate`: Validate the template
- `status`: Show stack status and outputs

Examples:
```bash
./scripts/deploy/cloudformation.sh dev validate
./scripts/deploy/cloudformation.sh staging status
./scripts/deploy/cloudformation.sh prod delete
```

## Parameters

The template accepts the following parameters:

- `ProjectName`: Name of the project (default: python-template)
- `Environment`: Environment name (dev, staging, prod)
- `AWSRegion`: AWS region (default: us-east-1)

## Outputs

After successful deployment, the stack provides the following outputs:

- `S3BucketName`: Name of the created S3 bucket
- `S3BucketArn`: ARN of the S3 bucket
- `DynamoDBTableName`: Name of the DynamoDB table
- `DynamoDBTableArn`: ARN of the DynamoDB table
- `ExecutionRoleArn`: ARN of the IAM execution role

## Environment-Specific Features

- **Development**: Basic configuration with 7-day log retention
- **Staging**: Enhanced monitoring and 14-day log retention
- **Production**: Full monitoring, point-in-time recovery, and 30-day log retention

## Cleanup

To remove all resources:

```bash
./scripts/deploy/cloudformation.sh <environment> delete
```

Or manually:
```bash
aws cloudformation delete-stack --stack-name python-template-<environment>
```

## Important Notes

⚠️ **These are example templates** - Customize them for your specific project needs before deployment.

- Review and modify resource configurations based on your requirements
- Update IAM permissions according to your security policies
- Adjust resource naming conventions to match your organization's standards
- Consider additional resources like VPC, security groups, or Lambda functions as needed
