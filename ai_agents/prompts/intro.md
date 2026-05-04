# Intro Prompt

Use this prompt when the user invokes the intro workflow or asks for project onboarding.

## Required Reading

Read:

- `AGENTS.md`
- `ai_agents/context/project.md`
- `README.md`
- `CHANGELOG.md`
- `docs/development_process.md`
- `docs/github_standards.md`
- `docs/deployment_process.md`
- `docs/config_structure.md`
- `docs/code_standards.md`

For deployment-heavy onboarding, also read:

- `ai_agents/context/deployment.md`
- `scripts/deploy/README.md`
- `docs/aws_naming_convention.md`

## Response Goals

Provide a concise onboarding walkthrough for this backend/infrastructure repo.

Cover:

- What the RAG chat application does.
- Where the main backend Lambda code lives.
- How config and deployment are organized.
- How local setup, tests, evals, and deploy scripts are normally run.
- Where development plans and generated reports should go.
- Which docs to read next for the user's likely task.

Avoid:

- Calling this repo a generic Python template.
- Duplicating full documentation content.
- Discussing the sibling frontend repo unless the user asks.

End by offering concrete next steps based on the user's stated goal.
