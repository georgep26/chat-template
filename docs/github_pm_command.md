# github-pm Slash Command

The **github-pm** slash command is an agentic project manager that uses the GitHub Project API and follows the workflow in [development_process.md](development_process.md). All features—including creating issues—are accessed through this single command; the agent routes intent and calls `scripts/github-projects/gh-pm` as needed.

## Prerequisites

- **gh** installed and authenticated with project scope. The agent runs `gh-pm check-env` and, if needed, instructs you to run `gh auth login` and `gh auth refresh -s project`.
- The script resolves the repository from `infra/infra.yaml` or the git remote.

## Actions

| Action | Subcommand | Description |
|--------|------------|-------------|
| check | `check-env` | Verify gh and auth; remediate if missing. |
| create | `create-issue` | Add a single issue (default: Backlog); uses templates in `.github/ISSUE_TEMPLATE/`. |
| breakdown | `breakdown-outline` | Turn an outline into proposed sub-issues; run duplicate check; confirm with user before creating. |
| cleanup | `cleanup-audit` | Report duplicates, orphaned, and stale issues; ask before any delete/reorganize. |
| fetch-sprint | `fetch-current-sprint` | Export current sprint issues as JSON to `docs/github_project_issues/`. |
| weekly-report | `weekly-report` | Generate report to `docs/github_project_reports/YYYY-MM-DD_weekly_report.md`. |
| discussions | `discussions-to-issues` | List discussions, propose as issues; create only after user confirmation. |

## Usage from Cursor

Invoke the slash command (e.g. type `github-pm` or “project manager”) and describe what you want, for example:

- “Check that gh is set up”
- “Add an issue: Add dark mode”
- “Break this into sub-issues: [paste outline]”
- “Clean up the project / find duplicates and stale issues”
- “Fetch current sprint issues as JSON”
- “Generate the weekly report”
- “Turn discussions into issues”

The agent will run the appropriate `gh-pm` subcommand (with `required_permissions: ["all"]` for GitHub API access) and follow the confirmation gates described in [.cursor/commands/github-pm.md](../.cursor/commands/github-pm.md).

## Creating issues

Issue creation is the **create** action. Invoke github-pm and describe what you want in natural language. Examples:

- “Add support for multiple AWS regions in the deployment script”
- “The deploy script fails when VPC ID is not set”
- “Update the deploy_network.sh script to write VPC ID and subnet IDs to infra.yaml”

The agent will:

- **Categorize** the request as Feature, Bug, or Task (or Epic/Initiative if you specify)
- **Build** a title and body that follow the template in `.github/ISSUE_TEMPLATE/` (feature_request.md, bug_report.md, task.md)
- **Run** `gh-pm create-issue` (which uses `scripts/github-projects/gh-issue-create`)
- **Add** the issue to the “Chat Template Project” and set project fields (Type, Status, Parent, etc.)

### Categorization

- **Feature**: New functionality, enhancements, improvements
- **Bug**: Something broken, errors, unexpected behavior
- **Task**: Refactoring, documentation, maintenance, script/config updates

For Epics or Initiatives, say so explicitly (e.g. “create an Epic for …”) so the title gets the correct prefix `[EPIC]` or `[INITIATIVE]`.

## Script reference

- Command contract: [.cursor/commands/github-pm.md](../.cursor/commands/github-pm.md)
- Script and subcommands: [scripts/github-projects/README.md](../scripts/github-projects/README.md)
