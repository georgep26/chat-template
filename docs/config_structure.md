# Configuration Structure

This project separates committed deployment intent from uncommitted account and runtime values.

## Application Config

Per-environment app config lives under:

```text
config/<env>/app_config.yaml
```

These files may contain placeholders such as:

```text
${KNOWLEDGE_BASE_ID}
${DB_CLUSTER_ARN}
${DB_CREDENTIALS_SECRET_ARN}
```

During deployment, `scripts/utils/hydrate_configs.sh --run` hydrates placeholders from environment secrets, runs config deployment, and restores tracked files.

## Infrastructure Manifest

`infra/infra.yaml` is committed and describes:

- `project`: project name and default region;
- `setup_profiles`: standard AWS Organizations setup profile;
- `github`: repository and branch protection settings;
- `accounts`: logical account references;
- `environments`: deployable environments and CI/CD participation;
- `roles`: IAM role and policy stack definitions;
- `resources`: deployable resource definitions in deployment order.

Do not commit account IDs, account names, real role ARNs, database credentials, or generated resource IDs to `infra.yaml`.

## Account Secrets

Real account values live in:

```text
infra/secrets/accounts.secrets.yaml
```

`infra/infra.yaml` can override this with `secrets.accounts_file`. The current active file is:

```text
infra/secrets/adte_sandbox/accounts.secrets.yaml
```

Create it from:

```bash
cp infra/secrets/template.accounts.secrets.yaml infra/secrets/adte_sandbox/accounts.secrets.yaml
```

This file stores values such as:

- account IDs;
- account names;
- local AWS profile names;
- organization access role names;
- stable global suffixes for globally unique resource names.

## Environment Secrets

Per-environment secrets live in:

```text
infra/secrets/<env>.secrets.yaml
```

Create one from:

```bash
cp infra/secrets/template.environment.secrets.yaml infra/secrets/adte_sandbox/sandbox.secrets.yaml
```

This file stores values such as:

- database credentials;
- GitHub environment secrets;
- VPC and subnet IDs;
- DB cluster ARN;
- DB credentials secret ARN;
- Knowledge Base ID;
- Cognito IDs.

## Generated State

Generated outputs may be written under:

```text
infra/state/<env>.outputs.json
```

This directory is gitignored. It is local convenience state and should be reconstructable from CloudFormation outputs and AWS APIs.

## Deployment Scripts

`deploy_all.sh` reads enabled resources from `infra.yaml` and calls handlers by convention:

```text
resources.<resource_key> -> scripts/deploy/resources/<resource_key>.sh
```

See [scripts/deploy/README.md](../scripts/deploy/README.md) for command usage.
