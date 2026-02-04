# RAG Chat Application

A RAG (Retrieval-Augmented Generation) chat application built with LangGraph, AWS Bedrock, and Postgres. This application provides an agentic RAG pipeline with conversation memory, query enhancement, and evaluation capabilities.

## Features

- **LangGraph Orchestration**: Agentic RAG pipeline with query rewriting, clarification, splitting, retrieval, and answer generation
- **AWS Bedrock Integration**: Uses Claude models via Bedrock Converse API and Amazon Knowledge Bases for retrieval
- **Conversation Memory**: Postgres-based chat history with automatic summarization for long conversations
- **Query Pipeline**: Intelligent query processing with rewrite, clarification, and multi-part query splitting
- **Evaluation Framework**: Evaluation framework with support for standard RAGAS metrics and custom metrics.
- **Lambda Deployment**: Standardized API interface for deployment as AWS Lambda function

## Environment Setup

The full environment setup process is handled by the `scripts/setup_env.sh` script. This script will:
- Detect your operating system (macOS, Linux, or Windows)
- Check if conda is already installed
- Install Miniconda if it's not installed (with OS-specific installation methods)
- Create the conda environment from `environment.yml` with all required dependencies
- Automatically set `PYTHONPATH` to the project root directory when the conda environment is activated

**Note**: The conda environment created from `environment.yml` is intended for **local development and testing**. It contains the same dependencies as the application's Lambda functions, allowing you to test the application locally before deploying to AWS. This ensures that your local development environment matches the production runtime environment.

**Note**: The `setup_env.sh` script automatically configures `PYTHONPATH` to point to the project root directory. This is done via an activation script that runs whenever you activate the conda environment, ensuring that Python can find the project modules without additional configuration.

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

### IDE Setup

To use the conda environment in your IDE, you'll need to configure it to use the Python interpreter from the conda environment.

**VSCode Setup:**

1. Open the Command Palette (`Cmd+Shift+P` on macOS, `Ctrl+Shift+P` on Windows/Linux)
2. Type "Python: Select Interpreter" and select it
3. Choose the interpreter from the conda environment. It should be located at:
   - **macOS/Linux**: `~/miniconda3/envs/chat-template-env/bin/python` (or `~/anaconda3/envs/chat-template-env/bin/python` if using Anaconda)
   - **Windows**: `%USERPROFILE%\miniconda3\envs\chat-template-env\python.exe` (or `%USERPROFILE%\anaconda3\envs\chat-template-env\python.exe` if using Anaconda)
4. Alternatively, you can create a `.vscode/settings.json` file in the project root with:
   ```json
   {
     "python.defaultInterpreterPath": "${env:CONDA_PREFIX}/bin/python"
   }
   ```
   (Note: This requires activating the environment before opening VSCode, or manually setting the path)

**Note**: For other IDEs (PyCharm, IntelliJ, etc.), the setup process may differ. Please refer to your IDE's documentation for configuring Python interpreters with conda environments.

## Branch Protection Setup

**Important**: If you are copying or forking this repository, you need to set up branch protection rules to enforce the development workflow. Pull requests to the `main` branch should come from the `development` branch for standard feature releases. However, pull requests from hotfix branches are also allowed when an immediate change needs to be made to production.

### Quick Setup

The easiest way to set up branch protection is using the Makefile:

```bash
make branch-protection
```

Alternatively, you can run the setup script directly:

```bash
bash scripts/setup_branch_protection.sh
```

### What It Does

The branch protection setup script will:

1. **Configure branch protection rules** for the `main` branch:
   - Requires pull requests before merging
   - Requires 1 approval (2 reviewers recommended for standard releases)
   - Prevents direct pushes to main
   - Requires branches to be up to date before merging
   - Requires conversation resolution

**Note**: The standard process is to submit PRs from the `development` branch, but hotfix branches are allowed for immediate production fixes. See `docs/development_process.md` for details on the hotfix process.

### Prerequisites

