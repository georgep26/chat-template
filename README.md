# RAG Chat Application

A production-ready Retrieval-Augmented Generation (RAG) application built with AWS Bedrock, LangChain, and PostgreSQL. This application provides conversational AI capabilities with RBAC filtering, metadata injection, and persistent conversation history.

## Features

- **RBAC Filtering**: Role-based access control for document retrieval using Bedrock Knowledge Base metadata filters
- **Metadata Injection**: Rich context formatting with document metadata (title, type, version, location)
- **Conversation History**: Persistent multi-turn conversations using PostgreSQL via SQLChatMessageHistory
- **Bedrock Integration**: Leverages AWS Bedrock for LLM inference and Knowledge Base for retrieval
- **Lambda-based**: Serverless architecture with container-based Lambda functions
- **CLI Support**: Test locally with command-line interface

## Architecture

- **Lambda Function**: Containerized Lambda handler (`chat_app_lambda.py`)
- **Bedrock Knowledge Base**: Vector search with metadata filtering
- **Aurora Serverless v2**: PostgreSQL database for conversation history
- **LangChain**: Orchestration framework for RAG pipeline

## Repository Structure

```
rag-app/
├── src/
│   ├── chat_app_lambda.py      # Lambda handler + LangChain orchestration + CLI
│   ├── prompt_config.py        # Prompt templates and Bedrock model config
│   ├── retrieval.py            # RBAC filtering and document retrieval
│   ├── history_store.py        # Conversation history (SQLChatMessageHistory)
│   ├── db.py                   # Optional direct Postgres logging (stubbed)
│   └── main.py                 # Legacy entry point (optional)
├── config/
│   ├── app_config.yml          # Application configuration (RAG settings)
│   └── logging.yml             # Logging configuration
├── infra/
│   └── cloudformation/
│       ├── template.yaml       # Master template (nested stacks)
│       ├── vpc-infrastructure.yaml
│       ├── rds-postgres.yaml
│       ├── lambda-function.yaml
│       └── lambda-role-policy.json
├── tests/
│   └── test_chat_handler.py    # Unit tests
├── requirements.txt            # Python dependencies
└── Dockerfile                  # Lambda container image
```

## Configuration

Configuration is managed through `config/app_config.yml` with environment variable overrides:

```yaml
rag:
  kb_id: "${KB_ID}"              # Bedrock Knowledge Base ID
  model_id: "${MODEL_ID}"         # Bedrock Model ID
  pg_dsn: "${PG_DSN}"             # PostgreSQL connection string
  default_top_k: "${DEFAULT_TOP_K}" # Default retrieval results (default: 6)
```

Environment variables take precedence over YAML values:
- `AWS_REGION`: AWS region (default: us-east-1)
- `KB_ID`: Bedrock Knowledge Base ID
- `MODEL_ID`: Bedrock model ID (e.g., `anthropic.claude-3-5-sonnet-20240620-v1:0`)
- `PG_DSN`: PostgreSQL connection string (format: `postgresql://user:pass@host:port/dbname`)
- `DEFAULT_TOP_K`: Default number of retrieval results (default: 6)

## Local Development

### Prerequisites

- Python 3.11+
- AWS CLI configured
- Access to Bedrock Knowledge Base
- PostgreSQL database (local or remote)

### Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure environment variables or update `config/app_config.yml`:
   ```bash
   export AWS_REGION=us-east-1
   export KB_ID=your-kb-id
   export MODEL_ID=anthropic.claude-3-5-sonnet-20240620-v1:0
   export PG_DSN=postgresql://user:password@localhost:5432/ragapp
   export DEFAULT_TOP_K=6
   ```

3. Initialize database table for conversation history:
   ```sql
   CREATE TABLE messages (
       id SERIAL PRIMARY KEY,
       conversation_id VARCHAR NOT NULL,
       role VARCHAR NOT NULL,
       content TEXT NOT NULL,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   );
   ```

### Running Locally

Use the CLI interface:

```bash
python -m src.chat_app_lambda \
  --conversation-id "conv-123" \
  --question "What does version 2023.4 say about password rotation?" \
  --user-roles "Finance,HR" \
  --ui-filters '{"document_type": "Policy", "version": "2023.4"}' \
  --user-id "alice@example.com"
```

## Lambda Deployment

### Build Docker Image

1. Build and tag the Docker image:
   ```bash
   docker build -t rag-app:latest .
   ```

2. Create ECR repository and push:
   ```bash
   aws ecr create-repository --repository-name rag-app
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
   docker tag rag-app:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest
   ```

### Deploy Infrastructure

Deploy CloudFormation stacks in order:

1. **VPC Infrastructure**:
   ```bash
   aws cloudformation deploy \
     --template-file infra/cloudformation/vpc-infrastructure.yaml \
     --stack-name rag-app-vpc-dev \
     --parameter-overrides ProjectName=rag-app Environment=dev
   ```

