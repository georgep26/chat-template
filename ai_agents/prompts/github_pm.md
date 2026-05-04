# GitHub Project Manager Prompt

Use this prompt when the user asks for GitHub Project management, issue creation, sprint reporting, cleanup, or the `github-pm` workflow.

## Required Reading

Read:

- `AGENTS.md`
- `docs/github_standards.md`
- `docs/development_process.md`
- `docs/github_pm_command.md`, if present and relevant
- `.github/ISSUE_TEMPLATE/`
- `scripts/github-projects/gh-pm`

If `docs/github-pm-notes.md` exists locally, read it at the start and update it with newly discovered project metadata at the end.

## Workflow

1. Run environment checks before GitHub API actions:

   ```bash
   ./scripts/github-projects/gh-pm check-env
   ```

2. Route the user's request to one primary action:
   - check environment
   - create issue
   - break down an outline into issues
   - cleanup audit
   - fetch current sprint
   - weekly report
   - discussions to issues
3. Use issue templates and project field definitions from the docs.
4. Ask for confirmation before closing, deleting, reorganizing, or bulk-mutating issues.
5. Use `docs/github_standards.md` for PR issue-linking and GitHub workflow standards.
6. Report script output and saved report paths clearly.

## Output Locations

- Durable development plans go in `docs/development_plans/`.
- Generated project reports should follow the script's documented output path.
- Local project metadata notes should remain local if gitignored.

Do not add AI tool attribution to issues, PRs, or reports.
