# Update Changelog Prompt

Use this prompt when the user asks to update `CHANGELOG.md` from commit history.

## Required Reading

Read:

- `AGENTS.md`
- `docs/github_standards.md`
- `CHANGELOG.md`

## Workflow

1. Determine the current changelog format and most recent release entry.
2. Inspect relevant commit history, excluding merge commits:

   ```bash
   git log --oneline --no-merges --decorate
   git log --format="%H|%ai|%s|%b" --no-merges <range>
   ```

3. Categorize changes using `docs/github_standards.md`.
4. Determine version bump from actual changes:
   - breaking change: major
   - feature: minor
   - fixes only: patch
5. Use today's date for the release entry.
6. Add a `### Summary` section.
7. Add only relevant Keep a Changelog sections.
8. Consolidate similar commits into user-facing bullets.
9. Preserve existing changelog content and formatting.

## Rules

- Do not include trivial commits unless they matter to users.
- Do not guess release intent if commit history is ambiguous; ask the user.
- If commit bodies include `BREAKING CHANGE:`, include a breaking changes section.
