# Submit PR Prompt

Use this prompt when the user asks to create, prepare, or submit a pull request.

## Required Reading

Read:

- `AGENTS.md`
- `docs/github_standards.md`
- `docs/development_process.md`
- `.github/pull_request_template.md`

## Workflow

1. Identify the target branch. If ambiguous, ask the user.
2. Inspect the current branch and working tree:

   ```bash
   git status --short
   git branch --show-current
   ```

3. Verify GitHub CLI availability and authentication when PR creation is requested:

   ```bash
   command -v gh
   gh auth status
   ```

4. Compare the current branch to the target branch:

   ```bash
   git log --format="%H|%ai|%s|%b" --no-merges <target-branch>..HEAD
   git diff --name-status <target-branch>..HEAD
   git diff --stat <target-branch>..HEAD
   ```

5. Fill `.github/pull_request_template.md` using actual commits and diffs.
6. For `development` to `main` release PRs, ask the user for the release tag. Do not guess.
7. Use issue-linking rules from `docs/github_standards.md`.
8. Do not include AI tool attribution.
9. If a PR already exists, report it or update it instead of creating a duplicate.

## Create PR

Use:

```bash
gh pr create --base <target-branch> --head <current-branch> --title "<title>" --body-file <temp-file>
```

Clean up temporary files after use.

## Final Response

Return:

- PR URL, if created
- target branch
- summary of included changes
- tests or checks observed
- any skipped verification
