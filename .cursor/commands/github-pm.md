# GitHub Project Manager (github-pm) Command

When the user invokes the **github-pm** slash command, you act as an agentic project manager that uses the GitHub Project API and follows the development process. All features are accessed through this single command; you route the user's intent to the right action and call the `gh-pm` script as needed.

## Prerequisites and Process

1. **Environment check**: Before any GitHub operation, run the check-env subcommand. If `gh` is not installed or not authenticated (including project scope), show the user how to fix it and stop until they confirm.
2. **Process adherence**: You must follow the workflow and field definitions in `docs/development_process.md`. Issue types (Feature, Bug, Task, Epic, Initiative), statuses (Backlog, Ready, In Progress, In Review, Complete), and priorities (P0–P3) are defined there. New issues default to Backlog unless the user specifies otherwise.
3. **Templates**: All created issues must follow the templates in `.github/ISSUE_TEMPLATE/` (feature_request.md, bug_report.md, task.md). Use the script's create flow so template structure and title prefixes are applied.

## Supported Actions

Route the user's request to exactly one primary action. If the user's message is ambiguous, ask which action they want.

| Action | Subcommand | When to use |
|--------|------------|-------------|
| **check** | `check-env` | User wants to verify gh is installed and authenticated, or before any other action. |
| **create** | `create-issue` (via gh-issue-create) | User wants to add a single issue to the project (default: Backlog). |
| **breakdown** | `breakdown-outline` | User provides an outline of features/subtasks and wants them turned into sub-issues. |
| **cleanup** | `cleanup-audit` | User wants to review project for duplicates, orphaned issues, and stale issues. |
| **fetch-sprint** | `fetch-current-sprint` | User wants the current sprint's issues as JSON for other agents/tools. |
| **weekly-report** | `weekly-report` | User wants a weekly status report (blockers, Epic/Initiative rollup) saved to docs. |
| **discussions** | `discussions-to-issues` | User wants to review discussions and convert them to issues. |

## Action Flows and Confirmation Gates

### check (check-env)
- Run: `./scripts/github-projects/gh-pm check-env [--json]`
- If the script reports missing `gh` or auth: print the remediation (install gh, `gh auth login`, `gh auth refresh -s project`) and do not proceed with other actions until the user confirms the fix.

### create (create-issue)
Use this when the user asks to create an issue, describes work to track, or reports a problem to track. Do not use when the user is asking a question, wants to make changes directly, or is only reviewing existing issues.

1. **Parse the request**: Extract title and body from the user's natural language. Title should be concise and descriptive **without** a prefix (the script adds [FEATURE], [BUG], [TASK], [EPIC], or [INITIATIVE]).
2. **Categorize type** (Feature / Bug / Task):
   - **Feature**: New functionality, enhancements, improvements (e.g. "Add dark mode", "Implement user auth").
   - **Bug**: Something broken, errors, unexpected behavior (e.g. "Login button doesn't work", "API returns 500").
   - **Task**: Refactoring, documentation, maintenance, script/config updates (e.g. "Update deploy_network.sh", "Update README").
3. **Review templates** in `.github/ISSUE_TEMPLATE/` (feature_request.md, bug_report.md, task.md). Expand the body so **every section** of the chosen template is filled. Use `N/A` only for sections with no applicable content. Do not pad to a fixed bullet count; use as many bullets as the request needs.
4. **Extract project fields** from user text and pass as `--field "Name=Value"`:
   - Initiative/Epic: use `--type "Initiative"` or `--type "Epic"` so the title gets `[INITIATIVE]` or `[EPIC]`.
   - Priority: "priority P0" → `--field "Priority=P0"`.
   - Dates: "start date 2/17/26" → `--field "Start Date=2/17/26"`.
   - Status: "in progress" → `--field "Status=In progress"`.
   - Parent: "under Epic #42" or "parent #42" → `--field "Parent issue=#42"` (accepts `#123`, `123`, URL, or issue title).
   - You may pass multiple `--field` flags. The script adds the issue to the project and sets Type, Status, etc.
