# Resource Reports

This directory is for generated reports that summarize deployed AWS resources.

Use the current deployment report command:

```bash
./scripts/deploy/report_resources.sh <environment>
./scripts/deploy/report_resources.sh <environment> --json
```

The report reads `infra/infra.yaml`, checks declared resource stacks, and flags likely orphan CloudFormation stacks that match the project/environment naming pattern.

Generated report files should generally stay local unless there is a specific reason to commit a point-in-time snapshot. For broader coding-agent reports, use `docs/working_files/`.
