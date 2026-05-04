# Deployed Resources Prompt

Use this prompt when the user asks for a deployed-resource report or wants to inspect AWS resources for an environment.

## Required Reading

Read:

- `AGENTS.md`
- `ai_agents/context/deployment.md`
- `scripts/deploy/README.md`
- `docs/resource_reports/README.md`
- `infra/infra.yaml`

## Workflow

1. Identify the target environment. If it is missing or ambiguous, ask the user for it.
2. Prefer the project reporting script instead of hand-written AWS queries:

   ```bash
   ./scripts/deploy/report_resources.sh <environment>
   ```

3. Use JSON only when the user or downstream tooling needs it:

   ```bash
   ./scripts/deploy/report_resources.sh <environment> --json
   ```

4. If AWS credentials or profile selection are needed, follow the account/profile values configured through `infra/infra.yaml` and the gitignored account secrets file. Do not hardcode account values.
5. Summarize:
   - declared resources found
   - missing resources
   - likely orphan stacks
   - failed or unusual statuses
   - report path, if a report was written

## Output Location

Resource reports belong under `docs/resource_reports/`.

Do not write account-specific investigation notes into committed docs unless the user explicitly asks for a trackable report.
