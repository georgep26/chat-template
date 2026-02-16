# Deployment Process — Full Review

This document describes in plain English every step of the deployment process: what each script does, what logic it contains, and how the steps fit together. It is based on the current documentation and all scripts under `scripts/`.

---

## 1. Overview

Deployment is split into two phases:

- **Phase 1: Setup (one-time)** — Bootstrap AWS accounts, CI/CD identity (OIDC, IAM roles), and GitHub (environments, branch protection, secrets). Run once per environment (or once per org when using multiple accounts).
- **Phase 2: Deploy (regular)** — Deploy or update infrastructure and application resources (network, S3, database, Knowledge Base, ECR, Lambda, configs). Run whenever you change infra or application code.

The single source of truth for configuration is **`infra/infra.yaml`**. It defines project name, GitHub settings (repo, solo mode, branch protection), environments (account IDs, regions, profiles, secrets files, VPC/subnets, deployer role ARNs, DB/KB IDs), cost tags, budgets, tags, **roles** (OIDC, deployer, evals, CLI, RAG Lambda execution, management-account policy and user), and **resources** (network, S3, chat_db, rag_knowledge_base, rag_lambda_ecr, rag_lambda). Resources are deployed in the order they appear; teardown runs in reverse order.

---

## 2. Phase 1: Setup (One-Time)

Setup is orchestrated by **`scripts/setup/setup_all.sh`**. It runs a fixed sequence of steps; each step can be skipped via a flag or (where applicable) by disabling the corresponding role in `infra.yaml`.

### 2.0. Local development environment 

**Script:** `scripts/setup/setup_local_dev_env.sh`

- **Purpose:** Prepare the machine for running other scripts and app code.
- **Logic:**
  - Detects OS (macOS, Linux, Windows).
  - Checks for `conda`; if missing and running in a TTY, offers to install Miniconda (Homebrew on macOS, or direct download).
  - Reads `environment.yml` at project root and gets the conda env name.
  - Creates the conda env if it does not exist (`conda env create`), or updates it if it does (`conda env update`).
  - Creates an activation script under the env’s `etc/conda/activate.d/` that sets `PYTHONPATH` to the project root when the env is activated.
- **When to run:** Once per developer machine; not required for CI.

---

### 2.1. Setup orchestrator

**Script:** `scripts/setup/setup_all.sh`

- **Purpose:** Run all one-time setup steps in order for a given environment.
- **Arguments:** `<environment>` (dev | staging | prod) plus options: `-y` (skip confirmations), `--config <path>`, `--skip-accounts`, `--skip-oidc`, `--skip-deployer-roles`, `--skip-github`, `--skip-evals-roles`.
- **Logic:**
  - Validates environment and loads `infra/infra.yaml` (or `--config` path). Validates that the chosen environment exists and has required fields.
  - For each step, decides status: **run**, **skip-flag** (CLI skip), or **skip-disabled** (role disabled in infra). Steps 1 (accounts) and 4 (GitHub) have no infra “enabled” check; steps 2 (OIDC), 3 (deployer), 5 (evals) respect `roles.<name>.enabled` in infra.
  - Prints a summary of what will run or be skipped and (unless `-y`) asks for confirmation.
  - Runs in order:
    1. **Setup AWS accounts** — `setup_accounts.sh` (with `-y` if auto-confirm).
    2. **Setup OIDC provider** — `setup_oidc_provider.sh <env> -y`.
    3. **Setup deployer roles** — `deploy_deployer_github_action_role.sh <env> deploy -y`.
    4. **Setup GitHub environments** — `setup_github.sh <env> -y`.
    5. **Setup evals roles** — `deploy_evals_github_action_role.sh <env> deploy -y`.
  - If any step fails, setup stops and reports failed steps. On success, prints a completion box and suggests next step: run `deploy_all.sh <env>`.

---

### 2.2. Setup AWS accounts

**Script:** `scripts/setup/setup_accounts.sh`