2. **RDS PostgreSQL** (requires VPC outputs):
   ```bash
   aws cloudformation deploy \
     --template-file infra/cloudformation/rds-postgres.yaml \
     --stack-name rag-app-rds-dev \
     --parameter-overrides \
       ProjectName=rag-app \
       Environment=dev \
       VpcId=<vpc-id> \
       PrivateSubnet1Id=<subnet-1-id> \
       PrivateSubnet2Id=<subnet-2-id> \
     --capabilities CAPABILITY_NAMED_IAM
   ```

3. **Lambda Function** (requires VPC and RDS outputs):
   ```bash
   aws cloudformation deploy \
     --template-file infra/cloudformation/lambda-function.yaml \
     --stack-name rag-app-lambda-dev \
     --parameter-overrides \
       ProjectName=rag-app \
       Environment=dev \
       AWSRegion=us-east-1 \
       VpcId=<vpc-id> \
       PrivateSubnet1Id=<subnet-1-id> \
       PrivateSubnet2Id=<subnet-2-id> \
       RDSSecurityGroupId=<rds-sg-id> \
       DBClusterEndpoint=<cluster-endpoint> \
       DBName=ragapp \
       DBSecretArn=<secret-arn> \
       KBId=<kb-id> \
       ModelId=anthropic.claude-3-5-sonnet-20240620-v1:0 \
       ECRImageUri=<account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest \
     --capabilities CAPABILITY_NAMED_IAM
   ```

**OR** deploy using the master template (nested stacks):

```bash
aws cloudformation deploy \
  --template-file infra/cloudformation/template.yaml \
  --stack-name rag-app-master-dev \
  --parameter-overrides \
    ProjectName=rag-app \
    Environment=dev \
    KBId=<kb-id> \
    ECRImageUri=<account-id>.dkr.ecr.us-east-1.amazonaws.com/rag-app:latest \
  --capabilities CAPABILITY_IAM
```

### Bedrock Knowledge Base Setup

The Knowledge Base must be created manually (or via separate CloudFormation/CDK):

1. Create Bedrock Knowledge Base in AWS Console
2. Configure data source (S3 bucket with documents)
3. Set up vector embeddings (Titan embeddings)
4. Configure vector store (OpenSearch Serverless)
5. Note the Knowledge Base ID and provide it as `KBId` parameter

### Database Initialization

After RDS deployment, initialize the messages table:

```sql
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    conversation_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
```

## Lambda Invocation

### Direct Invoke

```bash
aws lambda invoke \
  --function-name rag-app-dev-chat-app \
  --payload '{
    "conversation_id": "conv-123",
    "question": "What does version 2023.4 say about password rotation?",
    "user_roles": ["Finance"],
    "ui_filters": {"document_type": "Policy", "version": "2023.4"},
    "user_id": "alice@example.com"
  }' \
  response.json
```

### Response Format

```json
{
  "statusCode": 200,
  "headers": {"content-type": "application/json"},
  "body": "{\"answer\": \"...\", \"citations\": [...]}"
}
```

Body contains:
- `answer`: LLM-generated answer text
- `citations`: Array of citation objects with `title`, `type`, `version`, `s3_uri`, `page`, `snippet`

## RBAC Filtering

The application supports role-based access control via Bedrock Knowledge Base metadata filters:

- **User Roles**: Documents must have `allowed_groups` metadata containing at least one of the user's roles
- **UI Filters**: Additional filters like `document_type` and `version` can be applied

Filter structure:
```python
{
  "andAll": [
    {
      "orAll": [
        {"contains": {"key": "allowed_groups", "value": "Finance"}},
        {"contains": {"key": "allowed_groups", "value": "HR"}}
      ]
    },
    {"equals": {"key": "document_type", "value": "Policy"}},
    {"equals": {"key": "document_version_number", "value": "2023.4"}}
  ]
}
```

## Testing

Run unit tests:

```bash
pytest tests/test_chat_handler.py -v
```

## Dependencies

- `langchain`: Core LangChain framework
- `langchain-aws`: AWS Bedrock integration for LangChain
- `langchain-community`: Community integrations (SQLChatMessageHistory)
- `boto3`: AWS SDK for Python
- `psycopg2-binary`: PostgreSQL adapter
- `pyyaml`: YAML configuration parsing

## Troubleshooting

### Database Connection Issues

- Verify RDS security group allows inbound traffic from Lambda security group on port 5432
- Check Lambda VPC configuration (subnets and security groups)
- Verify Secrets Manager secret contains correct credentials

### Bedrock Permissions

- Ensure Lambda execution role has `bedrock:InvokeModel` and `bedrock-agent-runtime:Retrieve` permissions
- Verify Knowledge Base ID is correct
- Check region matches Knowledge Base region

### Conversation History Not Persisting

- Verify PostgreSQL table exists with correct schema
- Check `PG_DSN` environment variable format
- Review CloudWatch logs for SQL errors

## License

See LICENSE file for details.
