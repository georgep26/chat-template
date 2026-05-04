# AI Agent Strategy Plan

Date: 2026-05-04

## Goal

Make the `chat-template` backend repo usable from Codex, Claude Code, and Cursor without maintaining three separate sets of project instructions.

The repo should provide:

- A common project entry point for coding agents.
- Shared coding standards and deployment context.
- Shared prompt bodies for repeatable workflows.
- Tool-specific adapters for slash commands, skills, and project rules.
- A migration path from the current Cursor-only command set.
- A full translation of the current Cursor slash commands into common prompts under `ai_agents`.
- A single canonical location for development plans, regardless of which agent creates them.
- Shared GitHub standards for humans and agents, regardless of which agent is generating commits, PRs, changelog entries, or GitHub Project updates.
- A clean replacement of the stale legacy agent entry point with `AGENTS.md`.

## References Checked

- Codex custom instructions with `AGENTS.md`: https://developers.openai.com/codex/guides/agents-md
- Codex Agent Skills: https://developers.openai.com/codex/skills
- Codex CLI slash commands: https://developers.openai.com/codex/cli/slash-commands
- Claude Code memory and `CLAUDE.md`: https://docs.anthropic.com/en/docs/claude-code/memory
- Claude Code skills and commands: https://docs.claude.com/en/docs/claude-code/skills
- Claude Code slash commands: https://docs.anthropic.com/en/docs/claude-code/slash-commands
- Cursor rules and `AGENTS.md`: https://docs.cursor.com/en/context
- Cursor skills and discovery: https://cursor.com/docs/skills.md

## Initial Repo Findings

- At plan start, the repo had a legacy single-agent entry-point file, not `AGENTS.md`.
- At plan start, the legacy entry-point file was stale. It described the repo as a generic Python template and referenced `docs/template-outline.md`, which was not present.
- At plan start, Cursor support was the only mature agent setup:
  - `.cursor/commands/*.md` defines reusable commands.
  - `.cursor/rules/*.mdc` defines scoped project rules.
  - `.cursor/.cursorrules` is now a legacy compatibility pointer to `docs/github_standards.md`.
- The Cursor command set contains useful workflows but duplicates project context and has stale template/deployment assumptions in places.
- `docs/github_standards.md` is now the durable human/agent source for Git, GitHub, PR, changelog, GitHub Project, and commit-message standards.
- At plan start, no project-level `.claude/`, `.agents/`, `CLAUDE.md`, or `AGENTS.md` setup existed inside `chat-template`.

## Tool Capabilities

### Codex

Use these repo-supported surfaces:

- `AGENTS.md` for project instructions. Codex reads `AGENTS.md` from the repo root and nested working directories before doing work.
- `.agents/skills/<skill-name>/SKILL.md` for repo-scoped skills. Codex scans `.agents/skills` from the current working directory up to the repo root.

Important caveat:

- Current official Codex CLI slash-command docs focus on built-in slash commands. They do not document a repo-local `.codex/commands` equivalent to Cursor or Claude.
- Codex skills are the reliable repo-local abstraction. They can be invoked explicitly as skills, but strict `/intro` parity may require an optional local prompt adapter if Codex custom prompt discovery becomes stable/documented for the user's installed Codex version.

### Claude Code

Use these repo-supported surfaces:

- `CLAUDE.md` for project memory.
- `CLAUDE.md` can import `AGENTS.md` with `@AGENTS.md`, so Claude can share the same canonical instructions as Codex and Cursor.
- `.claude/skills/<skill-name>/SKILL.md` for project skills. A skill named `intro` can be invoked as `/intro`.
- `.claude/commands/*.md` also works, but Anthropic now recommends skills for workflows that may need support files.

### Cursor

Use these repo-supported surfaces:

- `AGENTS.md` as a simple root instruction file.
- `.cursor/rules/*.mdc` for scoped project rules, especially when instructions should apply only to Python, Bash, infra, or docs paths.
- `.agents/skills/<skill-name>/SKILL.md` for reusable slash-style workflows in Cursor.
- `.cursorrules` is legacy and should not be the long-term source of truth.