- **Purpose:** Create the three member accounts (dev, staging, prod) under the management account via AWS Organizations, optionally create budgets and alerts, deploy the management-account IAM user and policy, deploy CLI roles in each member account, and write account metadata into `infra/infra.yaml` and optionally a JSON file.
- **Precedence:** Script defaults &lt; values from `infra/infra.yaml` &lt; environment variables &lt; CLI options.
- **Logic:**
  - **Read defaults from infra:** Project name, `environments.*.email`, `org_role_name`, `budgets.budget_email`, `budgets.dev_max_budget` / `staging_max_budget` / `prod_max_budget`. Apply env vars and CLI overrides on top.
  - **Apply email defaults:** If dev/staging/prod emails are still empty, use `{PROJECT_NAME}+dev@example.com` (and similarly for staging/prod).
  - **Confirm** (unless `-y` or non-interactive).
  - **Preflight:** Ensure current identity is in the management account (`aws organizations describe-organization`).
  - **Create or reuse accounts:** For each of dev, staging, prod:
    - Account name = `{PROJECT_NAME}-dev` (and `-staging`, `-prod`). If an account with that name already exists, use its ID; otherwise call `aws organizations create-account` with the chosen email and `--role-name` (default `OrganizationAccountAccessRole`), then poll `describe-create-account-status` until SUCCEEDED or FAILED/timeout.
  - **Optional: Management account IAM:** Unless `--skip-iam-user`:
    - Deploy CloudFormation stack for **assume-org-access policy** (`infra/policies/assume_org_access_role_policy.yaml`) with parameters: project name, dev/staging/prod account IDs, org access role name. Create or update stack.
    - Deploy CloudFormation stack for **management admin user** (`infra/roles/management_account_admin_user.yaml`) that attaches that policy, so the user can assume `OrganizationAccountAccessRole` in each member account. Optionally prompt for console password and call `create-login-profile` or `update-login-profile`.
  - **Optional: CLI roles in member accounts:** Unless `--skip-cli-roles`:
    - For each env (dev, staging, prod), **assume** `OrganizationAccountAccessRole` in that account, then deploy the **CLI role** stack (`infra/roles/admin_cli_role.yaml`) with create-stack or update-stack. Stack name from infra: `{project}-cli-role-{env}`.
    - **Update local AWS config:** Append to `~/.aws/config` and `~/.aws/credentials` (only if sections don’t already exist): a management profile (if IAM user was created) and, for each env, a profile `{project}-{env}-cli` with `credential_process` pointing at `scripts/utils/assume_role_for_cli.sh <env> cli <source_profile>`.
  - **Write infra.yaml:** Set `project.management_account_id` and, for each environment, `account_id`, `account_name`, `email`, `org_role_name`, `cli_role_name`, `cli_profile_name`.
  - **Optional JSON:** If `--out-json` is set, write project name, management account ID, dev/staging/prod account IDs and emails, and org access role name to that file.
  - **Optional budgets:** If `--budget-alert-email` (or `budgets.budget_email` from infra) is set, create a cost budget in the **management** account for each linked account (filter by LinkedAccount), with 80% and 100% notifications to that email.

---

### 2.3. Setup OIDC provider

**Script:** `scripts/setup/setup_oidc_provider.sh`

- **Purpose:** Create the GitHub OIDC identity provider in the target AWS account so GitHub Actions can assume IAM roles via OIDC (no long-lived keys).
- **Arguments:** `<environment>` plus optional `create` | `delete` | `status`, and `-y`.
- **Logic:**
  - Loads infra, validates environment, and uses the **CLI profile** for that environment (`environments.<env>.cli_profile_name`) so the provider is created in the correct account.
  - **create (default):** If a provider with URL `https://token.actions.githubusercontent.com` already exists (list open-id-connect-providers), skip. Otherwise call `iam create-open-id-connect-provider` with URL, client-id-list `sts.amazonaws.com`, and the standard GitHub OIDC thumbprint. Print next-step hint (run deployer role script).
  - **delete:** Find the same provider, confirm, then `iam delete-open-id-connect-provider`.
  - **status:** List the provider ARN and optionally show URL/client IDs/thumbprints.

---

### 2.4. Deploy deployer GitHub Action role

**Script:** `scripts/deploy/deploy_deployer_github_action_role.sh`

