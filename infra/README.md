# Infrastructure Configuration

`infra/` contains the deployment manifest, CloudFormation templates, IAM roles, IAM policies, and uncommitted secret/state files used by the deployment scripts.

## Important Files

```text
infra/
  infra.yaml                         # committed logical deployment manifest
  secrets/
    template.accounts.secrets.yaml   # committed template for account values
    template.environment.secrets.yaml # committed template for env values
    <account-set>/accounts.secrets.yaml # uncommitted real account values
    <account-set>/<env>.secrets.yaml    # uncommitted real environment values
  state/
    <env>.outputs.json               # generated deployment outputs, gitignored
  resources/                         # CloudFormation resource templates
  roles/                             # IAM role templates
  policies/                          # IAM policy templates
```

## `infra.yaml`

`infra.yaml` is the committed source of truth for:

- project name and default region;
- setup profiles;
- logical accounts;
- deployable environments;
- GitHub CI/CD participation;
- roles and policies;
- resources, stack names, templates, config, ownership, and deploy order.

It must not contain real account IDs, account names, role ARNs, generated resource IDs, or passwords.

## Accounts and Environments

Logical accounts are defined under `accounts`. Real values are stored in the file configured by `secrets.accounts_file` and referenced with `secret_ref`. The active account set currently points to `infra/secrets/adte_sandbox/accounts.secrets.yaml`.

Environments are defined under `environments`. An environment points to one logical account and may opt into GitHub CI/CD:

```yaml
environments:
  sandbox:
    account: management
    region: us-east-1
    secrets_file: infra/secrets/adte_sandbox/sandbox.secrets.yaml
    ci_cd:
      enabled: false
```

Only environments with `ci_cd.enabled: true` are configured by GitHub setup scripts.

## Resources

Resources are deployed in the order they appear in `infra.yaml`. Teardown happens in reverse order.

Common fields:

- `enabled`: deploy when true; skip deploy when false.
- `ownership`: `owned` resources are deleted by teardown; `imported` resources are used but skipped during teardown.
- `template`: CloudFormation template path.
- `stack_name`: stack name pattern.
- `config`: resource-specific settings.

Resource handlers are resolved by convention:

```text
resources.<resource_key> -> scripts/deploy/resources/<resource_key>.sh
```

Adding a resource should not require editing `deploy_all.sh`.

## Deployment Sets

Deployment sets let CI and local commands deploy a subset of enabled resources without changing resource `enabled` flags:

```yaml
deployment_sets:
  full:
    resources: all
  pr:
    resources:
      - rag_lambda_ecr
      - rag_lambda
```

`deploy_all.sh` preserves the order from `resources` and filters to the selected set:

```bash
./scripts/deploy/deploy_all.sh staging --set pr
```

## Secret Templates

Create real local secret files from templates:

```bash
cp infra/secrets/template.accounts.secrets.yaml infra/secrets/adte_sandbox/accounts.secrets.yaml
cp infra/secrets/template.environment.secrets.yaml infra/secrets/adte_sandbox/sandbox.secrets.yaml
```

These files are gitignored.
