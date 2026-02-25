# Auto Chore Updates to Main

This repo keeps branch protection on `main` (PR required, required status check **Run Tests**) while allowing automated chore PRs to update `main` without running the full test suite or using a bypass token. Chore PRs satisfy the required check via a dedicated status workflow, then are merged with `GITHUB_TOKEN`.

## How it works

1. A workflow creates a **chore PR** from a bot branch (e.g. `bot/evals-results`, `bot/release-changelog`, `bot/aggregate-evals`) that only touches allowlisted paths.
2. **Run Tests** must be set to success so branch protection allows the merge. Because PRs created by `GITHUB_TOKEN` do not trigger other workflows (GitHub prevents recursive runs), the **creating workflow** sets the **Run Tests** commit status itself on the PR head commit, then merges with **GITHUB_TOKEN**.

No bypass list or PAT/App token is required for these chore PRs.

## Workflow permissions

For chore workflows to create and merge PRs with `GITHUB_TOKEN`, the repository must allow it:

1. In GitHub, go to **Settings** → **Actions** → **General**.
2. Under **Workflow permissions**, choose **Read and write permissions** so workflows have read and write access for all scopes.
3. Under **Pull request creation permissions**, check **Allow GitHub Actions to create and approve pull requests**.
4. Click **Save**.

Without these settings, chore workflows may fail when creating or merging PRs (e.g. "Resource not accessible by integration").

## Allowed chore paths

Only these paths qualify for the lightweight Run Tests pass:

- `evals/eval_outputs/**` — eval run outputs (only `published/` is committed; `local/` is gitignored)
- `CHANGELOG.md` — release changelog updates
- `docs/pr_evals_results/**` — aggregated eval results

## Allowed bot branches

Chore PRs use these **head** branches (one per workflow):

- `bot/evals-results`
- `bot/release-changelog`
- `bot/aggregate-evals`

PRs from any other branch (e.g. `development`) do not get the auto-pass even if they only touch chore paths; they go through the normal PR workflow and full tests.

## Workflows that use this pathway

| Workflow | Bot branch | Paths |
|----------|------------|--------|
| Run Evaluations (upload_to_main) | `bot/evals-results` | `evals/eval_outputs/published/**` |
| Create Release on Merge to Main | `bot/release-changelog` | `CHANGELOG.md` |
| Aggregate Evaluation Results | `bot/aggregate-evals` | `docs/pr_evals_results/**` |
| Sync Main to Development | *(main → development)* | Runs after pushes to main that touch the paths above. |

## Merge flow

Each of these workflows:

1. Creates a PR with `peter-evans/create-pull-request` to `main` from the corresponding bot branch.
2. Sets the **Run Tests** commit status to success on the PR head SHA (so branch protection is satisfied without relying on another workflow run).
3. Polls the commit status API until **Run Tests** is `success` (typically immediate), then runs `gh pr merge <number> --squash --delete-branch` with `GITHUB_TOKEN` (the bot branch is deleted after merge).

If the status does not appear in time, the step exits without failing and the PR remains open for manual merge.

## Syncing main back to development

After chore updates are merged to `main`, the **Sync Main to Development** workflow (`.github/workflows/sync-main-to-development.yml`) runs on push to `main` when the commit touches chore paths. It:

1. Creates a PR from `main` into `development` (or reuses an existing open one).
2. Sets **Run Tests** to success on the PR head (current `main` commit).
3. Merges the PR so `development` gets the latest evals, CHANGELOG, and aggregated docs.

Branch protection on `development` is satisfied by the same Run Tests status pattern; the workflow does not push directly. The sync runs only when the push to `main` includes `evals/eval_outputs/**`, `CHANGELOG.md`, or `docs/pr_evals_results/**`, i.e. after one of the chore workflows has merged.

## Recursion protection

Push-triggered workflows use `paths-ignore` so that merges that only add chore outputs do not re-trigger:

- **Deploy to Prod on Merge**: `paths-ignore` includes `evals/eval_outputs/**`, `docs/pr_evals_results/**`, and `CHANGELOG.md`.
- **Create Release on Merge**: same `paths-ignore` so chore-only merges (eval results, aggregated docs, or CHANGELOG) do not start another release run.

The main **Pull Request** workflow uses `paths-ignore: evals/eval_outputs/**` so chore PRs do not run tests or staging deploy.

## Troubleshooting

- **PR creation or merge fails ("Resource not accessible by integration")** — Ensure [Workflow permissions](#workflow-permissions) are set to **Read and write permissions** and **Allow GitHub Actions to create and approve pull requests** is enabled.
- **PR stays open** — The creating workflow sets Run Tests itself; if it timed out before merge (check it’s triggered by the path and that the head branch is allowlisted). Merge the PR manually if the diff is correct.
- **Run Tests never passes** — The creating workflow sets the status; ensure its job has `statuses: write`. Chore PRs are intended to be created only by the workflows above; manually opened chore PRs would need Run Tests set some other way (e.g. run the corresponding workflow or merge with admin override).
- **Required check missing** — Branch protection expects a check named **Run Tests**. Do not rename that context without updating branch protection.
