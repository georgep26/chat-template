# IAM Policies

This directory contains IAM managed policies that define permissions for various AWS services used by the application.

## Purpose

These policies are designed to be reusable and can be attached to IAM roles as needed. They follow the principle of least privilege, granting only the minimum permissions required for specific operations.

## Available Policies

### 1. Secrets Manager Policy (`secrets_manager_policy.yaml`)

Grants read access to AWS Secrets Manager for retrieving database credentials.

**Permissions:**
- `secretsmanager:GetSecretValue` - Retrieve secret values
- `secretsmanager:DescribeSecret` - Get secret metadata

**Resources:**
- Supports multiple secret name patterns:
  - `${ProjectName}-rag-chat-db-connection-${Environment}-*`
  - `${ProjectName}-${Environment}-db-credentials*`
  - `${ProjectName}-*-db-connection-${Environment}-*`

**Usage:**
Attach this policy to roles that need to access database credentials stored in Secrets Manager.

### 2. S3 Evaluation Policy (`s3_policy.yaml`)

Grants access to S3 buckets for uploading evaluation results.

**Permissions:**
- `s3:PutObject` - Upload files
- `s3:PutObjectAcl` - Set object ACLs
- `s3:GetObject` - Read files
- `s3:ListBucket` - List bucket contents

**Resources:**
- Can be configured for a specific bucket or use pattern-based access
- Default pattern: `*eval*`

**Usage:**
Attach this policy to roles that need to upload evaluation results to S3 (e.g., GitHub Actions).

### 3. Lambda Invoke Policy (`lambda_policy.yaml`)

Grants permission to invoke AWS Lambda functions.

**Permissions:**
- `lambda:InvokeFunction` - Invoke Lambda functions

**Resources:**
- Can be configured for a specific function or use pattern-based access
- Default pattern: `${ProjectName}-*-${Environment}-*`

**Usage:**
Attach this policy to roles that need to invoke Lambda functions during evaluation (when running in lambda mode).

### 4. Bedrock Evaluation Policy (`bedrock_policy.yaml`)

Grants access to AWS Bedrock for LLM inference during evaluation.

**Permissions:**
- `bedrock:InvokeModel` - Invoke foundation models
- `bedrock:InvokeModelWithResponseStream` - Invoke models with streaming
- `bedrock:Converse` - Use Converse API
- `bedrock:ConverseStream` - Use Converse API with streaming
- `bedrock:Retrieve` - Retrieve from knowledge bases
- `bedrock:RetrieveAndGenerate` - Retrieve and generate responses

**Resources:**
- Foundation models: `arn:aws:bedrock:${AWSRegion}::foundation-model/*`
- Knowledge bases: `arn:aws:bedrock:${AWSRegion}:${AWS::AccountId}:knowledge-base/*`

**Usage:**
Attach this policy to roles that need to invoke Bedrock models for evaluation judge models.

## Deployment

These policies can be deployed as standalone CloudFormation stacks or referenced in other templates.

### Standalone Deployment

```bash
# Deploy Secrets Manager policy
aws cloudformation create-stack \
  --stack-name chat-template-dev-secrets-manager-policy \
  --template-body file://infra/policies/secrets_manager_policy.yaml \
  --parameters ParameterKey=ProjectName,ParameterValue=chat-template \
               ParameterKey=Environment,ParameterValue=dev \
               ParameterKey=AWSRegion,ParameterValue=us-east-1

# Deploy S3 policy
aws cloudformation create-stack \
  --stack-name chat-template-dev-s3-policy \
  --template-body file://infra/policies/s3_policy.yaml \
  --parameters ParameterKey=ProjectName,ParameterValue=chat-template \
               ParameterKey=Environment,ParameterValue=dev

# Deploy Lambda policy
aws cloudformation create-stack \
  --stack-name chat-template-dev-lambda-policy \
  --template-body file://infra/policies/lambda_policy.yaml \
  --parameters ParameterKey=ProjectName,ParameterValue=chat-template \
               ParameterKey=Environment,ParameterValue=dev \
               ParameterKey=AWSRegion,ParameterValue=us-east-1

# Deploy Bedrock policy
aws cloudformation create-stack \
  --stack-name chat-template-dev-bedrock-policy \
  --template-body file://infra/policies/bedrock_policy.yaml \
  --parameters ParameterKey=ProjectName,ParameterValue=chat-template \
               ParameterKey=Environment,ParameterValue=dev \
               ParameterKey=AWSRegion,ParameterValue=us-east-1
```

### Referencing in Other Templates

You can reference these policies in other CloudFormation templates using their exported ARNs:

```yaml
Parameters:
  SecretsManagerPolicyArn:
    Type: String
    Description: 'ARN of the Secrets Manager policy'

Resources:
  MyRole:
    Type: AWS::IAM::Role
    Properties:
      ManagedPolicyArns:
        - !Ref SecretsManagerPolicyArn
```

## Customization

Each policy accepts parameters that allow customization:

- **ProjectName**: Name of the project (default: `chat-template`)
- **Environment**: Environment name (default: `dev`)
- **AWSRegion**: AWS region (default: `us-east-1`)
- **Service-specific parameters**: Some policies have additional parameters (e.g., `S3BucketName`, `LambdaFunctionName`)

## Security Best Practices

1. **Least Privilege**: These policies grant only the minimum permissions needed
2. **Resource Scoping**: Policies use ARN patterns to limit access to specific resources
3. **Environment Separation**: Policies are scoped by environment to prevent cross-environment access
4. **Tagging**: All policies include tags for resource management and cost tracking

## Integration with GitHub Actions

These policies are designed to work with the GitHub Actions IAM role (`infra/roles/evals_github_action_role.yaml`), which uses OIDC authentication. See the roles README for more information.

