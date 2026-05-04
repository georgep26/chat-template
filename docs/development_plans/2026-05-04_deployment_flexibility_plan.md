# Flexible Deployment Strategy Plan

Generated: 2026-05-04. This is a historical development plan; active deployment behavior is documented in `docs/deployment_process.md`, `infra/README.md`, and `scripts/deploy/README.md`.

## Goal

Build a deployment strategy that supports these paths:

- default production-style setup with separate `dev`, `staging`, and `prod` AWS accounts created through AWS Organizations;
- simple deployment into an existing account, including the management account or any sub-account;
- multiple environments in one account;
- one environment per account;
- mixed layouts, such as `dev` and `staging` in one account and `prod` in another.

The key distinction is:

- **setup scripts** prepare accounts, IAM access, GitHub environments, GitHub OIDC, and CI/CD roles;
- **deployment scripts** deploy application infrastructure into any configured environment using an AWS profile that already has the required permissions.

`infra/infra.yaml` should remain the visible source of truth for what can be deployed, what resources exist, and which environments are defined. It should not contain account IDs, account names, role ARNs, database passwords, API keys, or other deployment-specific private values.

## Research Notes

The updated strategy should follow these practices:

- AWS recommends separating workloads across accounts for isolation, billing, quotas, and blast-radius control. The default `dev/staging/prod` account setup should remain the recommended production workflow.
- AWS recommends keeping workloads out of the Organizations management account unless the workload must run there. The repo should support management-account deployment for convenience and experiments, but documentation should make clear that a member or sandbox account is the safer default.
- GitHub environments, environment secrets, and OIDC are a good fit for the CI/CD path. They should be tied only to environments that opt into CI/CD.
- CloudFormation stacks should be organized around resources that share a lifecycle. This supports independent deploy, update, drift detection, and teardown.
- Sensitive values should not be committed in templates or config. Local secret files, GitHub environment secrets, AWS Secrets Manager, or SSM Parameter Store should provide runtime values.

References:

