# RAG Application CloudFormation Templates

This directory contains AWS CloudFormation templates for deploying the RAG (Retrieval-Augmented Generation) application infrastructure.

## Structure

```
cloudformation/
├── template.yaml              # Master template (nested stacks)
├── vpc-infrastructure.yaml   # VPC, subnets, NAT Gateway
├── rds-postgres.yaml         # Aurora Serverless v2 PostgreSQL
├── lambda-function.yaml      # Lambda function with VPC and RDS integration
├── lambda-role-policy.json   # IAM policy examples/reference
├── parameters.yaml           # Parameter values for different environments
└── README.md                 # This file
```

## Template Overview

The CloudFormation templates create the following AWS resources:

- **VPC Infrastructure**: VPC with public/private subnets, Internet Gateway, NAT Gateway
- **Aurora Serverless v2 PostgreSQL**: Database cluster for conversation history
- **Lambda Function**: Container-based Lambda with Bedrock and RDS integration
- **IAM Roles**: Execution role with Bedrock, Secrets Manager, and VPC permissions
- **Security Groups**: Network security for Lambda→RDS communication

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- AWS account with necessary permissions
- Bedrock Knowledge Base created (manual setup required)
- ECR repository with Docker image pushed
- Appropriate IAM permissions for CloudFormation operations

## Knowledge Base Setup

**Important**: The Bedrock Knowledge Base must be created manually before deploying the Lambda stack.

1. Create Bedrock Knowledge Base in AWS Console:
   - Configure data source (S3 bucket with documents)
   - Set up embeddings model (Titan embeddings)
   - Configure vector store (OpenSearch Serverless)
   - Note the Knowledge Base ID

2. Ensure documents have metadata fields:
   - `allowed_groups`: Array or comma-separated list of roles that can access the document
   - `document_type`: Type of document (e.g., "Policy", "Guide")
   - `document_version_number`: Version identifier (e.g., "2023.4")
   - `document_title`: Title of the document

## Deployment Strategy

### Option 1: Deploy Master Template (Nested Stacks)

The `template.yaml` orchestrates all nested stacks:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name rag-app-master-dev \
  --parameter-overrides \
    ProjectName=rag-app \
    Environment=dev \
    AWSRegion=us-east-1 \
    KBId=YOUR_KB_ID \
    ModelId=anthropic.claude-3-5-sonnet-20240620-v1:0 \
    ECRImageUri=ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest \
  --capabilities CAPABILITY_IAM
```

### Option 2: Deploy Components Separately

Deploy stacks in order to have more control:

#### 1. Deploy VPC Infrastructure

```bash
aws cloudformation deploy \
  --template-file vpc-infrastructure.yaml \
  --stack-name rag-app-vpc-dev \
  --parameter-overrides \
    ProjectName=rag-app \
    Environment=dev \
    AWSRegion=us-east-1
```

Capture outputs:
- `VpcId`
- `PrivateSubnet1Id`
- `PrivateSubnet2Id`

#### 2. Deploy RDS PostgreSQL

```bash
aws cloudformation deploy \
  --template-file rds-postgres.yaml \
  --stack-name rag-app-rds-dev \
  --parameter-overrides \
    ProjectName=rag-app \
    Environment=dev \
    VpcId=<VPC_ID_FROM_STEP_1> \
    PrivateSubnet1Id=<SUBNET_1_ID> \
    PrivateSubnet2Id=<SUBNET_2_ID> \
    DBName=ragapp \
    MinACU=0.5 \
    MaxACU=4 \
  --capabilities CAPABILITY_NAMED_IAM
```

Capture outputs:
- `DBClusterEndpoint`
- `DBSecretArn`
- `RDSSecurityGroupId`

#### 3. Build and Push Docker Image

```bash
# Build image
docker build -t rag-app:latest .

# Create ECR repository (if not exists)
aws ecr create-repository --repository-name rag-app

# Get login token
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag rag-app:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest
```

#### 4. Deploy Lambda Function

```bash
aws cloudformation deploy \
  --template-file lambda-function.yaml \
  --stack-name rag-app-lambda-dev \
  --parameter-overrides \
    ProjectName=rag-app \
    Environment=dev \
    AWSRegion=us-east-1 \
    VpcId=<VPC_ID> \
    PrivateSubnet1Id=<SUBNET_1_ID> \
    PrivateSubnet2Id=<SUBNET_2_ID> \
    RDSSecurityGroupId=<RDS_SG_ID> \
    DBClusterEndpoint=<CLUSTER_ENDPOINT> \
    DBName=ragapp \
    DBSecretArn=<SECRET_ARN> \
    KBId=<YOUR_KB_ID> \
    ModelId=anthropic.claude-3-5-sonnet-20240620-v1:0 \
    ECRImageUri=<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest \
    LambdaMemorySize=512 \
    LambdaTimeout=300 \
  --capabilities CAPABILITY_NAMED_IAM
```

## Database Initialization

After RDS deployment, connect to the database and create the messages table:

```sql
-- Connect to Aurora cluster endpoint
psql -h <cluster-endpoint> -U admin -d ragapp

