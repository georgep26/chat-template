# Security Review Prompt

Use this prompt when the user asks for a repository security review, public-template safety check, secret scan, or PII/account-data review.

## Required Reading

Read:

- `AGENTS.md`
- `docs/github_standards.md`
- `docs/config_structure.md`
- `docs/github_environment_secrets.md`
- `.gitignore`
- `.cursorignore`, if present

## Review Scope

Focus on whether the repo is safe to use as a public GitHub template.

Check for:

- credentials and tokens
- AWS keys, account IDs, and ARNs
- real secrets in config or workflow files
- hardcoded passwords, API keys, or database credentials
- organization names, emails, or PII that should be placeholders
- generated state, reports, local notes, or hydrated config that should be gitignored
- workflow patterns that pass secrets through manual inputs instead of GitHub secrets
- deploy scripts that log sensitive values

Accepted as-is unless the user asks otherwise:

- author/copyright information in `LICENSE`
- personal GitHub usernames or public personal repo references in command docs

## Report Location

Write generated security reports to `docs/security_reports/` unless the user explicitly asks for a different location.

Use a dated filename:

```text
docs/security_reports/security_report_YYYY_M_D.md
```

Create the folder if needed.

## Report Format

Include:

- executive summary
- findings grouped by area
- result labels: `PASS`, `FINDING`, or `CAUTION`
- file locations
- recommendations
- short future-review checklist

Do not modify code or docs unless the user asks for fixes.
