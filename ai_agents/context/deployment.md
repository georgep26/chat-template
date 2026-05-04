# Deployment Context Index

Use this index before deployment, infrastructure, AWS, CI/CD, or config changes.

Read:

- `AGENTS.md` for global agent rules.
- `docs/deployment_process.md` for setup and deployment tracks.
- `docs/config_structure.md` for committed config, secrets files, and hydration.
- `scripts/deploy/README.md` for deploy orchestration and resource handler conventions.
- `docs/aws_naming_convention.md` for AWS naming and suffix rules.
- `docs/github_environment_secrets.md` for GitHub environment secret setup.
- `docs/oidc_github_identity_provider_setup.md` for manual GitHub OIDC setup.
- `infra/infra.yaml` for the actual resource list, deployment sets, environments, and ownership.

Key rules:

- Do not hardcode account IDs, regions, ARNs, generated resource IDs, or secret file values.
- Resource order comes from `infra/infra.yaml`.
- Deployment sets filter enabled resources without changing order.
- Resource handlers live under `scripts/deploy/resources/<resource_key>.sh`.
- Generated reports belong in `docs/resource_reports/` or `docs/working_files/` depending on report type.
