# Submit Pull Request (submit-pr) Command

When the user invokes the **submit-pr** slash command, you help them create and submit a pull request by analyzing their branch changes, filling out the PR template, and opening the PR to the target branch.

## Prerequisites

1. **GitHub CLI installed and authenticated**: The command uses `gh` CLI to create PRs. Verify with `gh auth status` or run `gh auth login` if needed.
2. **Current branch**: The command works on the current git branch. Ensure you're on the branch you want to submit.
3. **Target branch**: The user specifies the target branch (e.g., "development", "main"). The command validates the target branch exists.

## Process Flow

### 1. Parse User Intent
- Extract the target branch from the user's message (e.g., "submit a PR to development" → target: `development`)
- If ambiguous, ask the user to clarify the target branch
- Get the current branch using `git branch --show-current`

### 2. Validate Environment
- Check that `gh` is installed: `command -v gh`
- Check that `gh` is authenticated: `gh auth status`
- Verify current branch exists and has commits
- Verify target branch exists: `git ls-remote --heads origin <target-branch>`
- If any check fails, inform the user and stop

### 3. Analyze Changes
Gather information about the changes:

**a. Get commit history:**
```bash
git log --format="%H|%ai|%s|%b" --no-merges <target-branch>..HEAD
```
This provides commit hash, date, subject, and body for analysis.

**b. Get changed files:**
```bash
git diff --name-status <target-branch>..HEAD
```
This shows which files were added, modified, or deleted.

**c. Get file statistics:**
```bash
git diff --stat <target-branch>..HEAD
```
This provides a summary of lines changed.

### 4. Analyze and Categorize Changes
Review the commit messages and file changes to determine:
- **Type of Change**: Feature, Bug Fix, Task, Documentation Update, or Other
  - Look for keywords in commit messages: `feature:`, `fix:`, `bug:`, `docs:`, `refactor:`, `chore:`, `task:`
  - Check file paths: `.md` files → Documentation, `test_` → Testing, etc.
- **Summary**: Generate a concise summary (1-2 sentences) based on commit messages
- **Changes Made**: List specific changes based on commits and file changes
- **Related Issues**: Extract issue numbers from commit messages (e.g., `#123`, `closes/Closes #456`, `fixes/Fixes #789`)
- **Release Information**: Determine if this is a release PR (target is `main` and source is `development`)

### 5. Prompt for Release Tag (if applicable)
**CRITICAL**: If this is a release PR (`development → main`):
- **Prompt the user** for the release tag before proceeding
- Ask: "This appears to be a release PR (development → main). What release tag should be used? (e.g., v1.0.0, v1.2.3)"
- **DO NOT** guess or infer the release tag from commits or version numbers
- **DO NOT** leave a placeholder like `v1.0.0` - either get it from the user or leave it empty
- Wait for user confirmation before proceeding to template filling
- If the user doesn't provide a tag, leave the Release Tag field empty in the template

### 6. Fill Out PR Template
Read `.github/pull_request_template.md` and fill it out intelligently:

- **Summary**: Brief description based on commit analysis
- **Release Information**: 
  - If this is `development → main`, mark the checkbox as checked
  - **Release Tag**: Use the tag provided by the user in step 5, or leave empty if not provided
  - **Milestone**: Leave empty (user can fill if needed)
  - **NEVER** guess or infer release tags - always ask the user or leave empty
- **Type of Change**: Check the appropriate box based on analysis
- **Related Issues**: List any issue numbers found in commits
- **Changes Made**: Bullet points of key changes from commits
- **Testing**: Check boxes based on:
  - Presence of test files in changes
  - Commit messages mentioning tests
  - File changes in test directories
- **Documentation**: Check boxes based on:
  - `.md` file changes
  - `README.md` updates
  - Code comments/docstrings (infer from code changes)
- **Checklist**: Leave unchecked (user fills before merge)
- **Screenshots/Examples**: Leave empty
- **Additional Notes**: Add any relevant context from commit messages

### 7. Generate PR Title
Create a concise PR title based on:
- Primary commit message (first non-merge commit)
- Type of change
- Key feature/fix being introduced

Examples:
- "Feature: Add dark mode support"
- "Fix: Resolve login button issue"
- "Docs: Update API documentation"
- "Task: Refactor configuration management"

### 8. Create the Pull Request
Use GitHub CLI to create the PR:

```bash
gh pr create \
  --base <target-branch> \
  --head <current-branch> \
  --title "<PR Title>" \
  --body-file <temp-file-with-filled-template>
```

**Important**: 
- Use `required_permissions: ["all"]` when calling `run_terminal_cmd` so `gh` can access GitHub API
- Create a temporary file with the filled template content
- Clean up the temporary file after PR creation

### 9. Report Results
- Show the PR URL returned by `gh pr create`
- Summarize what was included in the PR (type, changes, issues)
- Remind user to review and adjust the PR description if needed

## Example Usage

**User**: "submit-pr to development"
**Process**:
1. Current branch: `ci_cd`
2. Target branch: `development`
3. Analyze commits: `git log development..ci_cd`
4. Fill template with changes
5. Create PR: `gh pr create --base development --head ci_cd --title "..." --body-file ...`
6. Return PR URL

**User**: "submit a PR to main"
**Process**:
1. Current branch: `feature/new-feature`
2. Target branch: `main`
3. Analyze commits
4. Detect if this is `development → main` (release PR)
5. **Prompt user for release tag** if it's a release PR
6. Fill template with provided release tag (or leave empty)
7. Create PR

## Error Handling

- **No changes**: If `git diff` shows no changes, inform user and stop
- **Branch doesn't exist**: If target branch doesn't exist, suggest valid branches
- **Not authenticated**: Guide user to run `gh auth login`
- **Merge conflicts**: Check for conflicts with `git merge-base --is-ancestor <target> HEAD` and warn if needed
- **Already exists**: Check if PR already exists with `gh pr list --head <current-branch> --base <target-branch>`

## Best Practices

- **Commit message analysis**: Use conventional commit format when possible
- **Concise summaries**: Keep PR summary to 1-2 sentences
- **Specific changes**: List actual changes, not generic descriptions
- **Issue linking**: Always link related issues when found in commits
- **Release tags**: **NEVER** guess release tags - always prompt the user or leave empty
- **User review**: Always remind user to review the PR description before submitting

## When to Use This Command

- User says "submit-pr", "create PR", "open PR", "submit pull request", "PR to <branch>"
- User wants to create a PR from their current branch to a target branch
- User wants help filling out the PR template based on their changes

## When Not to Use

- User wants to review an existing PR (use normal chat)
- User wants to merge a PR (use GitHub UI or `gh pr merge`)
- User wants to update an existing PR (use `gh pr edit` or GitHub UI)
