# Plan: Migrate Chat Template Deployment to New AWS Accounts

## Context

The chat template (RAG chat application) is currently deployed across three AWS accounts (dev/staging/prod) under one AWS Organization. We need to move the deployment to three **new** AWS accounts under a **different** management account / AWS Organization. The old deployment stays running in parallel until the new one is verified. The same GitHub repo is used.

## Key Insight: No changes to `infra.yaml` needed

`infra.yaml` uses `${PLACEHOLDER}` syntax throughout. All real values are injected at deploy time by `hydrate_configs.sh`, which reads from `infra/secrets/{env}_secrets.yaml` (locally) or GitHub environment variables (in CI). The only script that writes directly to `infra.yaml` is `setup_accounts.sh` (account IDs, names, CLI profile names), which we'll run as part of bootstrap.

**The only files we need to manually create/edit are the per-environment secrets files.**

---

## Migration Steps

### Phase 1: Create New Secrets Files

Create `infra/secrets/{dev,staging,prod}_secrets.yaml` by copying from `template.secrets.yaml`.

Fill in each file with values for the new accounts:

| Key | Source |
|-----|--------|
| `database.master_username` | Choose (e.g. `postgres`) |
| `database.master_password` | Generate new secure password |
| `github.org` / `github.repo` | Same repo as current |
| `config_secrets.MANAGEMENT_ACCOUNT_ID` | New management account ID |
| `config_secrets.BUDGET_EMAIL` | Your alert email |
| `config_secrets.REPO` | Same as `github.org/github.repo` |
| `config_secrets.ACCOUNT_ID` | New per-env account ID |
| `config_secrets.EMAIL` | Per-env account email |
| `config_secrets.COGNITO_REGION` | `us-east-1` |

