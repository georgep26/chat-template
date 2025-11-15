# RAG Chat Application

A production-ready RAG (Retrieval-Augmented Generation) chat application built with LangGraph, AWS Bedrock, and Postgres. This application provides an agentic RAG pipeline with conversation memory, query enhancement, and evaluation capabilities.

## Features

- **LangGraph Orchestration**: Agentic RAG pipeline with query rewriting, clarification, splitting, retrieval, and answer generation
- **AWS Bedrock Integration**: Uses Claude models via Bedrock Converse API and Amazon Knowledge Bases for retrieval
- **Conversation Memory**: Postgres-based chat history with automatic summarization for long conversations
- **Query Pipeline**: Intelligent query processing with rewrite, clarification, and multi-part query splitting
- **RAGAS Evaluation**: Offline evaluation framework using RAGAS metrics (answer accuracy, context precision/recall)
- **Lambda-Ready**: Standardized API interface for deployment as AWS Lambda function

## Environment Setup

The full environment setup process is handled by the `scripts/setup_env.sh` script. This script will:
- Detect your operating system (macOS, Linux, or Windows)
- Check if conda is already installed
- Install Miniconda if it's not installed (with OS-specific installation methods)
- Create the conda environment from `environment.yml` with all required dependencies

**Note**: The conda environment created from `environment.yml` is intended for **local development and testing**. It contains the same dependencies as the application's Lambda functions, allowing you to test the application locally before deploying to AWS. This ensures that your local development environment matches the production runtime environment.

### Quick Start

The easiest way to set up the environment is using the Makefile:

```bash
make dev-env
```

Alternatively, you can run the setup script directly:

```bash
bash scripts/setup_env.sh
```

**Important**: After running `make dev-env` or `bash scripts/setup_env.sh`, you **must manually activate** the conda environment before using the application:

```bash
conda activate chat-template-env
```

The setup script only creates or updates the conda environment; it does not activate it automatically. You need to activate it in your terminal session before running any application commands.

## Configuration

### Environment Variables

The application requires the following environment variables:

- `KB_ID`: AWS Bedrock Knowledge Base ID (or set in `config/app_config.yml`)
- `PG_CONN_INFO`: Postgres connection string (e.g., `postgresql://user:password@host:port/dbname`)

### Application Configuration

Edit `config/app_config.yml` to configure:

- **AWS/Bedrock Settings**: Region, model ID, temperature, knowledge base ID
- **Memory Settings**: Backend type (postgres/dynamo/vector), summarization threshold
- **Database Settings**: Postgres connection details

Example configuration:

```yaml
bedrock:
  region: "us-east-1"
  knowledge_base_id: "${KB_ID}"
  model:
    id: "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    temperature: 0.1

rag:
  memory:
    backend: "postgres"
    summarization_threshold: 20
```

### Postgres Database Setup

The application uses Postgres for chat history storage. Ensure Postgres is running and accessible:

1. Create a database:
   ```sql
   CREATE DATABASE rag_chat_db;
   ```

2. Set the `PG_CONN_INFO` environment variable:
   ```bash
   export PG_CONN_INFO="postgresql://user:password@localhost:5432/rag_chat_db"
   ```

The application will automatically create the required tables on first run.

## Directory Structure

```
python-template/
├── config/              # Configuration files for the application
│   └── app_config.yml   # Application configuration (includes app settings and logging)
├── docs/                # Documentation files
│   └── template-outline.md
├── infra/               # Infrastructure as Code (IaC) definitions
│   ├── cloudformation/  # AWS CloudFormation templates
│   │   ├── parameters.yaml
│   │   ├── template.yaml
│   │   └── README.md
│   ├── terraform/       # Terraform configuration files
│   │   ├── main.tf
│   │   ├── outputs.tf
│   │   └── variables.tf
│   └── README.md
├── notebooks/           # Jupyter notebooks for exploratory analysis
│   └── README.md
├── scripts/             # Utility and deployment scripts
│   ├── deploy/          # Deployment automation scripts
│   │   ├── cloudformation.sh
│   │   ├── deploy.sh
│   │   ├── terraform.sh
│   │   └── README.md
│   └── setup_env.sh     # Environment setup script
├── src/                 # Source code for the application
│   ├── main.py          # Lambda handler entry point
│   └── rag_app/         # RAG application package
│       ├── graph/       # LangGraph state, nodes, and graph definition
│       ├── api/         # API models and chat service
│       └── memory/      # Chat history storage abstractions
├── evals/               # RAGAS evaluation pipeline
├── data/                # Evaluation data (CSV files)
├── tests/               # Unit and integration tests
│   ├── test_graph_smoke.py
│   ├── test_api_lambda.py
│   ├── test_ragas_pipeline.py
│   └── test_memory_store.py
├── CHANGELOG.md         # Project changelog for version tracking
├── environment.yml      # Conda environment definition
├── Makefile             # Make commands for common tasks
├── pyproject.toml       # Python project configuration and dependencies
└── README.md            # This file
```