- **Purpose:** Deploy the IAM role that the **deploy** workflow (e.g. `.github/workflows/deploy.yml`) uses to run deployments via OIDC. One stack per environment.
- **Arguments:** `<environment>`, action `deploy` | `update` | `delete` | `validate` | `status`, and options: `--github-org`, `--github-repo`, `--oidc-provider-arn`, `--region`, `--project-name`, `-y`, `--write-to-infra`.
- **Logic:**
  - Load infra and resolve GitHub org/repo: from `github.github_repo` in infra (if not a placeholder), or from `resolve_github_org_repo` (git remote origin). For deploy/update, require org and repo.
  - Resolve OIDC provider: use `--oidc-provider-arn` if given; otherwise list OpenID Connect providers in the account and use the one ending with `token.actions.githubusercontent.com`. Fail if not found (hint: run setup_oidc_provider.sh first).
  - Use **CLI profile** for the environment to run CloudFormation in that account.
  - **deploy/update:** Validate template `infra/roles/deployer_role.yaml`, then create or update the stack named from infra (`roles.deployer.stack_name`, e.g. `{project}-deployer-role-{env}`) with parameters for GitHub org/repo and OIDC provider ARN. Wait for stack to complete. Get the role ARN from stack outputs.
  - **Write role ARN:** By default, write the role ARN into the environment’s **secrets file** (`infra/secrets/<env>_secrets.yaml`): set `github_environment_secrets.AWS_DEPLOYER_ROLE_ARN` and `config_secrets.DEPLOYER_ROLE_ARN`. If `--write-to-infra` is set, also (or instead) write to `infra/infra.yaml` under `environments.<env>.github_actions_deployer_role_arn`.
  - **Ensure deployer profile in AWS config:** If the deployer profile (e.g. `chat-template-dev-deployer`) is not already in `~/.aws/config`, append a `[profile ...]` block with `credential_process` pointing at `assume_role_for_cli.sh <env> deployer <source_profile>` so local runs can assume the deployer role.
  - **delete:** Delete the deployer role stack. **status:** Describe stack and show status/outputs.

---

### 2.5. Setup GitHub (environments, branch protection, secrets)

**Script:** `scripts/setup/setup_github.sh`

- **Purpose:** Configure the GitHub repo for CI/CD: set branch protection for **main** and **development** from `infra/infra.yaml`, ensure GitHub **environments** (dev, staging, prod) exist, and **deploy secrets** from `infra/secrets/<env>_secrets.yaml` into each environment’s GitHub Environment secrets.
- **Repo resolution:** `--repo` override &gt; `github.github_repo` in infra (if not a placeholder) &gt; git remote `origin` (parsed to `owner/repo`).
- **Solo mode:** `--solo` override &gt; `github.solo_mode` in infra. Solo mode sets `required_approving_review_count` to 0 for main; otherwise 1.

#### 2.5.1. Branch protection (for each branch in infra)

- **Source of branch list and rules:** `infra/infra.yaml` under `github.branch_protection`. The script configures protection for **main** and **development** only; other keys in `branch_protection` are not used by this script.
- **Main branch:**
  - If `--skip-branch-protection` is set, skip all branch protection.
  - Check if protection already exists (`gh api repos/{repo}/branches/main/protection`). If it does, warn and (unless `-y`) ask whether to update.
  - Build JSON for the protection rules:
    - If `yq` (and preferably `jq`) are available and `github.branch_protection.main` exists in infra, use that block. For **main**, the script **injects** `required_approving_review_count` from solo mode: 0 if solo, 1 otherwise (via `jq`). The API expects either required status check *contexts* or *checks*, not both; the infra for main uses `contexts: ["Run Tests"]` and no `checks`.
    - If infra has no block or yq/jq aren’t available, use a built-in default: strict status checks with context `Run Tests`, enforce admins, required PR reviews with the chosen review count, no force push, no deletion, conversation resolution required.
  - Call `gh api repos/{repo}/branches/main/protection --method PUT --input -` with that JSON.
- **Development branch:**
  - If the **development** branch does not exist in the repo (`gh api repos/{repo}/branches/development`), skip with a warning (create the branch first).
  - Otherwise, build protection JSON:
    - If `github.branch_protection.development` exists in infra, use it (e.g. strict status checks with empty contexts, enforce admins, 0 required reviews, allow force pushes, no deletions). No review-count injection for development.
    - Else use built-in default: deletion protection only (no required checks or reviews), no force push.
  - Unless `-y`, prompt to apply or update. Then `gh api repos/{repo}/branches/development/protection --method PUT --input -`.

So: **branch protection for each branch** (main and development) is driven by the corresponding key under `github.branch_protection` in `infra/infra.yaml`; for main, `required_approving_review_count` is overridden from solo mode.

