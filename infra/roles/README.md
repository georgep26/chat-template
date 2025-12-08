# Cloud Roles and Permissions

This directory contains definitions for cloud roles and permissions, such as IAM roles, IAM policies, and other access control configurations.

## Purpose

Any definition for cloud roles should be added here. This includes:
- IAM roles for applications, services, or users
- IAM policies and permission sets
- Service-specific role configurations (e.g., Lambda execution roles, ECS task roles)
- Cross-account access roles
- Role trust relationships

## Organization

Role definitions can be organized by:
- **Service**: Group roles by the AWS service they're used with (e.g., Lambda, ECS, EC2)
- **Environment**: Separate roles for different environments (dev, staging, prod)
- **Function**: Organize by the function or purpose of the role

## Integration

These role definitions can be:
- Referenced in your main CloudFormation or Terraform templates
- Deployed as standalone stacks/modules
- Included as reusable components across multiple projects

## Available Roles

### Lambda Execution Role (`lambda_execution_role.yaml`)

IAM role for RAG Lambda function execution with Bedrock, Secrets Manager, and VPC access.

**Trust Relationship:**
- Service: `lambda.amazonaws.com`

**Permissions:**
- Bedrock model invocation and knowledge base retrieval
- Secrets Manager access for database credentials
- VPC access for RDS connectivity
- CloudWatch Logs

**Usage:**
Attach this role to Lambda functions that need to access Bedrock, Secrets Manager, and RDS.

### GitHub Actions Role (`github_actions_role.yaml`)

IAM role for GitHub Actions to run the evaluation pipeline using OIDC authentication.

**Trust Relationship:**
- OIDC Provider: GitHub Actions (`token.actions.githubusercontent.com`)
- Scoped to specific repository and branch

**Permissions:**
- Secrets Manager access (via managed policy)
- S3 access for evaluation results (via managed policy)
- Lambda invocation (optional, via managed policy)
- Bedrock access for judge models (via managed policy)

**Usage:**
1. Deploy the required policies first (see `../policies/README.md`)
2. Deploy this role with the policy ARNs as parameters
3. Add the role ARN as a GitHub secret: `AWS_ROLE_ARN`
4. Update GitHub Actions workflow to use OIDC (see `.github/workflows/run-evals.yml`)

**Deployment Example:**
```bash
aws cloudformation create-stack \
  --stack-name chat-template-dev-github-actions-role \
  --template-body file://infra/roles/github_actions_role.yaml \
  --parameters \
    ParameterKey=ProjectName,ParameterValue=chat-template \
    ParameterKey=Environment,ParameterValue=dev \
    ParameterKey=GitHubOrg,ParameterValue=your-org \
    ParameterKey=GitHubRepo,ParameterValue=chat-template \
    ParameterKey=GitHubBranch,ParameterValue=development \
    ParameterKey=SecretsManagerPolicyArn,ParameterValue=arn:aws:iam::ACCOUNT:policy/chat-template-dev-secrets-manager-policy \
    ParameterKey=S3EvaluationPolicyArn,ParameterValue=arn:aws:iam::ACCOUNT:policy/chat-template-dev-s3-evaluation-policy \
    ParameterKey=BedrockEvaluationPolicyArn,ParameterValue=arn:aws:iam::ACCOUNT:policy/chat-template-dev-bedrock-evaluation-policy \
  --capabilities CAPABILITY_NAMED_IAM
```

## Example

See `lambda_execution_role.yaml` for a complete example of an IAM role definition that can be used with AWS Lambda functions.