## Recommended Architecture

Use `AGENTS.md` as the canonical project entry point, use `docs/` as the durable human/agent knowledge base, and use `ai_agents/` as the common agent-facing prompt and context index.

The `ai_agents/` folder should not duplicate detailed documentation that already exists in `docs/`. It should point agents to the right documentation, define repeatable workflow prompts, and give tool-specific adapters a common target.

Tool-specific wrappers should stay small:

- Cursor and Codex should share `.agents/skills` adapters that delegate to `ai_agents/prompts`.
- Claude Code skills in `.claude/skills` should act as slash commands and delegate to `ai_agents/prompts`.
- Codex skills in `.agents/skills` should delegate to `ai_agents/prompts`.

```text
AGENTS.md
CLAUDE.md

ai_agents/
  README.md
  context/
    project.md
    deployment.md
    coding_standards.md
  prompts/
    intro.md
    deployed_resources.md
    security_review.md
    submit_pr.md
    update_changelog.md
    github_pm.md
    diagram.md
    presentation.md

.agents/
  skills/
    intro/
      SKILL.md
    deployed-resources/
      SKILL.md
    security-review/
      SKILL.md
    submit-pr/
      SKILL.md
    update-changelog/
      SKILL.md
    github-pm/
      SKILL.md
    diagram/
      SKILL.md
    presentation/
      SKILL.md

.claude/
  skills/
    intro/
      SKILL.md
    deployed-resources/
      SKILL.md
    security-review/
      SKILL.md
    submit-pr/
      SKILL.md
    update-changelog/
      SKILL.md
    github-pm/
      SKILL.md
    diagram/
      SKILL.md
    presentation/
      SKILL.md

.cursor/
  rules/
    aws_strategy.mdc
    bash_set_e.mdc
    github_standards.mdc
    public_repo_strategy.mdc
    python_code_standards.mdc
```

### File Responsibilities

`AGENTS.md`

- Canonical agent entry point.
- Kept concise enough to load automatically.
- Should explain:
  - This is the backend/infrastructure repo, not the frontend.
  - Read `README.md` first for project overview.
  - Read `docs/deployment_process.md`, `docs/config_structure.md`, and `docs/aws_naming_convention.md` before deployment or infrastructure changes.
  - Use `infra/infra.yaml` as the deployment source of truth.
  - Do not hardcode account IDs, regions, secret paths, resource names, or deployment order.
  - Respect gitignored secrets and the public-repo strategy.
  - Follow existing shell/Python standards and run relevant tests or dry runs.

Legacy compatibility entry point

- Should be replaced by `AGENTS.md`.
- During migration, it can briefly remain as a compatibility pointer to `AGENTS.md` if needed, but it should not remain a second source of project instructions.
- Remove the stale template-oriented content that references missing docs.

`CLAUDE.md`

- Claude Code shim.
- Should import `AGENTS.md` with `@AGENTS.md`.
- Should contain only Claude-specific notes below the import, if needed.

`ai_agents/context/*.md`

- Agent-facing context indexes for topics that span multiple docs.
- These files should point to authoritative project docs instead of duplicating them.
- Example: `deployment.md` should tell agents to read `docs/deployment_process.md`, `docs/config_structure.md`, `scripts/deploy/README.md`, `docs/aws_naming_convention.md`, and `infra/infra.yaml`.
- Keep these files short. The durable content belongs in `docs/`.

`docs/github_standards.md`

- Canonical GitHub, Git, PR, changelog, GitHub Project, and commit-message guidance for humans and coding agents.
- Includes the commit-message rules previously defined in `.cursor/.cursorrules`.
- Defines standard git practices:
  - Inspect `git status` before making commits or PRs.
  - Distinguish agent changes from unrelated user changes.
  - Do not revert unrelated changes unless explicitly asked.
  - Keep commits focused on the requested task.
  - Use the repo's conventional commit vocabulary and lowercase format.
  - Review diffs before generating commit messages.
  - Mention tests or dry runs performed when preparing PR descriptions.