#### 2.5.2. GitHub environments

- Unless `--skip-environments`, ensure GitHub environments exist. **Which environments:** If positional args (e.g. `dev staging`) were passed, only those; otherwise dev, staging, prod.
- For each environment name, call `gh api -X PUT repos/{repo}/environments/{env}` to create the environment if it doesn’t exist (idempotent).

#### 2.5.3. Deploying secrets for each environment

- Unless `--skip-secrets`, the script deploys secrets from the repo’s **secrets files** into **GitHub Environment secrets** for each target environment.
- **Which environments get secrets:** Resolved in order:
  1. If positional args were passed (e.g. `setup_github.sh dev staging`), use only those envs that have a secrets file (e.g. `infra/secrets/dev_secrets.yaml`). If a selected env has no file, error.
  2. Else if `--deploy-secrets env1 env2` was used, use that list.
  3. Else use **all** envs (dev, staging, prod) that exist in infra and for which `infra/secrets/<env>_secrets.yaml` exists. If none exist, error.
- **Per-environment secret content** (for each env in the resolved list):
  - **1) `github_environment_secrets` map:** If the env’s secrets file has a top-level key `github_environment_secrets` with a map, each key is a GitHub Environment secret name and each value is the secret value. The script iterates over keys and calls `gh secret set <name> --env <env> --repo <repo>` (or without `--repo` if using default repo) for each. These are the names expected by the deploy workflow (e.g. `AWS_DEPLOYER_ROLE_ARN`, `AWS_REGION`, `S3_APP_CONFIG_URI`, `MASTER_DB_USERNAME`, `MASTER_DB_PASSWORD`, `VPC_ID`, `SUBNET_IDS`, `SECURITY_GROUP_IDS`). See `docs/github_environment_secrets.md`.
  - **2) Database mapping:** If the secrets file has `database.master_username` or `database.master_password` and the **same** name is not already present under `github_environment_secrets`, the script maps them to `MASTER_DB_USERNAME` and `MASTER_DB_PASSWORD` and sets those GitHub Environment secrets.
  - **3) `config_secrets` map:** If the env’s secrets file has `config_secrets` with a map, each key-value pair is set as a GitHub Environment secret with that name. These are used by the **hydrate** step in CI (e.g. `ACCOUNT_ID`, `DEPLOYER_ROLE_ARN`, `VPC_ID`, `DB_CLUSTER_ARN`, `KNOWLEDGE_BASE_ID`, etc.).
- **Dry run:** With `--dry-run`, the script only prints what would be set (e.g. “[env] Would set NAME (value length N)”) and does not call `gh`.
- **Confirmation:** Unless `-y` or dry run, it may call `confirm_deployment` before deploying secrets.

So: **for each environment** (dev, staging, prod, or the subset you passed), the script reads `infra/secrets/<env>_secrets.yaml` and syncs `github_environment_secrets`, DB→MASTER_DB_* mapping, and `config_secrets` into that environment’s GitHub Environment secrets.

---

### 2.6. Deploy evals GitHub Action role

**Script:** `scripts/deploy/deploy_evals_github_action_role.sh`

- **Purpose:** Deploy the IAM role and its policies used by the **evals** workflow (e.g. run-evals) to call Bedrock, Lambda, S3, and Secrets Manager via OIDC.
- **Arguments:** `<environment>`, action `deploy` | `update` | `delete` | `validate` | `status`, and options: `--github-org`, `--github-repo`, `--oidc-provider-arn`, `--github-source-branch`, `--github-target-branch`, `--region`, `--project-name`, `--include-lambda-policy`, `--knowledge-base-id`, `-y`.
- **Logic:**
  - Load infra; resolve GitHub org/repo and OIDC provider (same pattern as deployer script). Use **CLI profile** for the environment.
  - Deploy **policy stacks first** (order matters for role template references): Secrets Manager policy, S3 policy, Bedrock policy, and optionally Lambda invoke policy. Stack names are derived from project and env (e.g. `{project}-{env}-evals-secrets-manager-policy`). Templates live under `infra/policies/` (evals_*).
  - Deploy the **evals role** stack from `infra/roles/evals_github_action_role.yaml` with trust policy for the GitHub OIDC provider and subject constraints (repo, branch/environment). Attach the policy ARNs from the policy stacks. Stack name from infra: `roles.evals.stack_name`.
  - On success, the role ARN is typically added to GitHub Environment secrets as `AWS_EVALS_ROLE_ARN` (manually or via docs). **delete** removes the role stack then the policy stacks. **status** describes the role stack (and optionally policy stacks).

