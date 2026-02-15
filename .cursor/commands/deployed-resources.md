# Deployed Resources Report

Generate a comprehensive report of currently deployed AWS resources for a specified environment.

## Instructions

When the user runs this command, follow these steps:

### 1. Get Environment

Ask the user which environment to report on:
- `dev` - Development environment
- `staging` - Staging environment
- `prod` - Production environment

### 2. Read Configuration

Read `infra/infra.yaml` to get:
- Project name
- Environment account ID and region
- List of resources and their stack names
- Deployer profile for the environment

### 3. Query AWS CloudFormation

For each resource defined in `infra.yaml`, query AWS CloudFormation to get:
- Stack name
- Stack status (CREATE_COMPLETE, UPDATE_COMPLETE, etc.)
- Last updated timestamp
- Key outputs (ARNs, endpoints, IDs)

Use the AWS CLI commands:
```bash
# List all stacks for the project
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --query "StackSummaries[?contains(StackName, 'chat-template')]" \
  --region <region> \
  --profile <profile>

# Get stack details
aws cloudformation describe-stacks \
  --stack-name <stack-name> \
  --region <region> \
  --profile <profile>
```

### 4. Generate Report

Create a markdown report with the following structure:

```markdown
# Deployed Resources Report: {environment}

**Generated:** {timestamp}
**Account:** {account_id}
**Region:** {region}
**Profile:** {profile}

## Summary

| Resource | Stack Name | Status | Last Updated |
|----------|------------|--------|--------------|
| network | chat-template-vpc-{env} | CREATE_COMPLETE | 2024-01-15 |
| s3_bucket | chat-template-s3-{env} | UPDATE_COMPLETE | 2024-01-16 |
| ... | ... | ... | ... |

## Resource Details

### Network (VPC)

**Stack:** chat-template-vpc-{env}
**Status:** CREATE_COMPLETE
**Last Updated:** 2024-01-15T10:30:00Z

**Outputs:**
- VPC ID: vpc-xxxxx
- Private Subnets: subnet-xxx, subnet-yyy
- Lambda Security Group: sg-xxxxx

### S3 Bucket

**Stack:** chat-template-s3-{env}
**Status:** UPDATE_COMPLETE
**Last Updated:** 2024-01-16T14:20:00Z

**Outputs:**
- Bucket Name: chat-template-s3-bucket-{env}
- Bucket ARN: arn:aws:s3:::chat-template-s3-bucket-{env}

### Chat Database

**Stack:** chat-template-chat-db-{env}
**Status:** CREATE_COMPLETE
**Last Updated:** 2024-01-15T11:45:00Z

**Outputs:**
- Cluster ARN: arn:aws:rds:...
- Cluster Endpoint: ...
- Secret ARN: arn:aws:secretsmanager:...

### RAG Knowledge Base

**Stack:** chat-template-rag-kb-{env}
**Status:** CREATE_COMPLETE
**Last Updated:** 2024-01-15T12:00:00Z

**Outputs:**
- Knowledge Base ID: xxxxx
- Data Source ID: xxxxx

### RAG Lambda ECR

**Stack:** chat-template-rag-lambda-ecr-{env}
**Status:** CREATE_COMPLETE
**Last Updated:** 2024-01-15T12:15:00Z

**Outputs:**
- Repository URI: xxxxx.dkr.ecr.us-east-1.amazonaws.com/chat-template-rag-lambda-{env}

### RAG Lambda Function

**Stack:** chat-template-rag-lambda-{env}
**Status:** UPDATE_COMPLETE
**Last Updated:** 2024-01-17T09:15:00Z

**Outputs:**
- Function ARN: arn:aws:lambda:...
- Function Name: chat-template-{env}-rag-chat

## Health Indicators

| Resource | Health | Notes |
|----------|--------|-------|
| network | ✅ Healthy | All subnets available |
| s3_bucket | ✅ Healthy | Versioning enabled |
| chat_db | ✅ Healthy | Cluster available |
| rag_knowledge_base | ✅ Healthy | Data source synced |
| rag_lambda_ecr | ✅ Healthy | Repository active |
| rag_lambda | ✅ Healthy | Function active |

## Cost Estimate

Based on current configuration:
- VPC Endpoints: ~$14/month
- Aurora Serverless: ~$0-50/month (usage-based)
- S3: ~$1/month (storage-based)
- Lambda: ~$0-10/month (invocation-based)
- Knowledge Base: ~$0-5/month (query-based)
- ECR: ~$0-1/month (storage-based)

**Estimated Total:** ~$15-80/month (varies with usage)
```

### 5. Save Report

Save the report to `docs/resource_reports/deployed_resources_{env}.md`

Create the `docs/resource_reports/` directory if it doesn't exist.

### 6. Display Summary

Show a summary to the user:
- Number of resources found
- Overall health status
- Any resources in failed or missing state
- Link to the full report

## Error Handling

If a stack is not found:
- Mark it as "NOT DEPLOYED" in the report
- Note any dependencies that may be affected

If AWS credentials are not configured:
- Prompt the user to configure AWS CLI
- Suggest using the deployer profile from infra.yaml

## Example Usage

User: "Generate a deployed resources report for dev"

Assistant should:
1. Read infra/infra.yaml for dev environment config
2. Query AWS for each resource stack
3. Generate the markdown report
4. Save to docs/resource_reports/deployed_resources_dev.md
5. Display summary to user
