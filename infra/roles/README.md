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

### GitHub Actions Deployer Role (`deployer_role.yaml`)

IAM role for GitHub Actions to run the **deploy workflow** ([.github/workflows/deploy.yml](../../.github/workflows/deploy.yml)) via OIDC. This role has permissions to run [scripts/deploy/deploy_all.sh](../../scripts/deploy/deploy_all.sh) (network, S3, DB, knowledge base, Lambda, cost tags).

**Prerequisite:** Create the GitHub OIDC identity provider in AWS **before** deploying this role. See [docs/oidc_github_identity_provider_setup.md](../../docs/oidc_github_identity_provider_setup.md).

**Trust Relationship:**
- OIDC Provider: GitHub Actions
- Scoped to `repo:${GitHubOrg}/${GitHubRepo}:environment:${Environment}` (dev, staging, or prod)

**Permissions (inline DeployerPolicy):**
- CloudFormation (create/update/delete/describe stacks)
- IAM (CreateRole, PassRole, PutRolePolicy, AttachRolePolicy, etc. for KB and Lambda stacks)
- EC2 (VPC, subnets, security groups, NAT, endpoints)
- S3 (bucket and object operations for KB bucket and app config upload)
- RDS (Aurora cluster/instance, RDS Data API)
- Secrets Manager (create/update DB secret, read)
- Bedrock (Knowledge Base and Data Source create/update/delete, ingestion)
- Lambda (create/update function, update code)
- CloudWatch Logs (log group for Lambda)
- ECR (create repo, lifecycle policy, push image for Lambda container)
- Cost Explorer (list/update cost allocation tags)
- STS (GetCallerIdentity)

**Usage:**
1. Create the GitHub OIDC identity provider (see [docs/oidc_github_identity_provider_setup.md](../../docs/oidc_github_identity_provider_setup.md)).
2. Deploy this role **once per environment** (dev, staging, prod) using the deploy script or CloudFormation directly.
3. In GitHub, set the **environment secret** `AWS_DEPLOYER_ROLE_ARN` to this role’s ARN for each environment (Repository Settings → Environments → &lt;env&gt; → Environment secrets).

**Deployment:** Use [scripts/deploy/deploy_deployer_github_action_role.sh](../../scripts/deploy/deploy_deployer_github_action_role.sh) (recommended):

```bash
./scripts/deploy/deploy_deployer_github_action_role.sh dev deploy \
  --aws-account-id ACCOUNT_ID \
  --github-org YOUR_ORG \
  --github-repo chat-template \
  --oidc-provider-arn arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com
```

Repeat for `staging` and `prod`. Alternatively, deploy via CloudFormation directly:

```bash
aws cloudformation create-stack \
  --stack-name chat-template-dev-deployer-role \
  --template-body file://infra/roles/deployer_role.yaml \
  --parameters \
    ParameterKey=ProjectName,ParameterValue=chat-template \
    ParameterKey=Environment,ParameterValue=dev \
    ParameterKey=GitHubOrg,ParameterValue=YOUR_ORG \
    ParameterKey=GitHubRepo,ParameterValue=chat-template \
    ParameterKey=OIDCProviderArn,ParameterValue=arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

Use the stack output `RoleArn` as the `AWS_DEPLOYER_ROLE_ARN` secret for the matching GitHub environment.

**Note:** This role is separate from the **evals** role (`evals_github_action_role.yaml`). The deploy workflow uses `AWS_DEPLOYER_ROLE_ARN`; the run-evals workflow uses `AWS_EVALS_ROLE_ARN`. Set both secrets on each GitHub environment that runs deploy and/or evals.

---

### GitHub Actions Role for Evaluations (`evals_github_action_role.yaml`)

IAM role for GitHub Actions to run the evaluation pipeline using OIDC authentication.

**Prerequisite:** Create the GitHub OIDC identity provider in AWS **before** deploying this role. See [docs/oidc_github_identity_provider_setup.md](../../docs/oidc_github_identity_provider_setup.md) for setup steps. The deploy script requires the provider ARN via `--oidc-provider-arn`.

**Trust Relationship:**
- OIDC Provider: GitHub Actions (`token.actions.githubusercontent.com`)
- Scoped to specific repository and branch

**Permissions:**
- Secrets Manager access (via managed policy)
- S3 access for evaluation results (via managed policy)
- Lambda invocation (optional, via managed policy)
- Bedrock access for judge models (via managed policy)

**Usage:**
1. Create the GitHub OIDC identity provider (see [docs/oidc_github_identity_provider_setup.md](../../docs/oidc_github_identity_provider_setup.md)).
2. Deploy the required policies first (see `../policies/README.md`)
3. Deploy this role with the policy ARNs and OIDC provider ARN (use [scripts/deploy/deploy_evals_github_action_role.sh](../../scripts/deploy/deploy_evals_github_action_role.sh))
4. Add the role ARN as a GitHub secret: `AWS_EVALS_ROLE_ARN`
5. Update GitHub Actions workflow to use OIDC (see `.github/workflows/run-evals.yml`)

**Deployment:** Use the deploy script (requires OIDC provider ARN from prerequisite step):
```bash
./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
  --aws-account-id ACCOUNT_ID \
  --github-org your-org \
  --github-repo chat-template \
  --oidc-provider-arn arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com
```
Optional: `--knowledge-base-id <KB_ID>` to scope Bedrock KB access to this environment (recommended for same-account multi-env).

## Example

See `lambda_execution_role.yaml` for a complete example of an IAM role definition that can be used with AWS Lambda functions.