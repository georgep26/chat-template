# Configuration Structure

This document describes the configuration files used by the project: application config, infrastructure definition (`infra/infra.yaml`), and secrets files. For a quick reference of secret names and when they are required, see [github_environment_secrets.md](github_environment_secrets.md).

---

## Application configuration

**Location:** Per-environment files under `config/<env>/app_config.yaml` (e.g. `config/dev/app_config.yaml`, `config/staging/app_config.yaml`, `config/prod/app_config.yaml`). A reference template with the same structure is at `config/app_config.template.yaml`.

**Purpose:** Application settings for the RAG chat pipeline:

- RAG/Bedrock: rewrite, clarify, split, retrieval, generation, and summarization model settings (region, model ID, temperature)
- Memory backend: `memory_backend_type` (e.g. `aurora_data_api` or `postgres`), plus DB connection settings (cluster ARN, credentials secret ARN, database name, table name)
- API: host, port, timeout
- Logging: formatters, handlers, log levels

**Placeholders:** The committed per-env files use `${PLACEHOLDER}` for values that must not be in git (e.g. `${KNOWLEDGE_BASE_ID}`, `${DB_CLUSTER_ARN}`, `${DB_CREDENTIALS_SECRET_ARN}`). At deploy time, [scripts/utils/hydrate_configs.sh](../scripts/utils/hydrate_configs.sh) replaces these with real values: locally from `infra/secrets/<env>_secrets.yaml` (section `config_secrets`), and in CI from GitHub Environment secrets.

**Lambda:** The deployed Lambda loads the final config from S3 (the S3 URI is defined in infra and passed into the deploy workflow). If the Lambda runs inside a VPC, the VPC must allow access to S3 (e.g. S3 Gateway VPC Endpoint). See [lambda_vpc_s3_setup.md](lambda_vpc_s3_setup.md) for details.

---

## infra/infra.yaml

**Location:** `infra/infra.yaml`

**Purpose:** Single source of truth for deployment. All setup and deploy scripts read from this file (via `scripts/utils/config_parser.sh`); account IDs, regions, and resource names are not hardcoded in scripts.

**Main sections:**

- **project:** Name, default region, management account ID (placeholder).
- **github:** Repo (placeholder), solo mode, branch protection rules for `main` and `development`.
- **environments:** Per-environment (dev, staging, prod) settings: account_id, account_name, region, deployer_profile, secrets_file path, email, org_role_name, cli_role_name, cli_profile_name, github_actions_deployer_role_arn, vpc_id, subnet_ids, and (after deploy) db_cluster_arn, db_credentials_secret_arn, knowledge_base_id. Values may be placeholders (e.g. `${ACCOUNT_ID}`, `${VPC_ID}`) until hydrated.
- **cost_tags / budgets / tags:** Cost allocation tags, budget limits, and required/optional tags for resources.
- **roles:** OIDC provider, deployer, evals, CLI, RAG Lambda execution, management-account policy and user. Each has `enabled`, template path, stack name, and (for some) policies.
- **resources:** Deploy order: network, s3_bucket, chat_db, rag_knowledge_base, rag_lambda_ecr, rag_lambda. Each has `enabled`, template(s), stack name(s), and config (bucket names, ACU, etc.).

**Placeholders:** Values like `${ACCOUNT_ID}`, `${VPC_ID}`, `${DEPLOYER_ROLE_ARN}` are filled by `scripts/utils/hydrate_configs.sh` from the environment’s secrets file (`config_secrets`) or from GitHub Environment secrets in CI.

---

## Secrets files

**Location:** `infra/secrets/<env>_secrets.yaml` (e.g. `infra/secrets/dev_secrets.yaml`). These files are **gitignored**. A reference template is [infra/secrets/template.secrets.yaml](../infra/secrets/template.secrets.yaml).

**Sections:**

- **database:** `master_username`, `master_password`. Used by deploy scripts and can be synced to GitHub as `MASTER_DB_USERNAME` / `MASTER_DB_PASSWORD`.
- **github_environment_secrets:** Map of name → value for GitHub Environment secrets used by the deploy and run-evals workflows (e.g. `AWS_DEPLOYER_ROLE_ARN`, `AWS_REGION`, `S3_APP_CONFIG_URI`, `MASTER_DB_USERNAME`, `MASTER_DB_PASSWORD`, `VPC_ID`, `SUBNET_IDS`, `SECURITY_GROUP_IDS`).
- **config_secrets:** Map of name → value used by `hydrate_configs.sh` to replace placeholders in `infra/infra.yaml` and `config/<env>/app_config.yaml` (e.g. `ACCOUNT_ID`, `DEPLOYER_ROLE_ARN`, `VPC_ID`, `KNOWLEDGE_BASE_ID`, `DB_CLUSTER_ARN`, `DB_CREDENTIALS_SECRET_ARN`).

**Syncing to GitHub:** Run `./scripts/setup/setup_github.sh` to push secrets from the local `infra/secrets/<env>_secrets.yaml` files into the corresponding GitHub Environment secrets. For the full list of secret names and when each is required, see [github_environment_secrets.md](github_environment_secrets.md).