### Folder Descriptions

- **config/**: Contains the application configuration file (`app_config.yml`) which includes application settings, database configuration, API settings, AWS configuration, and logging configuration. This YAML file allows you to configure the application behavior without modifying code.

- **docs/**: Documentation files for the project, including guides, API documentation, and project outlines.

- **infra/**: Infrastructure as Code (IaC) definitions for deploying the application. Contains both CloudFormation and Terraform configurations for cloud infrastructure provisioning.

- **notebooks/**: Jupyter notebooks for data exploration, prototyping, and analysis. Useful for interactive development and sharing results.

- **scripts/**: Utility scripts for automation, deployment, and environment setup. The `deploy/` subdirectory contains scripts for deploying infrastructure and applications.

- **src/**: Main source code directory. Contains the core application logic and modules.
  - `rag_app/graph/`: LangGraph components (state, nodes, graph construction)
  - `rag_app/api/`: API models (ChatRequest, ChatResponse) and chat service
  - `rag_app/memory/`: Chat history storage (Postgres implementation, factory, summarization)

- **evals/**: RAGAS evaluation pipeline scripts and utilities

- **tests/**: Test files for unit testing, integration testing, and validation of the application code.

- **data/**: Evaluation datasets (CSV files with questions and reference answers)

## Code Quality Tools (Optional)

This template includes a comprehensive pre-commit configuration (`.pre-commit-config.yaml`) with various code quality tools that are **disabled by default**. You can enable any combination of these tools based on your project needs.

### Available Tools

**Basic File Quality:**
- Remove trailing whitespace
- Ensure files end with newlines
- Validate YAML syntax
- Prevent large file commits
- Check for merge conflicts
- Remove debug statements

**Python Code Quality:**
- **Black**: Auto-format Python code
- **isort**: Sort and organize imports
- **flake8**: Lint for style and errors
- **mypy**: Static type checking
- **bandit**: Security vulnerability scanning
- **pydocstyle**: Docstring formatting

**Other File Types:**
- **Prettier**: Format YAML files
- **hadolint**: Lint Dockerfiles
- **Terraform hooks**: Format and validate Terraform files
- **nbQA**: Apply formatting to Jupyter notebooks

### How to Enable

1. Install pre-commit:
   ```bash
   pip install pre-commit
   ```

2. Edit `.pre-commit-config.yaml` and uncomment the sections you want to use

3. Install the hooks:
   ```bash
   pre-commit install
   ```

4. Run manually (optional):
   ```bash
   pre-commit run --all-files
   ```

Once enabled, these tools will automatically run before each commit, ensuring consistent code quality across your project.

## Usage

### Running Tests

```bash
make test
```

### Running Evaluation

Create `data/eval_questions.csv` with columns `question` and `reference_answer`, then run:

```bash
make eval
```

Results will be saved to `data/eval_results.csv`.

### Lambda Deployment

The `src/main.py` file contains the Lambda handler. Deploy using your preferred method (SAM, Terraform, CloudFormation).

Example Lambda event:

```json
{
  "body": {
    "conversation_id": "conv-123",
    "user_id": "user-456",
    "message": "What is the cancellation policy?",
    "metadata": {}
  }
}
```

## Architecture

The RAG pipeline follows this flow:

1. **Query Rewrite**: Enhances user query for better retrieval
2. **Clarification**: Asks clarifying questions if query is underspecified
3. **Query Splitting**: Splits multi-part queries into subqueries
4. **Retrieval**: Retrieves relevant documents from Bedrock Knowledge Base
5. **Answer Generation**: Generates answer using retrieved context and conversation history

Conversation memory is automatically loaded before processing and persisted after completion. Long conversations are automatically summarized to maintain context window efficiency.