---

## 3. Phase 2: Deploy (Regular)

Deploy is orchestrated by **`scripts/deploy/deploy_all.sh`**. It runs resource deployments in the order defined in infra (network → s3_bucket → chat_db → rag_knowledge_base → rag_lambda_ecr → rag_lambda), then cost tags, then config hydration, sync app config, and deploy configs to S3.

### 3.1. Deploy orchestrator

**Script:** `scripts/deploy/deploy_all.sh`

- **Purpose:** Deploy all enabled resources for an environment in order, then run config sync and S3 config upload.
- **Arguments:** `<environment>`, skip flags (`--skip-network`, `--skip-s3`, `--skip-db`, `--skip-kb`, `--skip-ecr`, `--skip-lambda`, `--skip-cost-tags`), convenience flags (`--only-infra`, `--only-app`), `-y`, `--config`, and pass-through overrides (`--region`, `--vpc-id`, `--subnet-ids`, `--security-group-ids`, `--master-username`, `--master-password`, `--public-ip`, `--s3-app-config-uri`, `--local-app-config-path`, `--image-tag`).
- **Logic:**
  - Validates environment and loads infra. Builds a deployment plan: for each resource in order (network, s3_bucket, chat_db, rag_knowledge_base, rag_lambda_ecr, rag_lambda), status is **deploy** (enabled and not skipped), **skip-flag**, or **skip-disabled**. Infrastructure (network, S3, DB) uses CLI admin role; application (KB, ECR, Lambda) uses deployer role (see infra README).
  - Draws a box with the plan and (unless `-y`) asks for confirmation.
  - For each resource with status **deploy**, builds the appropriate args (env, deploy, -y, plus any pass-through overrides), then runs the corresponding script: `deploy_network.sh`, `deploy_s3_bucket.sh`, `deploy_chat_template_db.sh`, `deploy_knowledge_base.sh`, `deploy_ecr_repo.sh`, `deploy_rag_lambda.sh`. Stops on first failure.
  - If not `--skip-cost-tags`, runs `deploy_cost_analysis_tags.sh activate`.
  - Runs **hydrate_configs.sh** for the environment (injects secrets into infra.yaml and app_config placeholders; see below).
  - Runs **sync_app_config.sh --env <env>** to copy knowledge_base_id, db_cluster_arn, db_credentials_secret_arn from infra into `config/<env>/app_config.yaml`.
  - Runs **deploy_configs.sh <env>** to upload `config/<env>/` to the environment’s S3 bucket under `config/`.
  - Prints a completion summary.

---

### 3.2. Deploy network (VPC)

**Script:** `scripts/deploy/deploy_network.sh`

- **Purpose:** Ensure the environment has a VPC and subnets (and optional default security group) for DB and Lambda. Either deploy a CloudFormation network stack or use existing/default VPC and write IDs back to infra.
- **Logic:**
  - Reads `resources.network.use_defaults` from infra. If **true**:
    - If `environments.<env>.vpc_id` is set, verify that VPC exists and has at least two subnets (and optionally default security group). If not set, **discover** VPC: try default VPC first, then first available VPC in the region. Verify subnets (and SG). Write `vpc_id` and comma-separated `subnet_ids` back to `infra/infra.yaml` under `environments.<env>`. No CloudFormation stack is created.
  - If **use_defaults** is false, deploy the network CloudFormation stack from `infra/resources/vpc_template.yaml` with parameters (project, environment, region, VpcCidr, EnableNatGateway). Create or update stack, wait, then show outputs (VpcId, PrivateSubnetIds, LambdaSecurityGroupId).
  - **delete:** If use_defaults, no-op. Otherwise delete the stack.

---

### 3.3. Deploy S3 bucket

**Script:** `scripts/deploy/deploy_s3_bucket.sh`

- **Purpose:** Deploy the S3 bucket used for knowledge base documents and app config (stack from `infra/resources/s3_bucket_template.yaml`). Uses CLI profile locally; in CI, deployer role is used if infrastructure is not skipped.
- **Logic:** Load infra, get stack name and template, build parameters (bucket name, versioning, lifecycle from `resources.s3_bucket.config`). Create or update stack; on delete, empty versioned bucket (all versions and delete markers) then delete stack.

