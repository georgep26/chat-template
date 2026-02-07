# Repository Security Review (Public Template / PII & Secrets)

Perform a thorough security review of this repository so it remains safe to use as a **public GitHub template**: no personally identifiable information (PII), credentials, or account-specific data should be committed. Output the results to `docs/security_reports/security_report_YYYY_M_D.md` using today’s date.

## Approach

Follow these steps in order. Use search (grep, codebase search) and file reads; do not guess. Record findings in the report with **Result** (PASS / FINDING / CAUTION), **Location**, **Details**, and **Recommendation** where applicable.

### 1. Credentials and secrets

- Search the repo for:
  - GitHub tokens: `ghp_`, `gho_`, `github_pat_`
  - AWS keys/IDs: `AKIA`, `AIDA`, `AROA`
  - Generic patterns: `secret`, `password`, `api_key`, `apikey`, `token`, `credential` (case-insensitive)
- Confirm GitHub Actions workflows use only `secrets.*` or environment variables for sensitive values (no hardcoded passwords, URIs with secrets, or tokens in YAML).
- Confirm any app/config templates use placeholders only (e.g. `<account-id>`, `your-value-here`) and that real config files are listed in `.gitignore` or documented as S3-only.
- Review `.gitignore` and `.cursorignore` for: `.env`, `*.env`, `secrets`, `creds`, `app_config.yaml`, `config/local.yaml`, `*.pem`, `*.key`, and local notes (e.g. `working_notes.md`).

### 2. AWS account IDs and ARNs

- Search for AWS account ID patterns (e.g. `aws_account_id`, `account-id`, `ACCOUNT_ID`, 12-digit IDs) and ARNs containing 12-digit account IDs.
- Verify that any account IDs in the repo are either:
  - The standard AWS example `123456789012`, or
  - Placeholders like `ACCOUNT_ID`, `<account-id>`, or `account-id` in docs/templates.
- If any script uses the account ID at runtime (e.g. from AWS CLI), confirm it is not hardcoded.

### 3. Personally identifiable information (PII)

- **Still check for personal information** across the codebase (docs, config examples, README, scripts). Flag any PII that could identify individuals or organizations and should be replaced with generic placeholders.
- **Accepted as-is (do not flag as findings):**
  - **LICENSE** — Author/copyright name in the LICENSE file is acceptable.
  - **Personal GitHub usernames/repos** — References such as `georgep26` or personal repo URLs in `.cursor/commands/` or elsewhere are acceptable. Do not recommend removing or genericizing these.
- **Do flag as findings:** Organization or company names used as examples in docs (e.g. naming convention docs, README examples). These should use generic placeholders (e.g. `myorg`, `your-org`).
- Also search for: email addresses (regex for `...@...`); real names in docs or config (other than LICENSE). For each hit outside the accepted list above, decide whether it should be replaced with a placeholder.

### 4. Workflow and script safety

- Ensure no workflow passes secrets via `workflow_dispatch` inputs; they should use environment/repository secrets only.
- Check deploy scripts for `echo`, `print`, or `log` of passwords, ARNs, or tokens. Prefer not logging full secret ARNs in non-debug paths, or redact them.
- Confirm branch names and repo references in workflows are generic (e.g. `main`, `development`) and not org-specific.

### 5. Evaluation and test data

- If the repo contains evals, test fixtures, or sample data (e.g. CSVs), spot-check for PII or customer-specific content. Ensure only generic or synthetic data is committed.

### 6. Local-only and notes files

- Confirm files that may contain internal notes or PII (e.g. `working_notes.md`, `feature_ideas.md`) are in `.gitignore` and document that they must not be committed.
- If such files were ever committed, note in the report that history cleanup may be needed.

## Report structure

Write the report to **`docs/security_reports/security_report_YYYY_M_D.md`** (use today’s date). Include:

1. **Executive summary** — Overall assessment and critical findings.
2. **Sections** — One section per area above (credentials, account IDs, PII, workflows/scripts, evals/data, local files). For each:
   - **Result:** PASS / FINDING / CAUTION
   - **Location:** File or path
   - **Details:** What was found
   - **Recommendation:** What to change, if anything
3. **Summary of actions** — Table of recommended and optional fixes.
4. **Checklist for future reviews** — Short checklist so the same approach can be repeated.

## After the review

- Do not modify code or docs unless the user asks; the main deliverable is the report.
- If the user wants fixes, they can request them separately (e.g. “apply the recommended changes from the security report”).

## Reference

This process was used for the first review on 2026-02-07; the report is in `docs/security_reports/security_report_2026_2_7.md`. Use that report as a format and depth reference.

## Project policy (PII)

- **LICENSE:** Author/copyright name is acceptable; do not flag.
- **Personal GitHub (e.g. georgep26):** Acceptable in Cursor commands or elsewhere; do not flag.
- **Org/company names in docs:** Use generic placeholders (e.g. `myorg`); flag if a specific org name appears.
- Continue to check for other personal information elsewhere in the code.