- GitHub CLI (`gh`) must be installed and authenticated
  - Install: `brew install gh` (macOS), `apt install gh` (Linux), or `choco install gh` (Windows with [Chocolatey](https://chocolatey.org/install))
  - Authenticate: `gh auth login`
- Repository must be initialized with a git remote pointing to GitHub

### Manual Setup

If you prefer to set up branch protection manually:

1. Go to your repository on GitHub
2. Navigate to **Settings** → **Branches**
3. Add a branch protection rule for `main` with the settings mentioned above

## Configuration

### Application Configuration

Edit `config/app_config.yml` to configure:

- **AWS/Bedrock Settings**: Region, model ID, temperature, knowledge base ID
- **Memory Settings**: Backend type (postgres/aurora_data_api/dynamo/vector), summarization threshold
- **Database Settings**: Database connection configuration (varies by backend type)

Example configuration:

```yaml
rag_chat:
  retrieval:
    region: "us-east-1"
    knowledge_base_id: "your-knowledge-base-id-here"
    number_of_results: 10
  generation:
    model:
      id: "amazon.nova-micro-v1:0"
      temperature: 0.0
      region: "us-east-1"
  chat_history_store:
    memory_backend_type: "aurora_data_api"  # Options: postgres, aurora_data_api, dynamo, vector, local_sqlite
    # For postgres backend (requires VPC):
    # db_connection_secret_name: "chat-template-db-connection-dev"
    # table_name: "chat_history"
    # For aurora_data_api backend (no VPC required):
    db_cluster_arn: "arn:aws:rds:us-east-1:account-id:cluster:cluster-name"
    db_credentials_secret_arn: "arn:aws:secretsmanager:us-east-1:account-id:secret:secret-name"
    database_name: "chat_template_db"
    table_name: "chat_history"
  summarization:
    summarization_threshold: 20
    model:
      id: "amazon.nova-micro-v1:0"
      temperature: 0.0
      region: "us-east-1"
```

**Database Configuration Notes**:
- For `postgres` backend: Configure `db_connection_secret_name` in `app_config.yml`. The application will fetch credentials from AWS Secrets Manager.
- For `aurora_data_api` backend: Configure `db_cluster_arn`, `db_credentials_secret_arn`, and `database_name` in `app_config.yml`. This backend does not require VPC configuration.
- The application will automatically create the required tables on first run.

## AWS Roles

This project uses IAM roles to provide secure access to AWS resources. Each role is designed for a specific purpose and follows the principle of least privilege.

### Evals GitHub Action Role

The **Evals GitHub Action Role** enables GitHub Actions workflows to perform evaluations on AWS resources using OIDC (OpenID Connect) authentication. This role allows CI/CD pipelines to run evaluation tests without requiring long-lived AWS access keys.

**Key Features:**
- **OIDC Authentication**: Uses GitHub's OIDC provider for secure, temporary credential exchange
- **Environment-Scoped Permissions**: The role is scoped to a specific environment (dev, staging, or prod)
- **Automatic Policy Management**: Deploys required IAM policies (Secrets Manager, S3, Bedrock, and optionally Lambda) automatically

**How It Works:**
1. The role is deployed per environment using the deployment script
2. GitHub Actions workflows authenticate using OIDC tokens
3. AWS validates the token and grants temporary credentials
4. The role's permissions are limited to resources in the specified environment

**Important Security Note:**
When you deploy this role to an environment, GitHub Actions will **only** have permissions to access resources in that specific environment. For example:
- Deploying to `staging` allows access only to staging resources (staging Lambda functions, staging S3 buckets, etc.)
- Deploying to `dev` allows access only to dev resources
- Deploying to `prod` allows access only to production resources

This ensures that evaluation workflows can only interact with the environment they are intended to test, providing better security and isolation.

**Deployment:**
```bash
# Deploy to development environment
./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org your-org \
  --github-repo chat-template

# Deploy to staging environment
./scripts/deploy/deploy_evals_github_action_role.sh staging deploy \
  --aws-account-id 123456789012 \
  --github-org your-org \
  --github-repo chat-template

# Deploy with Lambda policy (for lambda mode evaluations)
./scripts/deploy/deploy_evals_github_action_role.sh dev deploy \
  --aws-account-id 123456789012 \
  --github-org your-org \
  --github-repo chat-template \
  --include-lambda-policy
```

**After Deployment:**
1. The script will output the role ARN
2. Add this ARN to your GitHub repository secrets as `AWS_ROLE_ARN`
3. The GitHub Actions workflow (`.github/workflows/run-evals.yml`) will automatically use this role for authentication

**Permissions Included:**
- Secrets Manager: Access to retrieve database credentials and other secrets
- S3: Upload evaluation results to S3 buckets
- Bedrock: Invoke Bedrock models for evaluation judge models
- Lambda (optional): Invoke Lambda functions when running evaluations in lambda mode

For more details, see the [evaluation framework documentation](evals/README.md).

## Directory Structure

```
chat-template/
├── AGENT.md             # Agent configuration and instructions
├── CHANGELOG.md         # Project changelog for version tracking
├── CONTRIBUTING.md      # Contribution guidelines
├── LICENSE              # Project license
├── Makefile             # Make commands for common tasks
├── README.md            # This file
├── environment.yml      # Conda environment definition
├── pyproject.toml       # Python project configuration and dependencies
├── requirements.txt     # Python dependencies
├── config/              # Configuration files for the application
│   ├── app_config.template.yaml  # Template configuration file
│   ├── app_config.yaml  # Application configuration (gitignored, includes app settings and logging)
│   └── README.md        # Configuration documentation
├── docs/                # Documentation files
│   ├── ai-instructions.md
│   ├── aurora_data_api_migration.md
│   ├── code_standards.md
│   ├── development_process.md
│   ├── template_outline.md
│   └── working_notes.md
├── infra/               # Infrastructure as Code (IaC) definitions
│   ├── resources/  # AWS CloudFormation templates
│   │   ├── db_secret_template.yaml
│   │   ├── knowledge_base_template.yaml
│   │   ├── lambda_template.yaml
│   │   ├── light_db_template.yaml
│   │   ├── s3_bucket_template.yaml
│   │   ├── vpc_template.yaml
│   │   ├── NETWORK_COST_ESTIMATE.md
│   │   └── README.md
│   ├── policies/        # IAM policy templates
│   │   ├── evals_bedrock_policy.yaml
│   │   ├── evals_lambda_policy.yaml
│   │   ├── evals_s3_policy.yaml
│   │   ├── evals_secrets_manager_policy.yaml
│   │   └── README.md
│   ├── roles/           # IAM role templates
│   │   ├── evals_github_action_role.yaml
│   │   ├── lambda_execution_role.yaml
│   │   └── README.md
│   └── README.md
├── notebooks/           # Jupyter notebooks for exploratory analysis
│   └── README.md
├── scripts/             # Utility and deployment scripts
│   ├── deploy/          # Deployment automation scripts
│   │   ├── deploy_chat_template_db.sh
│   │   ├── deploy_evals_github_action_role.sh
│   │   ├── deploy_knowledge_base.sh
│   │   ├── deploy_network.sh
│   │   ├── deploy_rag_lambda.sh
│   │   ├── deploy_s3_bucket.sh
│   │   ├── NETWORK_DEPLOYMENT.md
│   │   └── README.md
│   └── setup_env.sh     # Environment setup script
├── src/                 # Source code for the application
│   ├── rag_lambda/      # RAG Lambda function package
│   │   ├── api/         # API models (ChatRequest, ChatResponse)
│   │   ├── graph/       # LangGraph state, nodes, and graph definition
│   │   ├── memory/      # Chat history storage abstractions
│   │   ├── main.py      # Lambda handler entry point
│   │   ├── Dockerfile   # Docker image for Lambda deployment
│   │   └── requirements.txt  # Lambda-specific dependencies
│   └── utils/           # Shared utility modules
│       ├── aws_utils.py
│       ├── config.py
│       ├── llm_factory.py
│       └── logger.py
├── evals/               # RAGAS evaluation pipeline
│   ├── eval_outputs/    # Evaluation results and reports
│   ├── validation_data/ # Validation datasets
│   ├── evals_pipeline.py
│   ├── metrics_base.py
│   ├── metrics_custom.py
│   ├── metrics_ragas.py
│   ├── evals_config.yaml
│   └── README.md
├── data/                # Evaluation data and reference documents
│   ├── irc_chapters_toc/  # IRC chapter text files
│   └── *.csv            # Evaluation question datasets
├── sql/                 # SQL scripts for database setup
│   ├── eda.sql
│   └── embeddings_table_setup.sql
└── tests/               # Unit and integration tests
    ├── test_api_lambda.py
    ├── test_chat_handler.py
    ├── test_graph_smoke.py
    ├── test_main.py
    ├── test_memory_store.py
    └── test_ragas_pipeline.py
```

### Folder Descriptions

- **config/**: Contains the application configuration files. `app_config.yaml` (gitignored) includes application settings, database configuration, API settings, AWS configuration, and logging configuration. `app_config.template.yaml` provides a template for creating your own configuration.

- **docs/**: Documentation files for the project, including guides, API documentation, development processes, code standards, and project outlines.

- **infra/**: Infrastructure as Code (IaC) definitions for deploying the application.
  - `resources/`: AWS CloudFormation templates for deploying infrastructure components (VPC, Lambda, database, knowledge base, etc.)
  - `policies/`: IAM policy templates for various AWS services
  - `roles/`: IAM role templates for Lambda execution and GitHub Actions

- **notebooks/**: Jupyter notebooks for data exploration, prototyping, and analysis. Useful for interactive development and sharing results.

- **scripts/**: Utility scripts for automation, deployment, and environment setup.
  - `deploy/`: Deployment automation scripts for infrastructure components
  - `setup_env.sh`: Environment setup script that creates the conda environment

- **src/**: Main source code directory. Contains the core application logic and modules.
  - `rag_lambda/`: RAG Lambda function package containing the main application code
    - `graph/`: LangGraph components (state, nodes, graph construction)
    - `api/`: API models (ChatRequest, ChatResponse) and chat service
    - `memory/`: Chat history storage (Postgres, Aurora Data API implementations, factory, summarization)
    - `main.py`: Lambda handler entry point
    - `Dockerfile`: Docker image for Lambda deployment
  - `utils/`: Shared utility modules for AWS operations, configuration, logging, and LLM factory

- **evals/**: RAGAS evaluation pipeline scripts and utilities. Contains evaluation metrics, pipeline scripts, and evaluation results.

- **sql/**: SQL scripts for database setup, including embeddings table configuration.

- **tests/**: Test files for unit testing, integration testing, and validation of the application code.

- **data/**: Evaluation data and reference documents, including IRC chapter text files and CSV datasets with questions and reference answers.

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

The `src/rag_lambda/main.py` file contains the Lambda handler. The RAG application code is organized in the `src/rag_lambda/` folder with its own `Dockerfile` and `requirements.txt`. Deploy using your preferred method (SAM, Terraform, CloudFormation).

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