---

### 3.4. Deploy database (Aurora Serverless)

**Script:** `scripts/deploy/deploy_chat_template_db.sh`

- **Purpose:** Deploy Aurora Serverless v2 PostgreSQL and a Secrets Manager secret for DB credentials. Used for chat history and (via RDS Data API) embeddings table for the Knowledge Base.
- **Logic:**
  - VPC/subnets: from `--vpc-id` / `--subnet-ids`, env vars `VPC_ID`/`SUBNET_IDS`, or infra `environments.<env>.vpc_id` / `subnet_ids`; else try to get from VPC stack outputs. Master username/password from infra, secrets file, or CLI.
  - Deploy **secret stack** first (`infra/resources/db_secret_template.yaml`) to create/update the Secrets Manager secret. Then deploy **DB stack** (`infra/resources/light_db_template.yaml`) with cluster, instance(s), security group, RDS Data API, and reference to the secret. Optionally run SQL (e.g. embeddings table) via RDS Data API.
  - On success, script (or post-deploy flow) can write `db_cluster_arn` and `db_credentials_secret_arn` to infra or secrets so later steps (KB, Lambda, app_config) can use them.
  - **delete:** Delete DB stack then secret stack (order may depend on template dependencies).

---

### 3.5. Deploy Knowledge Base

**Script:** `scripts/deploy/deploy_knowledge_base.sh`

- **Purpose:** Deploy the Bedrock Knowledge Base that uses the PostgreSQL embeddings table and S3 bucket. Uses **deployer profile**.
- **Logic:** Load DB stack name, S3 bucket name, table name, embedding model from infra (or overrides). Ensure embeddings table exists (e.g. run SQL via RDS Data API). Deploy CloudFormation stack from `infra/resources/knowledge_base_template.yaml` with data source (S3, optional PostgreSQL). After create/update, sync the data source to start ingestion. Optionally write `knowledge_base_id` back to infra. **delete:** Delete stack (template may set DataDeletionPolicy RETAIN to avoid failing on vector store cleanup).

---

### 3.6. Deploy ECR repository

**Script:** `scripts/deploy/deploy_ecr_repo.sh`

- **Purpose:** Deploy the ECR repository for Lambda container images (stack from `infra/resources/ecr_repo_template.yaml`). Uses deployer profile in CI.
- **Logic:** Create or update stack with repository name and lifecycle policy (e.g. max 5 images). **delete:** Delete stack (repository must be empty or lifecycle handles cleanup).

---

### 3.7. Deploy RAG Lambda

**Script:** `scripts/deploy/deploy_rag_lambda.sh`

- **Purpose:** Build the Docker image for the RAG Lambda, push to ECR, and deploy/update the Lambda function (and its execution role if defined in infra). Uses deployer profile.
- **Logic:**
  - **Build/push:** Unless `--skip-build`, build Docker image, tag, authenticate to ECR, push. Image tag from `--image-tag` (default `latest`).
  - **Deploy stack:** Template `infra/resources/lambda_template.yaml`. Parameters: function name, ECR repo/image, memory, timeout, app config S3 URI, DB secret ARN, Knowledge Base ID, execution role, VPC/subnets/security groups. S3 app config URI can come from `--s3_app_config_uri`, `--local_app_config_path` (upload to S3 then use that URI), or infra. Resolve DB secret ARN and Knowledge Base ID from stack outputs or infra if not passed. Lambda execution role from infra role stack `rag_lambda_execution`.
  - **delete:** Delete Lambda stack.

---

### 3.8. Cost allocation tags

**Script:** `scripts/deploy/deploy_cost_analysis_tags.sh`

- **Purpose:** Activate cost allocation tags in AWS Cost Explorer so costs can be filtered by Name, Environment, Project (default tags from infra).
- **Logic:** Actions `activate` (default), `list`, `status`. Uses `ce:UpdateCostAllocationTagsStatus` to set the chosen tags to Active. List/status use Cost Explorer APIs. No environment argument; tags are account-wide.

---

### 3.9. Hydrate configs

**Script:** `scripts/utils/hydrate_configs.sh`

