# GitHub environment secrets for deploy workflow

The deploy workflow (`.github/workflows/deploy.yml`) reads sensitive and environment-specific values from **environment secrets**. Use this doc when you need to **set or inspect these secrets manually** (e.g. when not using `setup_github.sh` or when the script fails).

Only environments with `ci_cd.enabled: true` in `infra/infra.yaml` are managed by GitHub setup. Local-only environments, such as the `sandbox` environment with `ci_cd.enabled: false`, do not need GitHub environment secrets.

Configure secrets in GitHub:

**Settings → Environments → &lt;environment&gt; → Environment secrets**

## Secret names to set

| Secret name | Description | Required when |
|-------------|-------------|----------------|
| `S3_APP_CONFIG_URI` | S3 URI for app config (e.g. `s3://bucket/key`) | Deploying Lambda |
| `LOCAL_APP_CONFIG_PATH` | Local app config file path (uploaded to S3) | Optional |
| `MASTER_DB_USERNAME` | Database master username | Optional; defaults to `postgres` in deploy script if unset |
| `MASTER_DB_PASSWORD` | Database master password | When creating or updating DB (e.g. first deploy or password change) |
| `AWS_REGION` | AWS region for deployment | Optional; defaults to `us-east-1` if unset |
| `ACCOUNT_ID` | Expected AWS account ID for deployment guardrails | Required for CI deployments unless another trusted account check is added |
| `GLOBAL_SUFFIX` | Stable suffix used for globally unique names such as S3 buckets; the full account ID is a convenient default, but any stable account-unique value works | Required when resource config uses `{global_suffix}` and the accounts secrets file is not present in CI |
| `VPC_ID` | VPC ID | When using an imported/existing VPC |
| `SUBNET_IDS` | Comma-separated subnet IDs | When using an imported/existing VPC |
| `SECURITY_GROUP_IDS` | Comma-separated security group IDs (for Lambda) | Optional Lambda VPC override |

Existing repository/environment secrets used by the workflow:

- `AWS_DEPLOYER_ROLE_ARN` – already used for OIDC; keep this set at repo or environment level.

## Config secrets (for hydrate step)

The deploy workflow runs a **Hydrate configs** step that injects secrets into runtime config files while the deployment command runs. Set these per environment so the step can substitute real values:

| Secret name | Description |
|-------------|-------------|
| `BUDGET_EMAIL` | Budget alert email (project-level; duplicate in each env) |
| `REPO` | GitHub repo (e.g. `owner/repo`) (project-level; duplicate in each env). Note: GitHub reserves the `GITHUB_` prefix for secret names. |
| `DEPLOYER_ROLE_ARN` | IAM role ARN for GitHub Actions deployer (same as `AWS_DEPLOYER_ROLE_ARN` value) |
| `VPC_ID` | VPC ID for this environment |
| `SUBNET_IDS` | Comma-separated subnet IDs |
| `KNOWLEDGE_BASE_ID` | Bedrock Knowledge Base ID |
| `DB_CLUSTER_ARN` | RDS cluster ARN (for Aurora) |
| `DB_CREDENTIALS_SECRET_ARN` | Secrets Manager secret ARN for DB credentials |
| `COGNITO_REGION` | AWS region for Cognito user pool/JWT issuer |
| `COGNITO_USER_POOL_ID` | Cognito User Pool ID for JWT validation |
| `COGNITO_APP_CLIENT_ID` | Cognito app client ID (audience) |
| `COGNITO_CALLBACK_URLS` | Optional OAuth callback URLs CSV for Cognito stack deploy |
| `COGNITO_LOGOUT_URLS` | Optional OAuth logout URLs CSV for Cognito stack deploy |
| `COGNITO_DOMAIN_PREFIX` | Optional hosted UI domain prefix |
| `COGNITO_GENERATE_CLIENT_SECRET` | Optional `true|false` (SPA default is `false`) |
| `FUNCTION_URL_CORS_ALLOW_ORIGINS` | Optional CSV of allowed frontend origins for Lambda Function URL CORS (for example `https://d123.cloudfront.net`) |

Most config secrets are synced from the `config_secrets` section of the file configured by `environments.<env>.secrets_file` when you run `./scripts/setup/setup_github.sh`. `ACCOUNT_ID`, `AWS_REGION`, and `GLOBAL_SUFFIX` are synced from the account/environment config. To disable the hydrate step, set repository or environment variable `HYDRATE_CONFIGS` to `false`.

## Syncing secrets from infra/secrets (optional)

To push secrets from your configured local environment secrets files into GitHub environment secrets, use the GitHub setup script (requires [GitHub CLI](https://cli.github.com/) and [yq](https://github.com/mikefarah/yq)). Secrets are deployed by default for CI/CD-enabled environments:

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

Each env secrets file can define a `github_environment_secrets` map with the names above and a `config_secrets` map for the hydrate step; see `infra/secrets/template.environment.secrets.yaml`. The script also maps `database.master_username` / `database.master_password` to `MASTER_DB_USERNAME` / `MASTER_DB_PASSWORD` when present.

The active `infra.yaml` account set currently uses paths like:

```text
infra/secrets/adte_sandbox/dev.secrets.yaml
infra/secrets/adte_sandbox/staging.secrets.yaml
infra/secrets/adte_sandbox/prod.secrets.yaml
```

`sandbox` is local-only (`ci_cd.enabled: false`), so it is not synced to GitHub unless that environment is later enabled for CI/CD.

## Notes

- You can omit optional secrets for an environment if that deployment path is not used (e.g. no `MASTER_DB_PASSWORD` if the DB already exists and you never pass `--master-password`).
- Values are masked in logs; do not pass secrets via `workflow_dispatch` inputs.