- `AGENTS.md`, `CLAUDE.md`, Cursor rules, and command/skill wrappers should point agents to this file whenever they are asked to commit, generate a commit message, update the changelog, submit a PR, create issues, or manage GitHub Project work.

`ai_agents/prompts/*.md`

- Canonical prompt bodies for repeatable workflows.
- These are tool-neutral standard prompts.
- They should describe the agent workflow once and point to the relevant docs for context.
- They should avoid copying full sections from docs unless the content is genuinely agent-specific.
- Tool-specific command or skill files should be thin wrappers that instruct the agent to read and execute the matching shared prompt.

Initial standard prompts should cover every current Cursor slash command:

- `intro.md`
- `deployed_resources.md`
- `security_review.md`
- `submit_pr.md`
- `update_changelog.md`
- `github_pm.md`
- `diagram.md`
- `presentation.md`

`docs/*.md`

- Durable documentation for humans and agents.
- Use docs for project facts, standards, procedures, architecture decisions, deployment details, and GitHub standards.
- Use `ai_agents` to route agents to docs and standardize repeatable workflows, not to duplicate docs.

`docs/development_plans/*.md`

- Canonical location for development plans created by any coding agent.
- Use dated filenames, for example `docs/development_plans/2026-05-04_ai_agent_strategy_plan.md`.
- Do not use tool-specific plan folders as the durable source of truth. Cursor may still create temporary/internal plan files under `.cursor/plans`, but final plans that should be referenced later belong in `docs/development_plans`.
- Plans should be tool-neutral and readable by Codex, Claude Code, Cursor, and humans.

`.claude/skills/*/SKILL.md`

- Claude Code adapters.
- Use project skills as slash-command equivalents. For example, `.claude/skills/intro/SKILL.md` should make `/intro` available in Claude Code.
- Add Claude skills for every migrated Cursor slash command.
- Keep the real instructions in `ai_agents/prompts/<name>.md`.

`.agents/skills/*/SKILL.md`

- Codex adapters.
- Use repo-scoped Codex skills for workflow discovery.
- Add Codex skills for every migrated Cursor slash command.
- Each skill should reference `ai_agents/prompts/<name>.md`.

`.cursor/rules/*.mdc`

- Keep scoped rules for things Cursor can apply automatically by path.
- Replace duplicated content with links to shared context when possible.
- Keep commit-message guidance in `docs/github_standards.md` and use a Cursor project rule that references it.
- After the Cursor project rule is in place, `.cursor/.cursorrules` should either become a short compatibility pointer or be removed if Cursor behavior is verified.

## `/intro` Migration Plan

Migrate `/intro` first because it is low risk and exposes the strategy clearly.

1. Add `AGENTS.md` with current backend/deployment guidance.
2. Replace the legacy entry-point file with `AGENTS.md`; keep a temporary pointer only if needed during migration.
3. Add `CLAUDE.md` with `@AGENTS.md`.
4. Add the development-plan output policy to `AGENTS.md`, `CLAUDE.md`, and the shared intro prompt so every agent writes durable plans to `docs/development_plans`.
5. Add `docs/github_standards.md` with shared Git, GitHub, PR, changelog, GitHub Project, and commit-message guidance.
6. Add `ai_agents/prompts/intro.md` as the canonical onboarding prompt.
7. Add a shared `.agents/skills/intro/SKILL.md` adapter so both Codex and Cursor can invoke the same workflow.
8. Add `.claude/skills/intro/SKILL.md` so Claude Code can invoke `/intro`.
9. Validate Cursor discovery of the `.agents/skills/intro/SKILL.md` adapter.
10. Validate in each tool:
   - Cursor: type `/intro`.
   - Claude Code: type `/intro`.
   - Codex: invoke the `intro` repo skill through the Codex skills interface. If strict `/intro` is required, add an optional install script for Codex custom prompts only after confirming the installed Codex version supports them.

