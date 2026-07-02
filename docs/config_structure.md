# Configuration Structure

This project separates committed deployment intent from uncommitted account and runtime values.

## Application Config

Per-environment app config lives under:

```text
config/<env>/app_config.yaml
```

These tracked files are **templates** — they contain placeholders such as:

```text
${KNOWLEDGE_BASE_ID}
${DB_CLUSTER_ARN}
${DB_CREDENTIALS_SECRET_ARN}
```

Real values are never written back into these files. They must always retain
their placeholders in version control.

### Hydrated output (.build/)

Hydration writes the resolved file to a gitignored build path:

```text
.build/config/<env>/app_config.yaml
```

The two hydration entry points are:

- `scripts/utils/hydrate_configs.sh` — sed-replaces `${...}` placeholders with
  values from `infra/secrets/<account-set>/<env>.secrets.yaml` (or CI env vars).
- `scripts/deploy/sync_app_config.sh` — `yq -i` overwrites resource/ingestion
  values from `infra/infra.yaml` into the build copy.

Both copy the tracked template into `.build/` first, then edit only the build
copy. The deploy step (`scripts/deploy/deploy_s3_bucket.sh`) prefers the build
copy when uploading to `s3://<bucket>/config/app_config.yaml` and falls back to
the template only when no build copy exists (e.g. a private fork where the
tracked file already has real values).

To clean the build output:

```bash
./scripts/utils/hydrate_configs.sh --restore
# or just
rm -rf .build/
```

Why a separate build path: this repo is intended to remain safe as a public
template, so hydrated resource IDs and ARNs must never reach the tracked file.
On a private fork where leakage is not a concern you can ignore the build path
entirely — just commit real values into `config/<env>/app_config.yaml` and set
`HYDRATE_CONFIGS=false`.

## Infrastructure Manifest

`infra/infra.yaml` is committed and describes:

- `project`: project name and default region;
- `setup_profiles`: standard AWS Organizations setup profile;
- `github`: repository and branch protection settings;
- `accounts`: logical account references;
- `environments`: deployable environments and CI/CD participation;
- `roles`: IAM role and policy stack definitions;
- `resources`: deployable resource definitions in deployment order;
- `deployment_sets`: named subsets of enabled resources for full, app-layer, or runtime-only deploys.

Do not commit account IDs, account names, real role ARNs, database credentials, or generated resource IDs to `infra.yaml`.

## Account Secrets

Real account values live in the file configured by `secrets.accounts_file`:

```text
infra/secrets/<account-set>/accounts.secrets.yaml
```

The current active file is:

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

The full AWS account ID is the default `global_suffix` written by setup scripts. It does not have to be the account ID; it only needs to be stable and unique across accounts/environments that can create globally named resources.

## Environment Secrets

Per-environment secrets live in the file configured by `environments.<env>.secrets_file`:

```text
infra/secrets/<account-set>/<env>.secrets.yaml
```

Current active examples include:

```text
infra/secrets/adte_sandbox/sandbox.secrets.yaml
infra/secrets/adte_sandbox/dev.secrets.yaml
infra/secrets/adte_sandbox/staging.secrets.yaml
infra/secrets/adte_sandbox/prod.secrets.yaml
```

Create one from the template:

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

`deploy_all.sh` reads enabled resources from `infra.yaml`, filters them by the selected deployment set, and calls handlers by convention:

```text
resources.<resource_key> -> scripts/deploy/resources/<resource_key>.sh
```

See [scripts/deploy/README.md](../scripts/deploy/README.md) for command usage.