Leave these blank/placeholder — they'll be populated by deploy scripts and updated after resources are created:
- `DEPLOYER_ROLE_ARN`, `VPC_ID`, `SUBNET_IDS`
- `KNOWLEDGE_BASE_ID`, `DB_CLUSTER_ARN`, `DB_CREDENTIALS_SECRET_ARN`
- `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, `COGNITO_HOSTED_UI_DOMAIN`
- `FUNCTION_URL_CORS_ALLOW_ORIGINS`
- `github_environment_secrets.*` (synced to GitHub by `setup_github.sh`)

### Phase 2: Configure AWS CLI for New Management Account

Before running any scripts, ensure the AWS CLI can authenticate to the new management account:
- Either set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars, or
- Configure a profile and set `AWS_PROFILE`

Verify with: `aws sts get-caller-identity`

### Phase 3: Bootstrap Accounts

Run `setup_accounts.sh` with `--skip-accounts` (since accounts already exist). This will:
1. Deploy the `assume_org_access_role_policy` CloudFormation stack in the management account
2. Deploy the `management_account_admin_user` IAM user stack
3. Deploy CLI roles in each member account (via `OrganizationAccountAccessRole`)
4. Update `~/.aws/config` and `~/.aws/credentials` with new profiles
5. Write account IDs back to `infra.yaml`

**However** — `setup_accounts.sh` finds accounts by name (`chat-template-dev`, etc.). If the new accounts have different names, provide the correct emails so it can find them. Since accounts already exist, it will detect them via `find_account_by_name()` and skip creation.

**Potential issue**: If the new account names differ from `{project}-{env}` convention, the script won't find them by name. In that case, we'd need to either:
- (a) Rename the accounts to match, or
- (b) Manually write the account IDs to `infra.yaml` and run with `--skip-accounts --skip-cli-roles`, then deploy CLI roles separately

Command:
```bash
./scripts/setup/setup_accounts.sh -y
```

### Phase 4: Setup Per-Environment (OIDC + Roles + GitHub)

Run `setup_all.sh` for each environment with `--skip-accounts`:

```bash
./scripts/setup/setup_all.sh dev --skip-accounts -y
./scripts/setup/setup_all.sh staging --skip-accounts -y
./scripts/setup/setup_all.sh prod --skip-accounts -y
```

Each run executes these steps:
1. ~~Setup AWS accounts~~ (skipped)
2. **Setup OIDC provider** — Creates GitHub OIDC identity provider in the account
3. **Setup deployer roles** — Creates IAM deployer role for GitHub Actions (OIDC)
4. **Setup GitHub environments** — Updates GitHub repo environment secrets with new role ARNs and config
5. **Setup evals roles** — Creates IAM evals role for GitHub Actions

**Important**: Step 4 (GitHub secrets) will overwrite the old account references. After this, GitHub Actions CI/CD will target the new accounts. The old deployment will no longer receive CI/CD updates. Time this carefully — consider doing dev first, verifying, then staging, then prod.

### Phase 5: Deploy Infrastructure

Run full deployment for each environment:

```bash
./scripts/deploy/deploy_all.sh dev -y
./scripts/deploy/deploy_all.sh staging -y
./scripts/deploy/deploy_all.sh prod -y
```

Deploys in order (per `infra.yaml`):
1. **Network** — Discovers default VPC/subnets (`use_defaults: true`)
2. **S3 bucket** — `chat-template-{env}-s3-bucket`
3. **Aurora DB** — Secret + Serverless v2 cluster
4. **Bedrock Knowledge Base** — KB with pgvector on Aurora
5. **Cognito** — User pool + app client
6. **Frontend** — S3 + CloudFront distribution
7. **ECR repo** — Container image repository
8. **Lambda** — Builds Docker image, pushes to ECR, deploys function with Function URL
9. **Cost guard** — Budget monitoring (optional, skip with `--skip-cost-guard` initially)

Deploy scripts will auto-populate dynamic values (DB ARNs, KB ID, VPC info, etc.) back into `infra.yaml` and secrets.

**Pre-requisite**: Ensure Bedrock model access is enabled in each new account for:
- `amazon.titan-embed-text-v2:0` (embeddings)
- `amazon.nova-micro-v1:0` (or whichever models are in `app_config.yaml`)

This requires manual action in the Bedrock console (Model access page) for each account.

### Phase 6: Update Secrets with Deployed Resource Values

After Phase 5, update each secrets file (`infra/secrets/{env}_secrets.yaml`) with the values output by the deploy scripts:
- `KNOWLEDGE_BASE_ID` — from KB deployment output
- `DB_CLUSTER_ARN` — from DB deployment output
- `DB_CREDENTIALS_SECRET_ARN` — from DB secret deployment output
- `COGNITO_USER_POOL_ID` — from Cognito deployment output
- `COGNITO_APP_CLIENT_ID` — from Cognito deployment output
- `COGNITO_HOSTED_UI_DOMAIN` — from Cognito deployment output (if configured)
- `FUNCTION_URL_CORS_ALLOW_ORIGINS` — new CloudFront distribution URL
- `VPC_ID`, `SUBNET_IDS` — from network discovery

Then re-run GitHub secrets sync:
```bash
./scripts/setup/setup_github.sh dev -y
./scripts/setup/setup_github.sh staging -y
./scripts/setup/setup_github.sh prod -y
```

### Phase 7: Update Frontend Configuration

Update frontend `.env` files with values from Phase 5:
- `chat-template-frontend/.env.staging` (and `.env.local`, `.env.prod` as needed)

| Variable | New Value |
|----------|-----------|
| `VITE_CHAT_API_BASE_URL` | New Lambda function URL |
| `VITE_COGNITO_USER_POOL_ID` | New Cognito user pool ID |
| `VITE_COGNITO_APP_CLIENT_ID` | New app client ID |
| `VITE_COGNITO_DOMAIN` | New Cognito hosted UI domain |
| `VITE_ALLOWED_API_ORIGIN` | New Lambda function URL |
| `VITE_COGNITO_REDIRECT_URI` | New CloudFront URL + `/auth/callback` |
| `VITE_COGNITO_LOGOUT_REDIRECT_URI` | New CloudFront URL |

Then redeploy frontend:
```bash
./scripts/deploy/deploy_frontend.sh dev -y
./scripts/deploy/deploy_frontend.sh staging -y
./scripts/deploy/deploy_frontend.sh prod -y
```

### Phase 8: Upload Knowledge Base Content

1. Upload KB source documents to new S3 buckets under `kb_sources/` prefix
2. Trigger KB sync/ingestion job per environment

### Phase 9: Verification

1. **Test each environment end-to-end:**
   - Hit Lambda function URL with a test request
   - Access CloudFront frontend URL → verify Cognito login
   - Send a chat message → verify RAG retrieval works
2. **Verify CI/CD:**
   - Push to `development` branch → verify deploy workflow targets new dev account
   - Create PR to `main` → verify staging deployment
3. **Run evals** (optional):
   ```bash
   make eval  # or trigger via GitHub Actions
   ```

### Phase 10: Decommission Old Deployment

Once verified:
1. Tear down old resources: `./scripts/deploy/destroy_all.sh {env}` (using old CLI profiles)
2. Remove old AWS CLI profiles from `~/.aws/config` / `~/.aws/credentials`
3. Close old member accounts via Organizations (optional)

---

## Files to Create/Modify

| File | Action | Notes |
|------|--------|-------|
| `infra/secrets/dev_secrets.yaml` | Create/update | New account IDs, DB creds, emails |
| `infra/secrets/staging_secrets.yaml` | Create/update | New account IDs, DB creds, emails |
| `infra/secrets/prod_secrets.yaml` | Create/update | New account IDs, DB creds, emails |
| `chat-template-frontend/.env.staging` | Update | New Lambda URL, Cognito, CloudFront |
| `chat-template-frontend/.env.local` | Update | New local dev config |
| `~/.aws/config` | Auto-updated | By `setup_accounts.sh` |
| `~/.aws/credentials` | Auto-updated | By `setup_accounts.sh` |
| `infra/infra.yaml` | Auto-updated | By `setup_accounts.sh` and deploy scripts (via hydrate) |

## Existing Scripts Reused (no code changes needed)

| Script | Purpose |
|--------|---------|
| `scripts/setup/setup_accounts.sh --skip-accounts` | Management IAM user + CLI roles |
| `scripts/setup/setup_all.sh --skip-accounts` | OIDC, deployer roles, GitHub secrets, evals roles |
| `scripts/deploy/deploy_all.sh` | Full infra + app deployment |
| `scripts/deploy/deploy_frontend.sh` | Frontend build + S3/CloudFront deploy |
| `scripts/utils/hydrate_configs.sh` | Injects secrets into config templates at deploy time |

## Risk Considerations

- **GitHub secrets overwrite**: Updating GitHub environment secrets (Phase 4, step 4) redirects CI/CD to new accounts. Old deployment loses CI/CD. Do one env at a time.
- **URLs change**: Lambda function URLs, CloudFront domains, Cognito domains are all new. Update any external references.
- **KB data**: Must be re-uploaded and ingested in new accounts.
- **DB content**: Chat history doesn't carry over (fresh Aurora DB). Use pg_dump/restore if historical data is needed.
- **Bedrock model access**: Must be manually enabled in each new account via the AWS console before deployment.
- **Account naming**: `setup_accounts.sh` finds existing accounts by name. If new accounts aren't named `chat-template-{dev,staging,prod}`, the lookup will fail — see Phase 3 note.
