# GitHub Standards

This document is the shared source of truth for Git, commit-message, pull request, changelog, and GitHub Project practices. It is intended for both humans and coding agents.

## Standard Git Practice

- Run `git status --short` before committing, creating a pull request, or summarizing changes.
- Review the relevant diff before writing commit messages or PR descriptions.
- Distinguish your own changes from unrelated user changes in the working tree.
- Do not revert unrelated changes unless the user explicitly asks.
- Keep commits focused on the requested task.
- Do not include generated reports, local secrets, hydrated config values, or working files unless the user explicitly asks and the file is intended to be tracked.
- Never commit files under gitignored secrets paths such as `infra/secrets/**/accounts.secrets.yaml` or `infra/secrets/**/*.secrets.yaml`.
- Avoid destructive Git operations such as `git reset --hard`, `git checkout --`, branch deletion, or force pushes unless the user explicitly asks.

## Branch and PR Flow

The standard development flow is:

```text
feature/hotfix branch -> pull request to development -> QA -> pull request to main
```

Hotfixes can go directly to `main` when needed, then `main` should be merged back to `development`.

See [development_process.md](development_process.md) for the complete workflow, GitHub Project fields, release flow, and review expectations.

## Commit Messages

Use conventional commit style with this format:

```text
<type>: <concise, lowercase description>
```

Allowed types:

| Type | Use for |
| --- | --- |
| `feature` | New functionality |
| `fix` | Bug fixes |
| `docs` | Documentation changes |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or updating tests |
| `chore` | Build or maintenance changes |
| `performance` | Functional or compute performance improvements |

Rules:

- Use only the allowed types above.
- Use lowercase for the entire subject line.
- Keep the subject under 15 words.
- Do not end the subject with punctuation.
- Avoid unnecessary adjectives; focus on what changed.
- Do not include ticket IDs, emojis, tool attribution, or extra formatting in the subject.
- If the change is incompatible or breaking, add a body section beginning with `BREAKING CHANGE:` and describe the required follow-up.

Examples:

```text
feature: adding metadata extraction lambda
fix: resolving batch job timeout
refactor: reorganizing dynamodb query helpers
docs: updating deployment instructions
test: adding unit tests for lambda concurrency
chore: cleaning deployment scripts

feature: updating api response schema

BREAKING CHANGE: endpoint /v1/listings now requires listing_id
```

## Pull Requests

- Use `.github/pull_request_template.md` when creating a PR.
- Base PR titles on the primary change and keep them concise.
- Use the target branch requested by the user; ask if it is ambiguous.
- For `development` to `main` release PRs, ask the user for the release tag. Do not guess.
- In PR descriptions, summarize:
  - What changed.
  - Why it changed, if that is not obvious from the diff.
  - Tests, evaluations, dry runs, or deployment previews performed.
  - Any skipped verification and why.
- Do not add "Created by Cursor", "Made by Cursor", "Created by Claude", "Created by Codex", or similar AI attribution.
- If a PR already exists for the branch, update or report it instead of creating a duplicate.

## Linking GitHub Issues

When a PR targets `development`, link issues in the PR body so `.github/workflows/update-project-on-merge-to-dev.yml` can move them to `In Review` after merge.

Supported patterns:

```text
Closes #123
Fixes #123
Resolves #123
Related to: #123
```

For multiple issues, repeat the keyword for each issue:

```text
Closes #11
Closes #13
Closes #20
```

Do not use `Closes #11, #13, #20`; only the first issue may be parsed.

Linked issues should exist in the GitHub Project named `Chat Template Project`.

## Changelog Updates

When updating `CHANGELOG.md` from commit history:

- Exclude merge commits.
- Group related commits into user-facing bullets.
- Use Keep a Changelog categories.
- Add a `### Summary` section for a release entry.
- Add `### Breaking Changes` when commit bodies include `BREAKING CHANGE:`.

Commit type mapping:

| Commit type | Changelog category |
| --- | --- |
| `feature` | `Added` |
| `fix` | `Fixed` |
| `docs` | `Added` or `Changed`, depending on context |
| `refactor` | `Changed` |
| `performance` | `Changed` |
| `test` | Usually omit unless test infrastructure changed |
| `chore` | Usually omit unless build or maintenance behavior changed |

## GitHub Project Standards

- Follow the project workflow in [development_process.md](development_process.md).
- Use issue templates under `.github/ISSUE_TEMPLATE/`.
- Default new issues to `Backlog` unless the user specifies otherwise.
- Use the project's standard issue types, statuses, and priorities from `docs/development_process.md`.
- Do not close, delete, or reorganize project issues without explicit user confirmation.
- If a GitHub Project command discovers useful project IDs, field names, or options, record them in the local project notes file if that workflow uses one.
