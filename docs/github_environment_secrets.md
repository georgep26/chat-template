# GitHub environment secrets for deploy workflow

The deploy workflow (`.github/workflows/deploy.yml`) reads sensitive and environment-specific values from **environment secrets**. Use this doc when you need to **set or inspect these secrets manually** (e.g. when not using `setup_github.sh` or when the script fails). Configure these per environment (dev, staging, prod) in GitHub:

**Settings → Environments → &lt;environment&gt; → Environment secrets**

## Secret names to set

| Secret name | Description | Required when |
|-------------|-------------|----------------|
| `S3_APP_CONFIG_URI` | S3 URI for app config (e.g. `s3://bucket/key`) | Deploying Lambda (`skip_lambda` is false) |
| `LOCAL_APP_CONFIG_PATH` | Local app config file path (uploaded to S3) | Optional |
| `MASTER_DB_USERNAME` | Database master username | Optional; defaults to `postgres` in deploy script if unset |
| `MASTER_DB_PASSWORD` | Database master password | When creating or updating DB (e.g. first deploy or password change) |
| `AWS_REGION` | AWS region for deployment | Optional; defaults to `us-east-1` if unset |
| `VPC_ID` | VPC ID | When `skip_network` is true and `skip_db` is false |
| `SUBNET_IDS` | Comma-separated subnet IDs | When `skip_network` is true and `skip_db` is false |
| `SECURITY_GROUP_IDS` | Comma-separated security group IDs (for Lambda) | When `skip_network` is true and deploying Lambda |

Existing repository/environment secrets used by the workflow:

- `AWS_DEPLOYER_ROLE_ARN` – already used for OIDC; keep this set at repo or environment level.

## Config secrets (for hydrate step)

The deploy workflow runs a **Hydrate configs** step that injects secrets into `infra/infra.yaml` and `config/<env>/app_config.yaml` (templated files with `${PLACEHOLDER}` syntax). Set these per environment so the step can substitute real values:

| Secret name | Description |
|-------------|-------------|
| `MANAGEMENT_ACCOUNT_ID` | AWS management account ID (project-level; duplicate in each env) |
| `BUDGET_EMAIL` | Budget alert email (project-level; duplicate in each env) |
| `REPO` | GitHub repo (e.g. `owner/repo`) (project-level; duplicate in each env). Note: GitHub reserves the `GITHUB_` prefix for secret names. |
| `ACCOUNT_ID` | AWS account ID for this environment |
| `EMAIL` | Environment contact email |
| `DEPLOYER_ROLE_ARN` | IAM role ARN for GitHub Actions deployer (same as `AWS_DEPLOYER_ROLE_ARN` value) |
| `VPC_ID` | VPC ID for this environment |
| `SUBNET_IDS` | Comma-separated subnet IDs |
| `KNOWLEDGE_BASE_ID` | Bedrock Knowledge Base ID |
| `DB_CLUSTER_ARN` | RDS cluster ARN (for Aurora) |
| `DB_CREDENTIALS_SECRET_ARN` | Secrets Manager secret ARN for DB credentials |

These are synced from the `config_secrets` section of `infra/secrets/<env>_secrets.yaml` when you run `./scripts/setup/setup_github.sh`. To disable the hydrate step (e.g. private fork with real values committed), set repository or environment variable `HYDRATE_CONFIGS` to `false`.

## Syncing secrets from infra/secrets (optional)

To push secrets from your local `infra/secrets/{env}_secrets.yaml` files into GitHub environment secrets, use the GitHub setup script (requires [GitHub CLI](https://cli.github.com/) and [yq](https://github.com/mikefarah/yq)). Secrets are deployed by default:

```bash
# Branch protection + environments + deploy secrets (default)
./scripts/setup/setup_github.sh

# Deploy secrets for specific envs only
./scripts/setup/setup_github.sh --deploy-secrets dev staging

# Skip secret deployment
./scripts/setup/setup_github.sh --skip-secrets

# Dry run (print what would be set, do not call gh)
./scripts/setup/setup_github.sh --dry-run
```

Each env secrets file can define a `github_environment_secrets` map with the names above and a `config_secrets` map for the hydrate step; see `infra/secrets/template.secrets.yaml`. The script also maps `database.master_username` / `database.master_password` to `MASTER_DB_USERNAME` / `MASTER_DB_PASSWORD` when present.

## Notes

- You can omit optional secrets for an environment if that deployment path is not used (e.g. no `MASTER_DB_PASSWORD` if the DB already exists and you never pass `--master-password`).
- Values are masked in logs; do not pass secrets via `workflow_dispatch` inputs.