5. **Run**: `./scripts/github-projects/gh-pm create-issue <type> --title "..." --body "..." [--type Epic] [--type Initiative] [--field "Priority=P0"] [--field "Parent issue=#20"] ...`
   - This delegates to `scripts/github-projects/gh-issue-create`. Report the issue URL and project addition back to the user.
   - **CRITICAL**: Use `required_permissions: ["all"]` when calling run_terminal_cmd so `gh` can use the local auth session (sandbox blocks GitHub API otherwise).

### breakdown (breakdown-outline)
1. **Clarify**: If the user's outline is vague (e.g. "add some features for login"), ask for a concrete list of features and subtasks.
2. **Propose**: Generate a structured list of proposed child issues (title, type, brief description, optional parent). Output this as a clear list or table for the user.
3. **Duplicate check**: Run `./scripts/github-projects/gh-pm breakdown-outline --proposals-json '<...>' [--json]` if the script accepts proposals, or run a duplicate-check subcommand that returns existing open issues. Compare proposed titles/descriptions to existing issues.
4. **Confirm**: If any proposed issue is very similar to an existing one, list them and ask: "Should these be combined into one issue, or create a new one anyway?"
5. **Create**: After the user confirms (and optionally edits the list), create each issue by calling `create-issue` (or the script's create path). Default new issues to Backlog. Link children to the parent Epic/Initiative if specified.

### cleanup (cleanup-audit)
1. **Analyze**: Run `./scripts/github-projects/gh-pm cleanup-audit [--json]`. The script returns duplicates, orphaned issues, and stale issues (no update in >30 days).
2. **Summarize**: Present the summary in a short, readable form (counts and representative examples). Do not delete or reorganize anything yet.
3. **Confirm**: Ask the user: "Do you want to delete any of these, or reorganize (e.g. move to Backlog, set parent)?" For each category (duplicates, orphaned, stale), wait for explicit approval before suggesting or running destructive actions.
4. **Execute only after approval**: If the user approves specific actions, run the script again with the approved operations (e.g. close duplicates, update status). If the script does not support mutations, guide the user to do the changes in the UI or via `gh`, and never delete/close without confirmation.

### fetch-sprint (fetch-current-sprint)
- Run: `./scripts/github-projects/gh-pm fetch-current-sprint [--json]`
- The script writes JSON to `docs/github_project_issues/` with a deterministic filename (e.g. date + sprint id). Tell the user the path and that other agents can read this file.

### weekly-report (weekly-report)
- Run: `./scripts/github-projects/gh-pm weekly-report`
- The script writes a markdown report to `docs/github_project_reports/YYYY-MM-DD_weekly_report.md`. Summarize for the user: blockers, and how issues roll up to Epics and Initiatives.

### discussions (discussions-to-issues)
1. **Fetch**: Run `./scripts/github-projects/gh-pm discussions-to-issues [--dry-run] [--json]` to list recent discussions and proposed issue drafts.
2. **Summarize**: Show the user the list of discussions and the proposed issue title/type for each.
3. **Confirm**: Ask which discussions (or all) should be converted to issues. Do not create issues until the user confirms.
4. **Create**: Re-run without `--dry-run` for the selected items, or call create-issue for each approved conversion.

## Standard Response Format from Scripts

- Scripts may output **JSON blocks** (when run with `--json`) for machine use. When present, you may paste the path or a short summary for the user.
- Scripts also produce **human-readable** messages. Surface these to the user (e.g. "Sprint exported to ...", "Cleanup found N duplicates...").
- On script failure, show the script's stderr/stdout and suggest the next step (e.g. run check-env, check repo/project name).

## Sandbox and Permissions

- **All GitHub operations**: When running `gh-pm` or `gh-issue-create`, use `required_permissions: ["all"]` in run_terminal_cmd so `gh` can access the local auth session and network.

## When to Use This Command

- User says "github-pm", "project manager", "manage project", "check project", "sprint issues", "weekly report", "clean up issues", "break down this into issues", "add these to backlog", "discussions to issues", etc.
- User asks to create an issue and you want a single entry point: you can still route to create and follow the create flow above.

## When Not to Use

- User only wants to view or discuss issues without running any script (you can answer from context or suggest running fetch-sprint for data).
- User wants to edit code or docs unrelated to project management (use normal chat or other commands).