- **Purpose:** Replace `${VAR}` placeholders in `infra/infra.yaml` and `config/<env>/app_config.yaml` with real values so CI (or local) has concrete config without committing secrets.
- **Logic:**
  - **CI (`GITHUB_ACTIONS=true`):** Values come from **environment variables** (set from GitHub Environment secrets in the workflow). No secrets file read.
  - **Local:** Values come from `infra/secrets/<env>_secrets.yaml` section `config_secrets`; each key is exported as an env var.
  - **infra.yaml:** For the given env, set (if env vars present) e.g. `project.management_account_id`, `github.github_repo`, `budgets.budget_email`, and `environments.<env>.*` (account_id, email, github_actions_deployer_role_arn, vpc_id, subnet_ids, db_cluster_arn, db_credentials_secret_arn, knowledge_base_id).
  - **app_config.yaml:** Replace `${KNOWLEDGE_BASE_ID}`, `${DB_CLUSTER_ARN}`, `${DB_CREDENTIALS_SECRET_ARN}` with the same env vars (sed). Skip if `HYDRATE_CONFIGS=false` or no placeholders found.

---

### 3.10. Sync app config

**Script:** `scripts/deploy/sync_app_config.sh`

- **Purpose:** Copy knowledge_base_id, db_cluster_arn, and db_credentials_secret_arn from `infra/infra.yaml` into `config/<env>/app_config.yaml` so the app and Lambda use the deployed resources.
- **Logic:** For each env (or `--env` only), read from infra `environments.<env>.knowledge_base_id`, `db_cluster_arn`, `db_credentials_secret_arn`. Write into `config/<env>/app_config.yaml` at `rag_chat.retrieval.knowledge_base_id` and `rag_chat.chat_history_store.db_cluster_arn` / `db_credentials_secret_arn`. Skip env if `config/<env>/app_config.yaml` doesn’t exist. `--dry-run` only prints what would change.

---

### 3.11. Deploy configs to S3

**Script:** `scripts/deploy/deploy_configs.sh`

- **Purpose:** Upload the contents of `config/<env>/` to the environment’s S3 bucket under the `config/` prefix (same bucket as app config).
- **Logic:** Resolve bucket name from the S3 stack output `BucketName` or from infra `resources.s3_bucket.config.bucket_name`. Use CLI profile or current credentials. Run `aws s3 sync config/<env>/ s3://<bucket>/config/ --delete`. Ensures the Lambda and other consumers can read the latest app_config and other files from S3.

---

## 4. Teardown and Destroy

### 4.1. Teardown (full environment cleanup)

**Script:** `scripts/deploy/teardown.sh`

- **Purpose:** Remove all deployed stacks for an environment (and optionally management-account stacks) in **reverse** order of definition in infra, including roles. Handles the case where the CLI role is deleted last by temporarily assuming the OrganizationAccountAccessRole so the CLI profile is not used after its role is gone.
- **Logic:**
  - Load infra. Determine current account; if it equals `project.management_account_id`, management-account stacks (e.g. assume-org-access policy, management-admin user) can be deleted when appropriate.
  - **Reverse order:** Delete resource stacks (rag_lambda, rag_lambda_ecr, rag_knowledge_base, chat_db, s3_bucket, network), then role stacks (evals, deployer, OIDC provider, CLI role). For **CLI role** deletion: assume `OrganizationAccountAccessRole` in the env account using a source profile (e.g. `{project}-management-admin` or `TEARDOWN_ORG_SOURCE_PROFILE`), then delete the CLI role stack so we’re not using the role we’re deleting.
  - If a stack exists (by name from infra), it is deleted even if the resource/role is disabled in infra. **--dry-run** only lists what would be deleted. **-y** skips confirmations.

### 4.2. Destroy all (resources only)

**Script:** `scripts/deploy/destroy_all.sh`

- **Purpose:** Tear down only the **resource** stacks (Lambda, KB, DB, S3, Network) in reverse deploy order, without tearing down IAM roles or OIDC. Useful for wiping an env and redeploying.
- **Logic:** Calls each deploy script’s delete action in order: Lambda → Knowledge Base → Database → S3 → Network. Supports `--skip-*` and `--force` / `-y`.

---

## 5. Utilities (shared by scripts)

