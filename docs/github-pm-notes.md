# GitHub PM Project Notes

This file is used by the **github-pm** slash command to avoid repeated API lookups. The agent reads it at the start of each run and updates it when new information is learned or when lookups fail.

**Last updated:** 2026-02-23

---

## Project

- **Project name:** Chat Template Project
- **Project ID (V2):** *(e.g. `PVT_xxxx` — fill in when known from a successful run)*
- **Repository:** *(owner/repo from infra or git remote)*

---

## Field Definitions (from development_process.md)

Reference only; exact option names/IDs in the project may differ.

- **Status:** Backlog, Ready, In Progress, In Review, Complete
- **Type:** Feature, Bug, Task, Epic, Initiative
- **Priority:** P0, P1, P2, P3

---

## Resolved IDs (optional)

When the agent or scripts resolve field/option IDs via the API, record them here so future runs can skip lookups. Example:

| Field   | Option/Value | ID (if known) |
|---------|--------------|---------------|
| Status  | Backlog      |               |
| Status  | In Progress  |               |
| Type    | Feature      |               |

---

## Session Notes

- **2026-02-23 (issue #115):** Status labels in the project were updated from "In progress"/"In review" to "In Progress"/"In Review". Command doc (`.cursor/commands/github-pm.md`) and setup doc (`docs/gh_project_setup.md`) updated to use the new capitalization. Workflow `update-project-on-merge-to-dev.yml` already matches by `toLowerCase() === 'in review'`, so no change needed there.
