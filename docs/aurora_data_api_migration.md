# Aurora Data API Migration Guide

This document explains how to use the new Aurora Data API backend for chat history storage, which allows the Lambda function to run outside the VPC.

## Overview

The RAG Lambda now supports two database connection methods:

1. **`postgres`** (existing): Direct PostgreSQL connection via `psycopg`
   - Requires Lambda to be in VPC
   - Uses traditional connection pooling
   - Lower latency for high-frequency queries

2. **`aurora_data_api`** (new): Aurora Data API via HTTP
   - **No VPC required** - Lambda can run outside VPC
   - Managed connection handling by AWS
   - Better for serverless architectures
   - Cost savings (no NAT Gateway/VPC endpoints needed)

## Benefits of Aurora Data API

- **No VPC Required**: Lambda can be deployed without VPC configuration
- **Cost Savings**: Eliminates need for NAT Gateway (~$32/month) and VPC endpoints (~$14/month)
- **Simplified Networking**: No security group rules needed for database access
- **Better for Serverless**: No connection pooling concerns
- **IAM-Based Auth**: Uses Secrets Manager, no password handling in code

## Prerequisites

1. **Aurora HTTP Endpoint Enabled**: Your Aurora cluster must have HTTP endpoint enabled
   - Already configured in `light_db_template.yaml` with `EnableHttpEndpoint: true`

2. **IAM Permissions**: Lambda execution role must have RDS Data API permissions
   - Already added to `lambda_execution_role.yaml`

3. **Secrets Manager**: Database credentials must be in Secrets Manager
   - Already configured in your setup

## Configuration

### Option 1: Using Aurora Data API (Recommended for Cost Savings)

Update your `config/app_config.yml`:

```yaml
rag_chat:
  chat_history_store:
    memory_backend_type: "aurora_data_api"
    db_cluster_arn: "arn:aws:rds:us-east-1:123456789012:cluster:chat-template-light-db-cluster-dev"
    db_credentials_secret_arn: "arn:aws:secretsmanager:us-east-1:123456789012:secret:chat-template-db-connection-dev-xxxxx"
    database_name: "rag_chat_db"
    table_name: "chat_history"
```

### Option 2: Using Traditional PostgreSQL Connection

Keep your existing configuration:

```yaml
rag_chat:
  chat_history_store:
    memory_backend_type: "postgres"
    db_connection_secret_name: "chat-template-db-connection-dev"
    table_name: "chat_history"
```

## Getting Required Values

### Database Cluster ARN

Get from CloudFormation stack outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name chat-template-light-db-dev \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`DBClusterIdentifier`].OutputValue' \
  --output text
```

Then construct ARN: `arn:aws:rds:<region>:<account-id>:cluster:<cluster-id>`

Or get directly from RDS:

```bash
aws rds describe-db-clusters \
  --db-cluster-identifier chat-template-light-db-cluster-dev \
  --region us-east-1 \
  --query 'DBClusters[0].DBClusterArn' \
  --output text
```

### Database Credentials Secret ARN

Get from Secrets Manager:

```bash
aws secretsmanager describe-secret \
  --secret-id chat-template-db-connection-dev \
  --region us-east-1 \
  --query 'ARN' \
  --output text
```

Or from CloudFormation stack (if secret stack exists):

```bash
aws cloudformation describe-stacks \
  --stack-name chat-template-db-secret-dev \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`SecretArn`].OutputValue' \
  --output text
```

## Lambda Deployment

### With Aurora Data API (No VPC)

When using `aurora_data_api` backend, you can deploy Lambda **without VPC configuration**:

```bash
# Deploy Lambda without VPC (simpler, cheaper)
./scripts/deploy/deploy_rag_lambda.sh dev deploy \
  --skip-vpc  # If you add this option, or just omit VPC parameters
```

However, the current deployment script still requires VPC parameters. To fully remove VPC requirement, you would need to:

1. Make VPC parameters optional in `lambda_template.yaml`
2. Update deployment script to skip VPC when using Data API

### With Traditional PostgreSQL (VPC Required)

When using `postgres` backend, Lambda **must** be in VPC:

```bash
# Deploy Lambda with VPC (required for postgres backend)
./scripts/deploy/deploy_rag_lambda.sh dev deploy
```

## Migration Steps

### Step 1: Update Configuration

Update `config/app_config.yml` to use `aurora_data_api` backend with required ARNs.

### Step 2: Deploy Updated Lambda

Deploy the updated Lambda code:

```bash
./scripts/deploy/deploy_rag_lambda.sh dev deploy
```

### Step 3: Verify

Test the Lambda function to ensure it can:
- Retrieve chat history
- Store new messages
- Store metadata

## Code Changes

### New Files

- `src/rag_lambda/memory/data_api_store.py`: Aurora Data API implementation

### Modified Files

- `src/rag_lambda/memory/factory.py`: Added `aurora_data_api` backend support
- `src/rag_lambda/main.py`: Added configuration handling for Data API
- `infra/roles/lambda_execution_role.yaml`: Added RDS Data API permissions

## Limitations

### Aurora Data API Limitations

1. **No Transactions**: Cannot execute multiple statements in a single transaction
2. **Parameterized Queries Only**: All queries must use parameters (SQL injection protection)
3. **Some PostgreSQL Features**: Not all PostgreSQL features are available via Data API
4. **Latency**: Slightly higher latency than direct connections (HTTP overhead)

### Current Implementation Notes

- Messages are inserted one by one (Data API doesn't support batch inserts easily)
- Metadata upserts work correctly
- Table creation uses `IF NOT EXISTS` (may need error handling)

## Troubleshooting

### Error: "BadRequestException"

- Check that Aurora HTTP endpoint is enabled
- Verify cluster ARN is correct
- Ensure IAM role has `rds-data:ExecuteStatement` permission

### Error: "ForbiddenException"

- Check Secrets Manager ARN is correct
- Verify Lambda execution role has access to the secret
- Ensure secret contains valid database credentials

### Error: "Table does not exist"

- Tables are created automatically on first use
- Check that database name is correct
- Verify Lambda has permission to create tables

### Messages Not Appearing

- Check CloudWatch logs for Data API errors
- Verify conversation_id is being used correctly
- Check that table was created successfully

## Cost Comparison

### With VPC (postgres backend)
- NAT Gateway: ~$32/month
- VPC Endpoints: ~$14/month
- **Total: ~$46/month** + data transfer

### Without VPC (aurora_data_api backend)
- No NAT Gateway needed
- No VPC Endpoints needed
- **Total: $0/month** for networking + data transfer

**Savings: ~$46/month** by using Aurora Data API

## Rollback

To rollback to PostgreSQL connection:

1. Update `config/app_config.yml` to use `memory_backend_type: "postgres"`
2. Redeploy Lambda
3. Ensure Lambda is in VPC (if not already)

## Additional Resources

- [Aurora Data API Documentation](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html)
- [RDS Data API Boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rds-data.html)