- **config_parser.sh:** Loads `infra/infra.yaml` with `yq`. Exposes: project name, default region, GitHub org/repo, environment (account_id, region, profile, CLI role name, CLI profile name, secrets file path, vpc_id, subnet_ids), resource enabled/template/stack_name/config, role enabled/template/stack_name/policies, tags, and helpers like `get_environment_secrets_file`, `environment_exists`, `validate_config`. Used by almost all deploy/setup scripts.
- **common.sh:** Logging (print_info, print_step, print_warning, print_error, print_complete, print_header), validation (validate_environment, validate_action), AWS helpers (get_aws_account_id, stack_exists, get_stack_status, get_stack_output), project root and infra path, template validation, wait_for_stack.
- **deploy_summary.sh:** Box-drawing (draw_box_top, draw_box_row, etc.), print_resource_summary, print_full_deploy_summary, confirm_deployment, confirm_destructive_action. Used for user prompts and pretty output.
- **github_repo.sh:** get_repository_from_git (parse origin URL to owner/repo), get_github_repo_from_infra, resolve_github_repo, resolve_github_org_repo (sets RESOLVED_GITHUB_ORG, RESOLVED_GITHUB_REPO). Used by setup_github.sh and the two role-deploy scripts.
- **assume_role_for_cli.sh:** **Credential process** for AWS CLI profiles. Arguments: environment, role_type (cli | deployer), source_profile. Reads infra and (if account_id is a placeholder) config_secrets.ACCOUNT_ID from secrets file. Assumes OrganizationAccountAccessRole in the env account, then assumes the CLI or deployer role; outputs JSON credentials for `credential_process`. Used by profiles written by setup_accounts.sh and by deploy_deployer_github_action_role.sh when ensuring the deployer profile.

---

## 6. Summary table

| Phase   | Step              | Script                                  | What it does in one line |
|---------|-------------------|------------------------------------------|---------------------------|
| Setup   | 0                 | setup_local_dev_env.sh                   | Conda env + PYTHONPATH    |
| Setup   | Orchestrator      | setup_all.sh                             | Runs setup steps 1–5      |
| Setup   | 1                 | setup_accounts.sh                        | Org accounts, budgets, CLI roles, infra write |
| Setup   | 2                 | setup_oidc_provider.sh                    | GitHub OIDC provider in AWS |
| Setup   | 3                 | deploy_deployer_github_action_role.sh    | Deployer IAM role for deploy workflow |
| Setup   | 4                 | setup_github.sh                          | Branch protection (main + development), environments, env secrets |
| Setup   | 5                 | deploy_evals_github_action_role.sh       | Evals IAM role + policies |
| Deploy  | Orchestrator      | deploy_all.sh                            | Network → S3 → DB → KB → ECR → Lambda, then cost tags, hydrate, sync app config, deploy configs |
| Deploy  | Network           | deploy_network.sh                        | Use existing/default VPC or deploy VPC stack; write vpc_id/subnet_ids to infra |
| Deploy  | S3                | deploy_s3_bucket.sh                      | Deploy S3 bucket stack    |
| Deploy  | DB                | deploy_chat_template_db.sh               | Aurora + Secrets Manager secret |
| Deploy  | KB                | deploy_knowledge_base.sh                 | Bedrock KB + data source sync |
| Deploy  | ECR               | deploy_ecr_repo.sh                       | ECR repo stack            |
| Deploy  | Lambda            | deploy_rag_lambda.sh                     | Docker build/push + Lambda stack |
| Deploy  | Cost tags         | deploy_cost_analysis_tags.sh             | Activate cost allocation tags |
| Deploy  | Hydrate           | hydrate_configs.sh                       | Replace placeholders in infra + app_config (CI: env vars; local: secrets file) |
| Deploy  | Sync app config   | sync_app_config.sh                       | Copy KB/DB ARNs from infra to config/<env>/app_config.yaml |
| Deploy  | Deploy configs    | deploy_configs.sh                        | Sync config/<env>/ to S3  |
| Teardown| Full              | teardown.sh                              | Delete all stacks (resources + roles) in reverse order; CLI role last via org assume |
| Teardown| Resources only    | destroy_all.sh                           | Delete Lambda, KB, DB, S3, Network only |

This document reflects the behavior of the scripts and `infra/infra.yaml` as of the review. For required GitHub Environment secret names and usage in the deploy workflow, see `docs/github_environment_secrets.md` and `.github/workflows/deploy.yml`.
