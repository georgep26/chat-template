# Project Context Index

Use this index when an agent needs high-level project context.

Read:

- `AGENTS.md` for global coding-agent instructions.
- `README.md` for project overview, setup, and directory structure.
- `CHANGELOG.md` for recent changes.
- `docs/development_process.md` for the development workflow.
- `docs/github_standards.md` for Git, PR, changelog, and GitHub Project standards.
- `docs/code_standards.md` before Python code changes.

Important boundaries:

- This repo is the backend and infrastructure repo.
- The main runtime implementation is in `src/rag_lambda/`.
- Deployment configuration is centered on `infra/infra.yaml`.
- The sibling frontend repo should be ignored unless the user explicitly asks for frontend work.
