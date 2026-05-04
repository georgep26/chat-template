# Coding Agent Instructions

This file is the canonical project entry point for coding agents working in this repository.

## Project Scope

- This repository is the `chat-template` backend and infrastructure repo for a RAG chat application.
- The sibling frontend repo is outside this repository. Ignore frontend code unless the user explicitly asks for frontend work.
- The main backend runtime is the RAG Lambda under `src/rag_lambda/`.
- Infrastructure and deployment are config-driven through `infra/infra.yaml` and scripts under `scripts/`.

## Required Context

Before making changes, read the relevant docs:

- `README.md` for project overview, setup, and directory structure.
- `docs/code_standards.md` before writing or modifying Python.
- `docs/github_standards.md` before Git, commit, changelog, PR, issue, or GitHub Project work.
- `docs/deployment_process.md`, `docs/config_structure.md`, `docs/aws_naming_convention.md`, and `scripts/deploy/README.md` before deployment or infrastructure changes.
- `docs/frontend_interface.md` only when working on backend/frontend interface contracts.

## Agent Prompt Library

- Shared agent prompts live under `ai_agents/prompts/`.
- Agent context indexes live under `ai_agents/context/`.
- Tool-specific commands or skills should be thin adapters that point to the shared prompts.
- Do not duplicate durable project facts in `ai_agents`; point to `docs/` instead.

## Development Plans and Reports

- Durable development plans must be written to `docs/development_plans/`.
- Development plan filenames must include a date, for example `docs/development_plans/2026-05-04_ai_agent_strategy_plan.md`.
- Do not use tool-specific plan folders, such as `.cursor/plans`, as the durable source of truth.
- Generated coding-agent reports and working notes belong under `docs/working_files/`, which is gitignored except for its README.
- Resource status reports belong under `docs/resource_reports/`.

## Deployment Rules

- `infra/infra.yaml` is the deployment source of truth.
- Do not hardcode account IDs, account names, regions, secret paths, generated ARNs, resource names, or deployment order.
- Account values live in the configured `secrets.accounts_file`.
- Environment/runtime secrets live in each `environments.<env>.secrets_file`.
- Real secrets and account-specific values must stay out of committed files.
- Resource order is the order in `infra/infra.yaml`.
- Deployment sets in `infra/infra.yaml` define which resources a deployment should touch.
- `deploy_all.sh` resolves resource handlers by convention: `resources.<resource_key>` maps to `scripts/deploy/resources/<resource_key>.sh`.
- Adding a resource should normally require a `resources.<resource_key>` entry and a matching handler, not edits to `deploy_all.sh`.
- Imported resources marked `ownership: imported` are skipped during teardown.

## Coding Practice

- Match existing project patterns before adding new abstractions.
- Keep changes scoped to the user's request.
- Do not rewrite unrelated docs or refactor unrelated code while completing a focused task.
- Treat a dirty worktree as normal. Identify your changes and do not revert unrelated user changes.
- Use `rg` for searching where available.
- Use `apply_patch` for manual file edits.
- Run relevant tests, lint checks, or deployment dry runs when practical.
- If verification is skipped, explain why in the final response.

## Git and GitHub

- Follow `docs/github_standards.md`.
- Inspect `git status --short` before commits, PRs, changelog updates, or change summaries.
- Review relevant diffs before generating commit messages or PR descriptions.
- Do not include tool attribution in commits or PRs.

## Public Repository Safety

- This repo is intended to remain safe as a public template.
- Do not commit real credentials, account IDs, account names, internal notes, generated state, or hydrated config values.
- Use placeholders in committed config and keep private values in gitignored secrets files or GitHub environment secrets.
