# CloudFormation Templates

This directory contains AWS CloudFormation templates for deploying the RAG chat application infrastructure.

## Structure

```
cloudformation/
├── db_secret_template.yaml          # Database secret in Secrets Manager
├── knowledge_base_template.yaml     # AWS Bedrock Knowledge Base
├── lambda_template.yaml             # RAG Lambda function
├── light_db_template.yaml           # Aurora Serverless v2 PostgreSQL database
├── s3_bucket_template.yaml          # S3 bucket for knowledge base documents
└── README.md                         # This file
```

**Note:** Deployment scripts are located in `scripts/deploy/` directory.

## Available Templates

### Database Templates

- **`light_db_template.yaml`**: Aurora Serverless v2 PostgreSQL database with pgvector extension
  - Deployed via: `./scripts/deploy/deploy_chat_template_db.sh`
  - Creates: Aurora cluster, database, security groups, parameter groups

- **`db_secret_template.yaml`**: Secrets Manager secret for database credentials
  - Deployed via: `./scripts/deploy/deploy_chat_template_db.sh`
  - Creates: Secrets Manager secret with database connection details

### Application Templates

- **`lambda_template.yaml`**: RAG chat Lambda function with LangGraph orchestration
  - Deployed via: `./scripts/deploy/deploy_rag_lambda.sh`
  - Creates: Lambda function, CloudWatch log group
  - Requires: Lambda execution role (deployed separately via `infra/roles/lambda_execution_role.yaml`)

- **`knowledge_base_template.yaml`**: AWS Bedrock Knowledge Base for RAG retrieval
  - Deployed via: `./scripts/deploy/deploy_knowledge_base.sh`
  - Creates: Knowledge base, data source, IAM roles for Bedrock

- **`s3_bucket_template.yaml`**: S3 bucket for storing knowledge base documents
  - Deployed via: `./scripts/deploy/deploy_s3_bucket.sh`
  - Creates: S3 bucket with versioning and lifecycle rules

## Deployment Order

Deploy infrastructure in this order:

1. **Database**: `./scripts/deploy/deploy_chat_template_db.sh dev deploy`
2. **S3 Bucket** (optional): `./scripts/deploy/deploy_s3_bucket.sh dev deploy`
3. **Knowledge Base**: `./scripts/deploy/deploy_knowledge_base.sh dev deploy`
4. **Lambda Execution Role**: Deploy `infra/roles/lambda_execution_role.yaml` separately
5. **Lambda Function**: `./scripts/deploy/deploy_rag_lambda.sh dev deploy`

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- AWS account with necessary permissions
- Appropriate IAM permissions for CloudFormation operations
- Docker (for Lambda deployment)

## Usage

Each template has its own deployment script. See individual script help for details:

```bash
# Database
./scripts/deploy/deploy_chat_template_db.sh dev deploy --master-password <password>

# S3 Bucket
./scripts/deploy/deploy_s3_bucket.sh dev deploy

# Knowledge Base
./scripts/deploy/deploy_knowledge_base.sh dev deploy --s3-bucket <bucket-name>

# Lambda Function
./scripts/deploy/deploy_rag_lambda.sh dev deploy
```

## Important Notes

⚠️ **These templates are project-specific** - They are designed for the RAG chat application.

- Review and modify resource configurations based on your requirements
- Update IAM permissions according to your security policies
- Adjust resource naming conventions to match your organization's standards
- Templates are deployed independently using dedicated deployment scripts
