# Deployment Scripts

Deployment is split into orchestration scripts and per-resource handlers.

## Main Commands

Deploy the full deployment set for an environment:

```bash
./scripts/deploy/deploy_all.sh <environment>
```

Deploy a named deployment set:

```bash
./scripts/deploy/deploy_all.sh <environment> --set app
./scripts/deploy/deploy_all.sh staging --set pr
```

Preview the plan without touching AWS:

```bash
./scripts/deploy/deploy_all.sh <environment> --set full --dry-run
```

Tear down declared resources for an environment:

```bash
./scripts/deploy/teardown.sh <environment>
```

Report declared stack status:

```bash
./scripts/deploy/report_resources.sh <environment>
./scripts/deploy/report_resources.sh <environment> --json
```

The report also flags likely orphan CloudFormation stacks whose names match the project/environment but are no longer declared in `infra.yaml`.

Use a specific local AWS profile:

```bash
./scripts/deploy/deploy_all.sh sandbox --profile my-admin-profile
./scripts/deploy/teardown.sh sandbox --profile my-admin-profile
```

## Resource Handlers

`deploy_all.sh` reads `infra/infra.yaml`, selects a deployment set, and deploys matching enabled resources in YAML order. For each resource key, it calls a handler by convention:

```text
resources.<resource_key> -> scripts/deploy/resources/<resource_key>.sh
```

Example:

```text
resources.rag_lambda -> scripts/deploy/resources/rag_lambda.sh
```

Handlers share this contract:

```bash
./scripts/deploy/resources/<resource_key>.sh <environment> <deploy|delete|status|validate> [-y] [--profile <aws-profile>]
```

Run one resource directly:

```bash
./scripts/deploy/resources/s3_bucket.sh sandbox deploy
./scripts/deploy/resources/rag_lambda.sh sandbox status
./scripts/deploy/resources/chat_db.sh sandbox delete
```

## Adding a Resource

To add a resource:

1. Add `resources.<resource_key>` to `infra/infra.yaml`.
2. Add `scripts/deploy/resources/<resource_key>.sh`.
3. Put resource-specific logic in the handler.
4. Use shared code under `scripts/utils/` only for common plumbing.

Do not edit `deploy_all.sh` just to add a resource.

## Configuration Sources

Normal deploy-time values come from:

- `infra/infra.yaml`: committed logical topology and resource definitions.
- configured `secrets.accounts_file`: uncommitted account IDs, account names, profiles, and global suffixes.
- configured `environments.<env>.secrets_file`: uncommitted environment secrets and runtime config values, usually `infra/secrets/<account-set>/<env>.secrets.yaml`.
- CloudFormation outputs and AWS APIs.

`infra.yaml` should not contain real account IDs, account names, role ARNs, database credentials, or generated resource IDs.

## Teardown

`teardown.sh` is the supported teardown path. It calls resource handlers in reverse YAML order and skips resources marked `ownership: imported`.

`destroy_all.sh` has been removed.