## Full Workflow Migration (Skills-First)

After `/intro`, translate every existing workflow into a standard prompt under `ai_agents/prompts`, then add matching shared/Codex and Claude skill adapters.

Current workflows to migrate:

1. `deployed-resources`
   - Replace manual AWS instructions with a prompt that prefers `scripts/deploy/report_resources.sh <env>`.
   - Keep manual fallback guidance only for troubleshooting.
2. `security-review`
   - Keep public-repo safety checks.
   - Decide whether reports should remain in `docs/security_reports/` or move to `docs/working_files/security_reports/`.
3. `submit-pr`
   - Share one PR creation prompt and keep GitHub CLI checks.
   - Require `docs/github_standards.md` before generating titles, descriptions, or branch/commit guidance.
4. `update-changelog`
   - Share one changelog prompt and keep the current changelog format.
   - Use `docs/github_standards.md` when interpreting commit history.
5. `github-pm`
   - Keep this as a more complex workflow. It may deserve a dedicated skill with helper scripts.
   - Require `docs/github_standards.md` for issue, PR, project, and GitHub workflow standards.
6. `diagram` and `presentation`
   - Keep as external-reference workflows, but wrap them consistently.

For each migrated workflow:

1. Create `ai_agents/prompts/<command_name>.md`.
2. Add `.claude/skills/<command-name>/SKILL.md` as a Claude slash-command equivalent.
3. Add `.agents/skills/<command-name>/SKILL.md` as the shared Codex/Cursor repo skill.
4. Confirm the shared prompt points agents to relevant `docs/` files instead of copying their content.

## Implementation Hurdles to Anticipate

- Strict command parity is not perfect. Cursor and Claude Code support repo-local `/intro` directly. Codex's documented repo-local primitive is skills, not repo-local slash-command markdown files.
- Startup context can get too large. `AGENTS.md` and `CLAUDE.md` should stay concise and point to deeper docs.
- Duplicated instructions will drift. `docs/` should hold durable facts and standards, `ai_agents/prompts` should hold agent workflows that point to those docs, and wrappers should stay small.
- Cursor's `.cursorrules` is legacy. Preserve commit-message behavior through `docs/github_standards.md` and a Cursor project rule.
- Git guidance must be clear about dirty worktrees. Agents should not assume every change belongs to them, especially because this repo often has concurrent user edits and generated files.
- Cursor may create plans under `.cursor/plans`, but those should be treated as tool-local working artifacts. Durable development plans belong in `docs/development_plans`.
- The repo now has important deployment behavior in `infra/infra.yaml`; agent prompts must tell agents to inspect that file before editing deploy scripts or docs.
- Some wrappers may rely on agents following an instruction to read another file. Keep wrappers explicit and test them in each tool.

## Decision

Use `AGENTS.md` plus shared prompts as the source of truth.

Use tool-specific adapters only where each tool needs a specific discovery location:

- Codex: `.agents/skills`.
- Claude Code: `CLAUDE.md` and `.claude/skills`.
- Cursor: `.cursor/rules` and `.agents/skills`.

Use `docs/` as the durable knowledge base for humans and agents. Use `ai_agents/` as the common agent-facing space that routes agents to docs and standard prompts.

Do not build three independent prompt libraries.

## Implementation Status

Implemented in the first migration pass:

- Added `AGENTS.md` as the canonical agent entry point.
- Replaced the legacy entry-point file with a temporary compatibility pointer.
- Added `CLAUDE.md` importing `AGENTS.md`.
- Added `ai_agents/` with context indexes and shared prompts for every existing Cursor slash command.
- Removed `.cursor/commands/*.md` after verifying Cursor can use `.agents/skills`.
- Added `.claude/skills/*/SKILL.md` adapters for Claude Code.
- Added `.agents/skills/*/SKILL.md` adapters for Codex.
- Added/connected `docs/github_standards.md` as the durable GitHub standards source for humans and agents.
- Updated README and GitHub Project command docs to point to the shared agent strategy.
