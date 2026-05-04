# Deployment Process

Deployment has two separate tracks:

- **Setup track:** prepares AWS accounts, IAM access, GitHub OIDC, GitHub environments, and CI/CD roles.
- **Deployment track:** deploys application infrastructure into any configured environment using an AWS profile.

## Configuration

The committed deployment manifest is:

```text
infra/infra.yaml
```

It defines logical accounts, environments, roles, resources, resource order, ownership, and CI/CD participation.

Private values live in gitignored files:

```text
infra/secrets/<account-set>/accounts.secrets.yaml
infra/secrets/<account-set>/<env>.secrets.yaml
infra/state/<env>.outputs.json
```

This repo currently points `secrets.accounts_file` at:

```text
infra/secrets/adte_sandbox/accounts.secrets.yaml
```

Create local secret files from templates:

```bash
cp infra/secrets/template.accounts.secrets.yaml infra/secrets/adte_sandbox/accounts.secrets.yaml
cp infra/secrets/template.environment.secrets.yaml infra/secrets/adte_sandbox/sandbox.secrets.yaml
```

Environment secret file paths are configured under `environments.<env>.secrets_file`. Do not write account IDs, account names, generated ARNs, or passwords into committed `infra/infra.yaml`.

## Setup Track

Use setup when you want the standard production workflow:

- `dev`, `staging`, and `prod` AWS Organizations member accounts;
- budgets and cost controls;
- local CLI/admin profiles;
- GitHub OIDC;
- GitHub environments and secrets;
- deployer and evals roles.

Main commands:

```bash
./scripts/setup/setup_accounts.sh
./scripts/setup/setup_all.sh dev
./scripts/setup/setup_all.sh staging
./scripts/setup/setup_all.sh prod
./scripts/setup/setup_github.sh
```

`setup_accounts.sh` writes real account values to the configured `secrets.accounts_file`.

Environments with `ci_cd.enabled: false` are deployable locally but are not managed by GitHub setup.

## Deployment Track

Deploy the full deployment set for an environment:

```bash
./scripts/deploy/deploy_all.sh <environment>
```

Deploy a named deployment set:

```bash
./scripts/deploy/deploy_all.sh <environment> --set app
./scripts/deploy/deploy_all.sh staging --set pr
```

Preview a deployment:

```bash
./scripts/deploy/deploy_all.sh <environment> --set full --dry-run
```

Use a specific profile:

```bash
./scripts/deploy/deploy_all.sh sandbox --profile my-admin-profile
```

Deploy one resource directly:

```bash
./scripts/deploy/resources/s3_bucket.sh sandbox deploy
./scripts/deploy/resources/chat_db.sh sandbox status
./scripts/deploy/resources/rag_lambda.sh sandbox deploy
```

Resource handlers follow this convention:

```text
resources.<resource_key> -> scripts/deploy/resources/<resource_key>.sh
```

`deploy_all.sh` reads enabled resources from `infra.yaml`, filters them through the selected `deployment_sets.<name>.resources`, preserves YAML resource order, and calls the matching handler. It also checks `deploy_cost_control` before deploying cost guard/tags and `deploy_config` before hydrating and uploading app config. Adding a resource should require a new `resources.<resource_key>` entry and a matching handler, not an edit to `deploy_all.sh`.

The reusable GitHub deploy workflow accepts the same deployment set. The pull request workflow uses `deployment_set: pr` for staging so it can refresh Lambda/runtime code without redeploying network or database resources. Production-on-merge currently uses `deployment_set: pr` for the same runtime-only deployment surface in prod. Manual `workflow_dispatch` runs default to `deployment_set: full` unless another set is provided.

## Teardown

Tear down declared resources:

```bash
./scripts/deploy/teardown.sh <environment>
```

Preview teardown:

```bash
./scripts/deploy/teardown.sh <environment> --dry-run
```

Teardown calls resource handlers in reverse YAML order and skips resources marked:

```yaml
ownership: imported
```

`destroy_all.sh` is no longer supported.

## Reporting

Report declared resource status:

```bash
./scripts/deploy/report_resources.sh <environment>
./scripts/deploy/report_resources.sh <environment> --json
```

The report includes declared resources and likely orphan CloudFormation stacks that match the project/environment naming pattern.

## Adding a New Environment

1. Add a logical account under `accounts` in `infra/infra.yaml` if needed.
2. Add the environment under `environments`.
3. Add real account/profile values to the configured accounts secrets file.
4. Add an environment secrets file from `template.environment.secrets.yaml`.
5. Run `deploy_all.sh <env> --dry-run`.
6. Deploy with `deploy_all.sh <env>`.

For local-only environments, set:

```yaml
ci_cd:
  enabled: false
```

## Adding a New Resource

1. Add a new key under `resources` in `infra/infra.yaml`.
2. Add `scripts/deploy/resources/<resource_key>.sh`.
3. Keep resource-specific behavior inside the handler.
4. Use shared helpers only for repeated mechanics.
5. Run `deploy_all.sh <env> --dry-run` and `teardown.sh <env> --dry-run`.

## Config Hydration

Tracked config files can contain placeholders. Deployment uses runtime hydration:

```bash
scripts/utils/hydrate_configs.sh --run <env> -- <command>
```

This hydrates placeholders, runs the command, and restores tracked files.
