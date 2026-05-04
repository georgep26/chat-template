# AWS Naming Convention

This document defines the naming convention for the `chat-template` AWS backend. The committed source of truth is `infra/infra.yaml`; deployment scripts resolve names from that file and from gitignored account/environment secret files.

## Source Of Truth

Resource names should be declared in one of these places:

- `infra/infra.yaml` for stack names, resource config, deployment order, ownership, and tokenized naming patterns.
- `infra/resources/*.yaml` for CloudFormation resources created inside a stack.
- `infra/roles/*.yaml` and `infra/policies/*.yaml` for IAM names.

Do not hard-code real account IDs, account names, generated ARNs, generated resource IDs, or passwords in committed files.

## Naming Tokens

Deployment scripts substitute these tokens when reading `infra.yaml`:

| Token | Source | Notes |
| --- | --- | --- |
| `{project}` | `project.name` | Current value is `chat-template`. |
| `{env}` | Deployment environment | Current environments are `sandbox`, `dev`, `staging`, and `prod`. |
| `{account}` | Environment account key | Example: `management`, `dev`, `staging`, `prod`. |
| `{account_id}` | Account secrets | Real value stays in gitignored account secrets. |
| `{region}` | Environment region | Use only when a resource truly needs region in the name. |
| `{global_suffix}` | Account secrets, then `GLOBAL_SUFFIX`, then `ACCOUNT_ID` | Used for globally unique names. |

Use the full AWS account ID as the default `global_suffix` for real deployments. It does not have to be the account ID; it only needs to be stable and unique across accounts/environments that could create globally named resources such as S3 buckets.

## Environments

Use these environment names:

- `sandbox`: local or manual deployments into the configured management account.
- `dev`: development member account.
- `staging`: staging member account.
- `prod`: production member account.

Do not introduce aliases such as `qa`, `stage`, `production`, or `development` unless `infra.yaml` is intentionally changed to add a new environment.

## Resource Patterns

The active stack/resource patterns are declared in `infra.yaml`. Current examples:

| Resource | Stack name | Primary AWS name |
| --- | --- | --- |
| S3 document bucket | `{project}-s3-{env}` | `{project}-{env}-s3-{global_suffix}` |
| Chat database | `{project}-chat-db-{env}` | `{project}-light-db-cluster-{env}` |
| DB secret | `{project}-chat-db-secret-{env}` | `{project}-db-connection-{env}` or stack output value |
| Bedrock Knowledge Base | `{project}-rag-kb-{env}` | `{project}-kb-{env}` |
| RAG Lambda ECR | `{project}-rag-lambda-ecr-{env}` | `{project}-rag-lambda-{env}` |
| Cognito | `{project}-cognito-{env}` | `{project}-{env}-users`, `{project}-{env}-spa-client` |
| Frontend | `{project}-frontend-{env}` | `{project}-{env}-frontend-{account_id}` |
| RAG Lambda | `{project}-rag-lambda-{env}` | `{project}-{env}-rag-chat` |
| Cost guard | `{project}-cost-guard-{env}` | `{project}-{env}-cost-guard-*` |

Prefer stack names in docs and scripts when possible. Generated physical names should be discovered from CloudFormation outputs or AWS APIs unless the name is explicitly declared in `infra.yaml`.

## IAM Names

IAM names should describe the principal or policy purpose:

| IAM resource | Pattern |
| --- | --- |
| Deployer role | `{project}-{env}-deployer-github-actions-role` |
| Evals role | `{project}-{env}-evals-github-actions-role` |
| Local admin CLI role | `{project}-{env}-admin-cli-role` |
| RAG Lambda execution role | `{project}-{env}-lambda-execution-role` |
| Assume org access policy | `{project}-assume-org-access-role-policy` |

IAM roles and policies that need account-specific ARNs should receive account IDs at setup/deploy time from secret files or the active AWS identity, not from committed config.

## Tags

Tags are required for lookup, cost allocation, and governance. `infra.yaml` currently defines:

```yaml
tags:
  required:
    - Name
    - Project
    - Environment
  optional:
    - Owner
    - CostCenter
    - ManagedBy
```

Cost allocation tags currently include:

```yaml
cost_tags:
  allocation_tags:
    - Project
    - Environment
    - Owner
    - CostCenter
  defaults:
    ManagedBy: cloudformation
```

Use `ManagedBy: cloudformation` for resources created by these templates. Use the `ownership` field in `infra.yaml` to distinguish owned resources from imported resources; do not encode ownership in the name.

## Golden Rules

1. Keep `infra.yaml` as the naming source of truth.
2. Include `{env}` in environment-specific resources.
3. Use `{global_suffix}` only when an AWS name must be globally unique.
4. Do not rename long-lived production resources casually; CloudFormation replacements can delete or recreate resources.
5. Use CloudFormation outputs and AWS APIs for generated names instead of copying generated values into committed docs.
6. Keep secret/account-specific values in `infra/secrets/**`, GitHub environment secrets, or runtime environment variables.
