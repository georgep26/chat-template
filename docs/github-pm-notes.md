# GitHub PM Project Notes

This file is used by the **github-pm** slash command to avoid repeated API lookups. The agent reads it at the start of each run and updates it when new information is learned or when lookups fail.

**Last updated:** (updated by agent when notes change)

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

- *(Agent appends brief notes here when a command fails or when new project info is discovered.)*
