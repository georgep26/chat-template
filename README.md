# RAG Chat Application

A RAG (Retrieval-Augmented Generation) chat application built with LangGraph, AWS Bedrock, and Postgres. This application provides an agentic RAG pipeline with conversation memory, query enhancement, and evaluation capabilities. The quickest way to get started is to use the /intro command with an AI agent like Cursor, GitHub Copilot, or Claude Code. Alternatively, you can follow the setup instructions below.

## Features

- **LangGraph Orchestration**: Agentic RAG pipeline with query rewriting, clarification, splitting, retrieval, and answer generation
- **AWS Bedrock Integration**: Can use any model via Bedrock Converse API and Amazon Knowledge Bases for retrieval
- **Conversation Memory**: Postgres-based chat history with automatic summarization for long conversations
- **Query Pipeline**: Intelligent query processing with rewrite, clarification, and multi-part query splitting
- **Evaluation Framework**: Evaluation framework with support for standard RAGAS metrics and custom metrics.
- **Lambda Deployment**: Standardized API interface for deployment as AWS Lambda function
- **Standardized Deployment Process**: Standardized deployment process for all environments (dev, staging, prod) with evaluation framework integration.

## Development process

Development uses a branch model: **main** (stable/production), **development** (integration), and feature or hotfix branches. Work flows via feature branch → PR to **development** → QA → PR to **main**; hotfixes go from a hotfix branch directly to **main**, then **main** is merged back to **development**. See [docs/development_process.md](docs/development_process.md) for the full workflow, GitHub Project pairing, PR review expectations, and hotfix process. Optional code quality hooks are in `.pre-commit-config.yaml` (disabled by default).

## Setup

Full details are in [docs/deployment_process.md](docs/deployment_process.md). Summary:

> **Important:** This repo is set up for a **public** repo: sensitive values live in `infra/secrets/` (gitignored) and placeholders in `infra/infra.yaml` and `config/<env>/app_config.yaml` are filled at deploy time by [scripts/utils/hydrate_configs.sh](scripts/utils/hydrate_configs.sh) (CI uses GitHub Environment secrets; local runs use the secrets files). For a **private** repo you can set `HYDRATE_CONFIGS=false` and commit real values, or keep the same flow with secrets only in GitHub or local files.

### 4.1 Local development environment setup

Run the local dev setup script so you have a conda env and PYTHONPATH set to the project root. The script detects OS (macOS, Linux, Windows), installs Miniconda if needed (e.g. via Homebrew on macOS or Chocolatey on Windows), creates the conda environment from `environment.yml`, and sets `PYTHONPATH` on activation. See [docs/deployment_process.md](docs/deployment_process.md) (Phase 1 step 0) for more detail.

**Quick start:**

The easiest way to set up the environment is using the Makefile:

```bash
make dev-env
```

Alternatively, you can run the setup script directly:

```bash
bash scripts/setup/setup_local_dev_env.sh
```

After setup, **activate** the conda environment: `conda activate chat-template-env`. Configure your IDE to use the conda interpreter (e.g. in VSCode: Command Palette → "Python: Select Interpreter" → choose the env). See [docs/deployment_process.md](docs/deployment_process.md) for more.

### 4.2 Environments setup in GitHub and AWS

One-time setup for an environment (dev, staging, prod): AWS accounts, OIDC provider, deployer role, GitHub environments and secrets, evals role. The recommended way is to run:

```bash
./scripts/setup/setup_all.sh <environment>
```

(e.g. `./scripts/setup/setup_all.sh dev`). You can skip specific steps with flags (see [docs/deployment_process.md](docs/deployment_process.md)). If you prefer or need to do steps manually (e.g. scripts fail or you don’t use them), use [docs/github_environment_secrets.md](docs/github_environment_secrets.md) for setting GitHub environment secrets and [docs/oidc_github_identity_provider_setup.md](docs/oidc_github_identity_provider_setup.md) for creating the GitHub OIDC identity provider in AWS. Branch protection can be applied with `make branch-protection` or `bash scripts/setup/setup_branch_protection.sh` (requires GitHub CLI `gh`).

### 4.3 Initial deployment

Deploy infrastructure and application (network, S3, DB, Knowledge Base, ECR, Lambda, config sync):

```bash
./scripts/deploy/deploy_all.sh <environment>
```

See [docs/deployment_process.md](docs/deployment_process.md) for Phase 2 and options (e.g. `--skip-network`, `--only-app`).

Application and infrastructure configuration (app config, `infra/infra.yaml`, secrets) is described in [docs/config_structure.md](docs/config_structure.md). Per-env app config lives in `config/<env>/app_config.yaml`; see [config/app_config.template.yaml](config/app_config.template.yaml) for structure and options. IAM roles (deployer, evals, Lambda execution) are deployed by the setup and deploy scripts; for the evals role and run-evals workflow, see [evals/README.md](evals/README.md).

