# Auto Chore Updates to Main

This repo keeps branch protection on `main` (PR required, required status check **Run Tests**) while allowing automated chore PRs to update `main` without running the full test suite or using a bypass token. Chore PRs satisfy the required check via a dedicated status workflow, then are merged with `GITHUB_TOKEN`.

## How it works

1. A workflow creates a **chore PR** from a bot branch (e.g. `bot/evals-results`, `bot/release-changelog`, `bot/aggregate-evals`) that only touches allowlisted paths.
2. **Chore PR Status** (`.github/workflows/auto_chore_pr_status.yml`) runs for PRs that touch those paths. It posts **Run Tests** = success only when the PR head branch is an allowlisted bot branch and **all** changed files are under the allowlisted paths.
3. Branch protection sees Run Tests = success, so the PR is mergeable.
4. The creating workflow waits for that status, then merges the PR using **GITHUB_TOKEN**.

No bypass list or PAT/App token is required for these chore PRs.

## Allowed chore paths

Only these paths qualify for the lightweight Run Tests pass:

- `evals/eval_outputs/**` — eval run outputs
- `CHANGELOG.md` — release changelog updates
- `docs/pr_evals_results/**` — aggregated eval results

## Allowed bot branches

The Chore PR Status workflow posts Run Tests success only for PRs whose **head** branch is one of:

- `bot/evals-results`
- `bot/release-changelog`
- `bot/aggregate-evals`

PRs from any other branch (e.g. `development`) do not get the auto-pass even if they only touch chore paths; they go through the normal PR workflow and full tests.

## Workflows that use this pathway

| Workflow | Bot branch | Paths |
|----------|------------|--------|
| Run Evaluations (upload_to_main) | `bot/evals-results` | `evals/eval_outputs/**` |
| Create Release on Merge to Main | `bot/release-changelog` | `CHANGELOG.md` |
| Aggregate Evaluation Results | `bot/aggregate-evals` | `docs/pr_evals_results/**` |

## Merge flow

Each of these workflows:

1. Creates a PR with `peter-evans/create-pull-request` to `main` from the corresponding bot branch.
2. Polls the commit status API for the PR head SHA until **Run Tests** is `success` (up to ~4 minutes).
3. Runs `gh pr merge <number> --squash` with `GITHUB_TOKEN`.

If the status does not appear in time, the step exits without failing and the PR remains open for manual merge.

## Recursion protection

Push-triggered workflows use `paths-ignore` so that merges that only add chore outputs do not re-trigger:

- **Deploy to Prod on Merge**: `paths-ignore` includes `evals/eval_outputs/**`, `docs/pr_evals_results/**`, and `CHANGELOG.md`.
- **Create Release on Merge**: same `paths-ignore` so chore-only merges (eval results, aggregated docs, or CHANGELOG) do not start another release run.

The main **Pull Request** workflow uses `paths-ignore: evals/eval_outputs/**` so chore PRs do not run tests or staging deploy.

## Troubleshooting

- **PR stays open** — Chore PR Status may not have run (check it’s triggered by the path and that the head branch is allowlisted). Merge the PR manually if the diff is correct.
- **Run Tests never passes** — Ensure the PR only changes allowlisted paths and the head branch is exactly one of the three bot branches.
- **Required check missing** — Branch protection expects a check named **Run Tests**. The Chore PR Status job uses that exact context; do not rename it without updating branch protection.