-- Create table for LangChain SQLChatMessageHistory
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    conversation_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for faster lookups
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
```

The `PG_DSN` in Lambda environment variables is automatically constructed from Secrets Manager.

## Parameters

### Common Parameters (All Templates)

- `ProjectName`: Name of the project (default: rag-app)
- `Environment`: Environment name (dev, staging, prod)
- `AWSRegion`: AWS region (default: us-east-1)

### VPC Template Parameters

- `VpcCidr`: CIDR block for VPC (default: 10.0.0.0/16)

### RDS Template Parameters

- `DBName`: Database name (default: ragapp)
- `MasterUsername`: Master database username (default: admin)
- `MinACU`: Minimum Aurora Capacity Units (default: 0.5)
- `MaxACU`: Maximum Aurora Capacity Units (default: 4)

### Lambda Template Parameters

- `KBId`: **Required** - Bedrock Knowledge Base ID
- `ModelId`: Bedrock Model ID (default: anthropic.claude-3-5-sonnet-20240620-v1:0)
- `ECRImageUri`: **Required** - ECR image URI for Lambda container
- `DefaultTopK`: Default number of retrieval results (default: 6)
- `LambdaMemorySize`: Lambda memory size in MB (default: 512)
- `LambdaTimeout`: Lambda timeout in seconds (default: 300)

## Outputs

### VPC Stack Outputs

- `VpcId`: VPC ID
- `PublicSubnet1Id`, `PublicSubnet2Id`: Public subnet IDs
- `PrivateSubnet1Id`, `PrivateSubnet2Id`: Private subnet IDs

### RDS Stack Outputs

- `DBClusterEndpoint`: Aurora cluster endpoint hostname
- `DBClusterPort`: Database port (5432)
- `DBSecretArn`: ARN of Secrets Manager secret with DB credentials
- `DBName`: Database name
- `RDSSecurityGroupId`: Security group ID for RDS

### Lambda Stack Outputs

- `LambdaFunctionArn`: ARN of the Lambda function
- `LambdaFunctionName`: Name of the Lambda function
- `LambdaExecutionRoleArn`: ARN of the Lambda execution role

## IAM Permissions

The Lambda execution role is automatically configured with:

- **Bedrock**: `bedrock:InvokeModel`, `bedrock-agent-runtime:Retrieve`
- **Secrets Manager**: `secretsmanager:GetSecretValue` (scoped to DB secret)
- **VPC**: Automatic via `AWSLambdaVPCAccessExecutionRole` managed policy
- **CloudWatch Logs**: Log creation and writing

See `lambda-role-policy.json` for reference examples.

## Environment-Specific Considerations

### Development

- Minimal Aurora capacity (0.5-2 ACU)
- 14-day log retention
- Basic monitoring

### Staging

- Moderate Aurora capacity (2-4 ACU)
- 30-day log retention
- Enhanced monitoring

### Production

- Auto-scaling Aurora capacity (4-16 ACU)
- 30-day log retention
- Full monitoring and alerting
- Consider enabling automated backups and point-in-time recovery

## Validation

Validate templates before deployment:

```bash
aws cloudformation validate-template --template-body file://template.yaml
aws cloudformation validate-template --template-body file://vpc-infrastructure.yaml
aws cloudformation validate-template --template-body file://rds-postgres.yaml
aws cloudformation validate-template --template-body file://lambda-function.yaml
```

## Testing Lambda Invocation

After deployment, test the Lambda function:

```bash
aws lambda invoke \
  --function-name rag-app-dev-chat-app \
  --payload '{
    "conversation_id": "test-123",
    "question": "What does version 2023.4 say about password rotation?",
    "user_roles": ["Finance"],
    "ui_filters": {"document_type": "Policy", "version": "2023.4"},
    "user_id": "test@example.com"
  }' \
  response.json

cat response.json
```

## Troubleshooting

### Lambda Can't Connect to RDS

1. Verify Lambda is in the same VPC as RDS
2. Check security group rules (Lambda SG → RDS SG on port 5432)
3. Verify subnets are correct (private subnets)
4. Check CloudWatch logs for connection errors

### Bedrock Access Denied

1. Verify Lambda execution role has Bedrock permissions
2. Check Knowledge Base ID is correct
3. Verify region matches Knowledge Base region
4. Ensure model ID is correct and available in your region

### Database Authentication Errors

1. Verify Secrets Manager secret contains correct credentials
2. Check Lambda execution role has `secretsmanager:GetSecretValue` permission
3. Verify `PG_DSN` environment variable format
4. Check RDS security group allows Lambda security group

## Cleanup

To remove all resources, delete stacks in reverse order:

```bash
# If using master template
aws cloudformation delete-stack --stack-name rag-app-master-dev

# If deploying separately
aws cloudformation delete-stack --stack-name rag-app-lambda-dev
aws cloudformation delete-stack --stack-name rag-app-rds-dev
aws cloudformation delete-stack --stack-name rag-app-vpc-dev
```

**Note**: Ensure you have backups of your database before deletion.

## Important Notes

⚠️ **Production Deployment Checklist**:

- [ ] Review and customize all resource configurations
- [ ] Verify IAM permissions follow least-privilege principle
- [ ] Enable CloudWatch alarms for Lambda errors and RDS metrics
- [ ] Set up database backups and point-in-time recovery
- [ ] Configure VPC Flow Logs for network monitoring
- [ ] Review security group rules and scope down as needed
- [ ] Test failover scenarios for Aurora
- [ ] Document Knowledge Base metadata schema requirements
- [ ] Set up monitoring and alerting for production workloads