## Run evals and tests

```bash
make test    # pytest
make eval    # RAGAS evaluation pipeline
```

Create `data/eval_questions.csv` with columns `question` and `reference_answer` for evals; results go to `data/eval_results.csv`. See [evals/README.md](evals/README.md) for details. The Lambda handler is in `src/rag_lambda/main.py`; deploy via `deploy_all.sh` or the deploy scripts. Example Lambda event body: `{"conversation_id": "conv-123", "user_id": "user-456", "message": "What is the cancellation policy?", "metadata": {}}`.

## Key design elements

- **Public repo strategy** ([.cursor/rules/public_repo_strategy.mdc](.cursor/rules/public_repo_strategy.mdc)): Templated configs with `${PLACEHOLDER}`; sensitive data in `infra/secrets/` and GitHub Environment secrets; [scripts/utils/hydrate_configs.sh](scripts/utils/hydrate_configs.sh) injects values at deploy time. Private forks can set `HYDRATE_CONFIGS=false` and commit real values.
- **AWS deployment strategy** ([.cursor/rules/aws_strategy.mdc](.cursor/rules/aws_strategy.mdc)): All config from `infra/infra.yaml`; naming, tags, resource order, OIDC for GitHub Actions. See [docs/aws_naming_convention.md](docs/aws_naming_convention.md) for naming rules.
- **Inexpensive deployment**: [docs/aurora_data_api_migration.md](docs/aurora_data_api_migration.md) describes using the Aurora Data API backend so Lambda can run without VPC (no NAT Gateway). [docs/lambda_vpc_s3_setup.md](docs/lambda_vpc_s3_setup.md) covers Lambda in VPC and S3 config loading (e.g. S3 Gateway VPC Endpoint).

## Directory structure

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
├── config/              # Application configuration per environment
│   ├── app_config.template.yaml  # Reference template
│   ├── dev/
│   │   └── app_config.yaml
│   ├── staging/
│   │   └── app_config.yaml
│   └── prod/
│       └── app_config.yaml
├── docs/                # Documentation
│   ├── ai-instructions.md
│   ├── aurora_data_api_migration.md
│   ├── aws_naming_convention.md
│   ├── code_standards.md
│   ├── config_structure.md
│   ├── deployment_process.md
│   ├── development_process.md
│   ├── github_environment_secrets.md
│   ├── lambda_vpc_s3_setup.md
│   ├── oidc_github_identity_provider_setup.md
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
│   ├── setup/           # Setup scripts (local dev, branch protection, GitHub, etc.)
│   │   ├── setup_local_dev_env.sh
│   │   ├── setup_branch_protection.sh
│   │   └── ...
│   └── utils/           # Shared script utilities (config_parser, hydrate_configs, etc.)
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

### Folder descriptions

- **config/**: Per-environment application config (`config/<env>/app_config.yaml`). Contains RAG/Bedrock, memory backend, API, and logging settings. See [docs/config_structure.md](docs/config_structure.md) and [config/app_config.template.yaml](config/app_config.template.yaml).

- **docs/**: Project documentation: deployment and development process, config structure, GitHub secrets, OIDC setup, AWS naming, Aurora Data API, Lambda/VPC/S3, code standards.

- **infra/**: Infrastructure as Code (IaC) definitions for deploying the application.
  - `resources/`: AWS CloudFormation templates for deploying infrastructure components (VPC, Lambda, database, knowledge base, etc.)
  - `policies/`: IAM policy templates for various AWS services
  - `roles/`: IAM role templates for Lambda execution and GitHub Actions

- **notebooks/**: Jupyter notebooks for data exploration, prototyping, and analysis. Useful for interactive development and sharing results.

- **scripts/**: Automation, deployment, and setup scripts.
  - `deploy/`: Deploy infrastructure and application (network, S3, DB, Knowledge Base, ECR, Lambda, configs)
  - `setup/`: Local dev env, branch protection, GitHub environments and secrets, OIDC, accounts
  - `utils/`: Shared helpers (config_parser.sh, hydrate_configs.sh, common.sh, etc.)

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

## RAG Architecture

The RAG pipeline follows this flow:

1. **Query Rewrite**: Enhances user query for better retrieval
2. **Clarification**: Asks clarifying questions if query is underspecified
3. **Query Splitting**: Splits multi-part queries into subqueries
4. **Retrieval**: Retrieves relevant documents from Bedrock Knowledge Base
5. **Answer Generation**: Generates answer using retrieved context and conversation history

Conversation memory is automatically loaded before processing and persisted after completion. Long conversations are automatically summarized to maintain context window efficiency.