- [AWS Organizations management account best practices](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_best-practices_mgmt-acct.html)
- [AWS multi-account strategy guidance](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/benefits-of-using-multiple-aws-accounts.html)
- [AWS CloudFormation best practices](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html)
- [GitHub Actions environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [GitHub Actions OIDC with AWS](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services)

## Current Issues

### Setup and deployment are mixed

The current scripts often assume that the target environment was created by the repo's AWS Organizations setup. That makes it harder to deploy into an existing account with an already configured AWS profile.

Going forward, setup and deployment should be separate.

Setup answers:

- Should accounts be created?
- Should CLI/admin roles be created?
- Should GitHub OIDC be configured?
- Should GitHub environments and secrets be configured?
- Should CI/CD deployer and evals roles be created?

Deployment answers:

- Which environment am I deploying?
- Which account does that environment resolve to?
- Which AWS profile should be used?
- Which resources are enabled?
- What order should those resources deploy in?
- What stack outputs are needed by later resources or app config?

### `infra.yaml` contains or expects account-specific values

The current config model includes fields like `account_id`, `account_name`, profile names, and deployed output values inside `infra/infra.yaml`. The new approach should avoid committing any value that reveals the user's actual AWS deployment.

Committed config should describe logical topology and desired infrastructure. Uncommitted secrets/state should provide actual account IDs, account names, role ARNs, generated global suffixes, credentials, and deployed output values.

### Scripts still know too much about resource names

`deploy_all.sh` and teardown logic should not need edits when a new resource is added. The orchestrator should read resource definitions from `infra.yaml`, preserve YAML order, and call the resource handler that matches the resource key by convention.

The plan should avoid putting script paths in `infra.yaml` for every resource. That would turn the config into a script registry. Instead, `infra.yaml` should define the desired resource, and the deployment layer should resolve `resources.<resource_key>` to `scripts/deploy/resources/<resource_key>.sh`.

### Hardcoded environment names remain

Many scripts still assume `dev`, `staging`, and `prod`. That should remain the default setup topology, but it should not be a limitation of deployment.

Deployment scripts should accept any environment key defined in `infra.yaml`.

## Target File Model

### Committed files

`infra/infra.yaml`

- logical project name;
- default region;
- logical accounts;
- logical environments;
- setup profiles;
- CI/CD participation;
- roles, policies, and resources;
- stack names, templates, parameters, ownership, outputs, and enabled flags.

`infra/secrets/template.accounts.secrets.yaml`

- documented template for account-specific secrets;
- committed with placeholders only.

`infra/secrets/template.environment.secrets.yaml`

- documented template for per-environment runtime secrets;
- committed with placeholders only.

`config/<env>/app_config.yaml`

- committed application config with placeholders;
- no account IDs or deployed ARNs.

### Uncommitted files

`infra/secrets/accounts.secrets.yaml`

- actual account IDs;
- actual account names;
- profile names;
- bootstrap role names;
- deployer role ARNs if created outside the repo;
- stable generated global suffixes for globally unique resources.

`infra/secrets/<env>.secrets.yaml`

- environment-specific runtime secrets;
- database usernames/passwords;
- app secrets;
- GitHub environment secrets for CI/CD environments;
- optional deployer role ARN or profile override.

`infra/state/<env>.outputs.json`

- generated deployment outputs;
- CloudFormation stack outputs;
- Knowledge Base IDs;
- DB cluster ARNs;
- S3 config URIs;
- Lambda ARNs;
- any value needed by app config hydration or resource reporting.

The `infra/state/` directory should be gitignored. It is convenience state, not source of truth. Deploy scripts should be able to reconstruct it from CloudFormation outputs and AWS APIs.

## Target `infra.yaml` Shape

The config should be explicit, ordered, and mostly declarative.

Example:

```yaml
project:
  name: chat-template
  default_region: us-east-1

setup_profiles:
  default_standard_accounts:
    enabled: true
    type: aws_organizations
    environments:
      - dev
      - staging
      - prod
    create_accounts: true
    configure_github: true
    configure_oidc: true
    configure_deployer_roles: true
    configure_evals_roles: true
    configure_budgets: true

accounts:
  management:
    kind: management
    secret_ref: management
    allow_workload_deployments: true

  default-dev:
    kind: organizations_member
    secret_ref: default_dev
    created_by_setup_profile: default_standard_accounts

  default-staging:
    kind: organizations_member
    secret_ref: default_staging
    created_by_setup_profile: default_standard_accounts

  default-prod:
    kind: organizations_member
    secret_ref: default_prod
    created_by_setup_profile: default_standard_accounts

  sandbox:
    kind: existing
    secret_ref: sandbox

environments:
  dev:
    account: default-dev
    region: us-east-1
    secrets_file: infra/secrets/dev.secrets.yaml
    ci_cd:
      enabled: true
      github_environment: dev
      deployer_role: deployer
      evals_role: evals

  staging:
    account: default-staging
    region: us-east-1
    secrets_file: infra/secrets/staging.secrets.yaml
    ci_cd:
      enabled: true
      github_environment: staging
      deployer_role: deployer
      evals_role: evals
      promotion_source: dev

  prod:
    account: default-prod
    region: us-east-1
    secrets_file: infra/secrets/prod.secrets.yaml
    ci_cd:
      enabled: true
      github_environment: production
      deployer_role: deployer
      evals_role: evals
      promotion_source: staging

  playground:
    account: management
    region: us-east-1
    secrets_file: infra/secrets/playground.secrets.yaml
    ci_cd:
      enabled: false

roles:
  oidc_provider:
    enabled: true
    template: infra/roles/oidc_provider.yaml
    stack_name: "{project}-{env}-oidc-provider"
    setup_only: true

  cli_admin:
    enabled: true
    template: infra/roles/admin_cli_role.yaml
    stack_name: "{project}-{env}-cli-admin-role"
    setup_only: true

  deployer:
    enabled: true
    template: infra/roles/deployer_role.yaml
    stack_name: "{project}-{env}-deployer-role"
    setup_only: true

  rag_lambda_execution:
    enabled: true
    template: infra/roles/rag_lambda_execution_role.yaml
    stack_name: "{project}-{env}-rag-lambda-execution-role"
    setup_only: false

resources:
  s3_bucket:
    enabled: true
    ownership: owned
    template: infra/resources/s3_bucket_template.yaml
    stack_name: "{project}-{env}-s3-bucket"
    parameters:
      ProjectName: "{project}"
      Environment: "{env}"
      BucketName: "{project}-{env}-config-{global_suffix}"
    outputs:
      bucket_name: BucketName

  chat_db:
    enabled: true
    ownership: owned
    stack_name: "{project}-{env}-chat-db"
    template: infra/resources/light_db_template.yaml
    secondary_stacks:
      secret:
        stack_name: "{project}-{env}-db-secret"
        template: infra/resources/db_secret_template.yaml
    outputs:
      db_cluster_arn: DBClusterArn
      db_credentials_secret_arn: DBCredentialsSecretArn

  rag_knowledge_base:
    enabled: true
    ownership: owned
    stack_name: "{project}-{env}-knowledge-base"
    template: infra/resources/knowledge_base_template.yaml
    depends_on:
      - s3_bucket
      - chat_db
    outputs:
      knowledge_base_id: KnowledgeBaseId

  rag_lambda:
    enabled: true
    ownership: owned
    stack_name: "{project}-{env}-rag-lambda"
    template: infra/resources/rag_lambda_template.yaml
    depends_on:
      - chat_db
      - rag_knowledge_base
```

Important rules:

- Resource order is the order in `infra.yaml`.
- `enabled: true` means deploy the resource.
- `enabled: false` means skip deploy.
- Teardown checks all declared resources, including disabled resources, and deletes matching stacks if they exist.
- If a resource is removed from `infra.yaml`, teardown may not know its exact stack name. Disable and teardown before removing resources, or use the orphan sweep described below.
- `setup_only: true` roles are managed by setup scripts, not normal app deployment.
- `ci_cd.enabled: true` marks an environment as managed by GitHub CI/CD.
- `ci_cd.enabled: false` means deploy scripts can still deploy the environment locally, but setup scripts should not create GitHub environments or CI/CD deployer roles for it.
- Resource handlers are resolved by convention. For resource key `rag_lambda`, the handler is `scripts/deploy/resources/rag_lambda.sh`.
- Do not add script paths to every resource unless there is a strong reason to override the convention.

## Account Secrets Shape

`infra/secrets/accounts.secrets.yaml` should contain the real account and access details.

Example:

```yaml
accounts:
  management:
    account_id: "111122223333"
    account_name: my-management-account
    bootstrap_profile: my-management-admin
    deploy_profile: my-management-admin
    global_suffix: "111122223333"

  default_dev:
    account_id: "444455556666"
    account_name: chat-template-dev
    organization_access_role: OrganizationAccountAccessRole
    bootstrap_profile: my-management-admin
    deploy_profile: chat-template-dev-cli
    global_suffix: "444455556666"

  sandbox:
    account_id: "777788889999"
    account_name: sandbox-account
    bootstrap_profile: sandbox-admin
    deploy_profile: sandbox-admin
    global_suffix: "777788889999"
```

Notes:

- `global_suffix` should be generated once per account or environment and persisted in secrets. The full AWS account ID is a convenient default, but any stable value that is unique across deployed accounts/environments works.
- Avoid committing account IDs. Using the full account ID in gitignored `global_suffix` config is acceptable when you want predictable globally unique resource names.
- Use `aws sts get-caller-identity` during deploy to verify the selected profile resolves to the expected account.
- If no expected account ID is available, require an explicit `--allow-unknown-account` flag before deploying.

## Deployment Track

Deployment orchestration should be generic and environment-driven. Resource deployment logic should stay in per-resource handlers.

### Main commands

```bash
./scripts/deploy/deploy_all.sh <env> [-y] [--profile <aws-profile>] [--set <deployment-set>]
./scripts/deploy/resources/<resource_key>.sh <env> <deploy|delete|status|validate> [-y] [--profile <aws-profile>]
./scripts/deploy/teardown.sh <env> [-y] [--profile <aws-profile>]
./scripts/deploy/report_resources.sh <env> [--profile <aws-profile>]
```

No other deploy-time options should be needed for normal use. Values should come from:

1. `infra/infra.yaml`;
2. `infra/secrets/accounts.secrets.yaml`;
3. `infra/secrets/<env>.secrets.yaml`;
4. CloudFormation outputs and AWS APIs.

### Deploy behavior

`deploy_all.sh` should:

1. Load `infra.yaml`.
2. Load account secrets.
3. Validate that `<env>` exists.
4. Resolve the environment's account, region, global suffix, and profile.
5. Verify `aws sts get-caller-identity` matches the expected account.
6. Build a deploy summary from the enabled resources in the selected deployment set, preserving YAML order.
7. Prompt for confirmation unless `-y`.
8. Resolve each selected enabled resource to `scripts/deploy/resources/<resource_key>.sh` and call the handler.
9. Capture stack outputs after each resource deploy.
10. Write generated outputs to `infra/state/<env>.outputs.json`.
11. Hydrate app config from secrets plus outputs.
12. Upload config to the configured app config destination.

### Resource handler strategy

Each resource keeps its own handler script. The handler name should match the resource key in `infra.yaml`.

Directory layout:

```text
scripts/deploy/
  deploy_all.sh
  teardown.sh
  report_resources.sh
  resources/
    network.sh
    s3_bucket.sh
    chat_db.sh
    rag_knowledge_base.sh
    rag_lambda_ecr.sh
    cognito.sh
    frontend.sh
    rag_lambda.sh
  lib/
    deploy_context.sh
    cloudformation.sh
    resource_summary.sh
    output_state.sh
    aws_identity.sh
```

Handler contract:

```bash
scripts/deploy/resources/<resource_key>.sh <env> <deploy|delete|status|validate> [-y] [--profile <aws-profile>]
```

Rules:

- `deploy_all.sh` never contains a resource-specific `case` statement.
- `deploy_all.sh` reads the resource keys from `infra.yaml` in YAML order.
- For each enabled resource, `deploy_all.sh` calls `scripts/deploy/resources/<resource_key>.sh`.
- If the handler file does not exist, deployment fails with a clear missing-handler error.
- Individual resource handlers can be run directly for one-off deploy, status, validate, or delete actions.
- Handlers should read all parameters from config, secrets, generated outputs, and AWS APIs.
- Handlers should print a deploy summary and prompt unless `-y`.
- Handlers should use shared library functions for common plumbing, but keep resource-specific lifecycle logic local.

This intentionally avoids a central `deploy_resource.sh` that tries to implement every resource lifecycle. The current resources have enough unique behavior that a shared deploy implementation would become a large switch statement.

### Shared deployment libraries

The shared code should handle repeated mechanics only:

- config and secret loading;
- environment/account/profile resolution;
- AWS identity validation;
- standard summary and confirmation prompts;
- CloudFormation create/update/delete/wait helpers;
- standard tagging;
- stack output capture;
- generated state writes;
- common S3/ECR cleanup helpers.

The shared libraries should not know about the full lifecycle of `chat_db`, `rag_knowledge_base`, `rag_lambda`, or other complex resources.

Resource-specific handlers remain responsible for behavior such as:

- building and pushing Lambda images;
- creating Bedrock Knowledge Bases with non-CloudFormation API steps;
- emptying S3 buckets before deletion;
- copying ECR images between accounts;
- discovering or validating imported VPCs;
- creating database secrets;
- running database SQL setup;
- starting and polling Bedrock ingestion jobs;
- writing generated outputs to state.

This keeps `deploy_all.sh` stable when resources are added while still acknowledging that resources have genuinely different deployment lifecycles.

## Setup Track

Setup remains opinionated around the default production workflow.

### Main commands

```bash
./scripts/setup/setup_all.sh [-y]
./scripts/setup/setup_accounts.sh [-y]
./scripts/setup/setup_environment_access.sh <env> [-y]
./scripts/setup/setup_github.sh [-y]
```

### `setup_accounts.sh`

Purpose:

- create the default `dev`, `staging`, and `prod` AWS Organizations member accounts;
- create or validate CLI/admin access roles;
- create budgets and cost controls;
- write actual account IDs and account names to `infra/secrets/accounts.secrets.yaml`, not `infra.yaml`.

It should default to the setup profile in `infra.yaml`:

```yaml
setup_profiles.default_standard_accounts
```

It does not need to support every ad hoc deployment target. Existing accounts can be added manually to `accounts.secrets.yaml` and used directly by deploy scripts.

### `setup_environment_access.sh`

Purpose:

- configure OIDC provider when `ci_cd.enabled: true`;
- create deployer and evals roles when enabled;
- write role ARNs to the environment secrets file or account secrets file;
- optionally create local AWS profile blocks.

This script should skip GitHub and OIDC work for environments where `ci_cd.enabled: false`.

### `setup_github.sh`

Purpose:

- configure GitHub environments only for environments with `ci_cd.enabled: true`;
- deploy GitHub environment secrets from each environment's secrets file;
- configure branch protection and deployment rules;
- leave ad hoc local-only environments alone.

## Hydration Strategy

`infra.yaml` should not be mutated during deploy. Hydration should be runtime-only.

Inputs:

- committed config placeholders from `infra.yaml` and `config/<env>/app_config.yaml`;
- account secrets from `infra/secrets/accounts.secrets.yaml`;
- environment secrets from `infra/secrets/<env>.secrets.yaml`;
- deployed outputs from CloudFormation and AWS APIs.

Outputs:

- temporary hydrated files in a build directory;
- deployed app config in S3;
- generated local state under `infra/state/`;
- GitHub environment secrets for CI/CD environments.

Recommended flow:

1. Deploy resources.
2. Read CloudFormation outputs into memory.
3. Write `infra/state/<env>.outputs.json`.
4. Render app config into a temporary directory.
5. Upload rendered app config to S3.
6. Leave committed files unchanged.

Do not write account IDs, account names, ARNs, or generated resource IDs back into `infra.yaml`.

## Teardown Strategy

`teardown.sh` should replace `destroy_all.sh`. Do not preserve `destroy_all.sh` long-term.

### Teardown behavior

`teardown.sh <env>` should:

1. Load `infra.yaml` and secrets.
2. Resolve the environment account and profile.
3. Verify the active AWS account.
4. Show a teardown summary.
5. Delete resources in reverse YAML order by calling each resource handler with `delete`.
6. Include disabled resources if a matching stack exists.
7. Delete setup-managed roles only when explicitly requested or when running a full environment teardown.
8. Run a leftover report after deletion.

### Disabled resources

Deploy semantics:

- `enabled: true`: deploy or update.
- `enabled: false`: skip deployment.

Teardown semantics:

- all declared resources are candidates for deletion;
- if the stack exists, delete it even when `enabled: false`;
- this allows a resource to be disabled, torn down, and later removed from config.

### Orphan sweep

If a resource was removed from `infra.yaml` before teardown, the normal resource list may not know about it. Add a sweep mode:

```bash
./scripts/deploy/teardown.sh <env> --sweep
./scripts/deploy/teardown.sh <env> --sweep-delete
```

`--sweep` reports leftovers. `--sweep-delete` deletes leftovers after confirmation.

Sweep by:

- CloudFormation stacks tagged with `Project` and `Environment`;
- stack names matching `{project}-{env}`;
- S3 buckets with configured names or project/env tags;
- ECR repositories with project/env names;
- IAM roles and policies with project/env names or tags;
- CloudWatch log groups with project/env prefixes;
- Secrets Manager secrets created by the project;
- Bedrock Knowledge Base resources associated with the environment;
- cost guard stacks and budget resources.

Every deployed stack should include standard tags:

- `Project`
- `Environment`
- `ManagedBy`
- `Repo`
- `ResourceKey`
- `DeploymentId`

Tags are essential because names alone are not enough for reliable teardown.

## Resource Reporting

Add a reporting command:

```bash
./scripts/deploy/report_resources.sh <env> [--profile <aws-profile>] [--json]
```

The report should show:

- expected account and active account;
- region;
- resources declared in `infra.yaml`;
- stack status for each declared resource;
- stack outputs;
- disabled resources that still exist;
- orphan resources found by tag/name sweep;
- estimated cost-sensitive resources, such as NAT gateways, VPC endpoints, Aurora clusters, ECR images, and S3 buckets.

This can later back a Cursor command or agent command for "show deployed resources for this environment".

## Promotion Strategy

Production deployment should not rebuild images when promoting from staging.

If staging and prod are in the same account:

- retag the existing ECR image;
- update prod stack parameters to reference the promoted tag or digest.

If staging and prod are in different accounts:

- copy the image by digest from staging ECR to prod ECR;
- or configure ECR cross-account replication for promoted repositories;
- store the promoted image digest in deployment state;
- deploy prod from the digest, not from a rebuilt image.

This should be modeled in `infra.yaml`:

```yaml
environments:
  prod:
    ci_cd:
      promotion_source: staging
      promotion_mode: copy_image_digest
```

## Anticipated Hurdles

### Account IDs are needed but should not be committed

Some AWS operations require account IDs for ARNs, trust policies, and validation. Keep account IDs in `accounts.secrets.yaml`, then inject them at runtime.

### Global AWS resource names need uniqueness

S3 bucket names are globally unique. Since account IDs should not be committed, use a stable secret `global_suffix` instead of putting account IDs in committed names. The secret value can be the full account ID, but it only needs to be stable and unique across deployed accounts/environments.

### Multiple environments in one account can collide

Every stack name, bucket name, repository name, log group, and secret name must include `{env}`. Preflight should reject two environments in the same account if their resolved stack names collide.

### Removed resources are hard to delete

If a resource is deleted from `infra.yaml`, teardown loses its exact definition. Recommended workflow:

1. set `enabled: false`;
2. run teardown for that environment;
3. confirm report is clean;
4. remove the resource from `infra.yaml`.

Use orphan sweep for mistakes.

### CloudFormation outputs must replace config mutation

Existing scripts write deployed values back into config. The new strategy should use CloudFormation outputs and generated state instead. This avoids dirty working trees and keeps private account details out of committed files.

### CI/CD environments are not the same as deployable environments

Some environments are deployable but not CI/CD managed. `ci_cd.enabled` should be the deciding field. Setup scripts should not assume every environment needs GitHub configuration.

### Management account deployments need warnings

The repo should allow them, but deploy summaries should warn when deploying workload resources into the management account. Require explicit confirmation unless `-y` is used.

### Teardown can delete resources that should only be referenced

If an environment references a VPC, bucket, or other resource that is managed outside this deployment, teardown must know whether the resource is owned or imported.

Add ownership metadata:

```yaml
resources:
  network:
    enabled: true
    ownership: imported
```

Rules:

- `owned`: deploy and delete.
- `imported`: validate and use, but do not delete.

### Avoid a fake common deployer

Not every resource can be handled by a generic CloudFormation deployer. The current deployment scripts prove that resource lifecycles vary substantially.

The right common layer is:

- a common handler contract;
- convention-based handler lookup;
- shared libraries for repeated mechanics.

The wrong common layer is:

- one central deploy function that tries to know how every resource works;
- a large resource-specific `case` statement inside `deploy_all.sh`;
- script paths duplicated throughout `infra.yaml`.

Adding a new resource should require adding `resources.<resource_key>` to `infra.yaml` and adding `scripts/deploy/resources/<resource_key>.sh`. It should not require editing `deploy_all.sh`.

## Implementation Phases

### Phase 1: Define the new config contract

- Update `infra.yaml` to separate setup profiles, accounts, environments, roles, and resources.
- Add `accounts.secrets.yaml` and environment secrets templates.
- Add comments explaining that resource order is deployment order.
- Add `.gitignore` entries for account secrets, environment secrets, state files, and hydrated temp output.

### Phase 2: Build config resolution helpers

- Add helpers to enumerate environments from `infra.yaml`.
- Load account secrets.
- Resolve account, profile, region, global suffix, and CI/CD settings.
- Add placeholder substitution for `{project}`, `{env}`, `{account}`, `{region}`, and `{global_suffix}`.
- Add AWS identity validation with `sts get-caller-identity`.

### Phase 3: Rewrite deploy orchestration

- Update `deploy_all.sh` to read resources in YAML order.
- Remove skip flags.
- Use `enabled` to decide deploy behavior.
- Resolve resource handlers by convention: `scripts/deploy/resources/<resource_key>.sh`.
- Move repeated deployment plumbing into `scripts/deploy/lib/`.
- Keep resource-specific behavior inside each resource handler.
- Capture stack outputs to generated state.
- Keep individual resource handlers config-driven and limited to `<env> <action> [-y] [--profile]`.

### Phase 4: Rewrite hydration

- Stop mutating `infra.yaml`.
- Render app config into a temporary build directory.
- Read values from secrets and generated outputs.
- Upload rendered config to S3.
- Keep committed files unchanged.

### Phase 5: Refactor setup

- Keep `setup_accounts.sh` focused on the default `dev/staging/prod` AWS Organizations path.
- Write created account IDs and names to `accounts.secrets.yaml`.
- Add or update `setup_environment_access.sh`.
- Make `setup_github.sh` operate only on `ci_cd.enabled` environments.

### Phase 6: Harden teardown

- Consolidate on `teardown.sh`.
- Remove `destroy_all.sh`.
- Delete declared resources in reverse YAML order.
- Delete disabled resources if they exist.
- Respect `ownership`.
- Add orphan report and guarded orphan delete.

### Phase 7: Add reporting and validation

- Add `report_resources.sh`.
- Add config validation command.
- Add preflight collision checks.
- Add tests with multiple environment/account layouts.

### Phase 8: Clean up documentation

After the implementation is working, update the documentation so the repo describes one current deployment model instead of a mix of old and new workflows.

Primary docs to update:

- `README.md`
  - explain the new setup vs deployment split;
  - show the recommended quick-start path;
  - show how to deploy to an existing account with a local AWS profile;
  - show how to deploy the default `dev/staging/prod` account workflow;
  - point to the detailed deployment docs instead of duplicating every command.

- `infra/README.md`
  - document the new `infra.yaml` structure;
  - explain committed config vs uncommitted secrets/state;
  - explain resource order, `enabled`, `ownership`, `ci_cd.enabled`, and handler naming conventions;
  - document `accounts.secrets.yaml` and environment secrets templates.

- `scripts/deploy/README.md`
  - replace old per-script examples with the new handler contract;
  - document `deploy_all.sh`, direct resource handler usage, `teardown.sh`, and `report_resources.sh`;
  - explain that `deploy_all.sh` discovers handlers by resource key and does not need edits when a resource is added.

- `docs/deployment_process.md`
  - rewrite around the new setup track and deployment track;
  - remove old skip-flag and pass-through override guidance;
  - document the default AWS Organizations workflow separately from ad hoc existing-account deployment.

- `docs/config_structure.md`
  - update the `infra.yaml` and secrets descriptions;
  - remove references to writing account IDs and deployed ARNs into committed config;
  - document generated state under `infra/state/`.

- `docs/github_environment_secrets.md`
  - clarify that GitHub secrets apply only to environments with `ci_cd.enabled: true`;
  - document how local-only environments avoid GitHub setup.

- `docs/oidc_github_identity_provider_setup.md`
  - align with `setup_environment_access.sh`;
  - keep manual OIDC instructions only if they remain useful as a fallback.

- `.github/prompts/intro.md`
  - update any agent-facing setup/deployment instructions so future AI work follows the new strategy.

Docs to review for deletion, consolidation, or archival:

- `docs/full_setup_plan.md`
- `docs/aws_organizations.md`
- `docs/gh_project_setup.md`
- `docs/github-pm-notes.md`
- `docs/lambda_vpc_s3_setup.md`
- `docs/working_files/aurora_data_api_migration.md`
- `scripts/deploy/NETWORK_DEPLOYMENT.md`
- `infra/resources/NETWORK_COST_ESTIMATE.md`
- older files under `docs/development_plans/`
- older cost, resource, and security report folders if they are no longer useful in the active repo docs

Deletion criteria:

- delete docs that describe commands, config fields, or workflows that no longer exist;
- delete docs that duplicate newer canonical docs without adding historical value;
- keep docs that are still useful as manual fallback procedures, but mark them clearly as fallback/manual;
- move historical planning material to a `docs/archive/` folder only if it may still explain decisions;
- avoid leaving obsolete docs in active docs paths where they can mislead future work.

Documentation acceptance criteria:

- a new developer can read `README.md` and know the recommended path;
- an operator can read `docs/deployment_process.md` and deploy either the default account workflow or an existing-account environment;
- `infra/README.md` explains every top-level `infra.yaml` section;
- `scripts/deploy/README.md` explains how to add a new resource without editing `deploy_all.sh`;
- no active doc tells users to mutate `infra.yaml` with account IDs, deployed ARNs, or generated resource IDs;
- no active doc describes `destroy_all.sh` as a supported path.

## Recommended First Implementation Slice

Start by locking the config model before editing every script.

1. Update `infra/infra.yaml` to the new forward-only structure.
2. Add `infra/secrets/accounts.secrets.yaml` template.
3. Add config resolver helpers.
4. Update `deploy_all.sh` to read resources in YAML order and honor `enabled`.
5. Create `scripts/deploy/resources/` and `scripts/deploy/lib/`.
6. Move one simple handler, such as `s3_bucket`, and one complex handler, such as `rag_lambda`, to the new contract.
7. Add a `playground` environment that deploys to an existing account profile with `ci_cd.enabled: false`.
8. Run a deploy summary and teardown summary without touching GitHub setup.

That validates the most important architectural split: setup can remain production-workflow oriented, while deployment becomes flexible and account-agnostic.
