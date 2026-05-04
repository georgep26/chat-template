# AI Agents

This folder is the common agent-facing space for Codex, Claude Code, Cursor, and future coding agents.

The durable project knowledge base is `docs/`. Humans and agents should both read those docs for standards, architecture, deployment, and process.

`ai_agents/` exists to route agents to that knowledge and to standardize repeatable workflows.

## Structure

- `context/`: Short indexes that point agents to the right docs for a topic.
- `prompts/`: Tool-neutral workflow prompts used by Cursor commands, Claude Code skills, and Codex skills.

## Rules

- Do not copy full docs into this folder.
- Point agents to authoritative docs instead.
- Keep prompts tool-neutral where possible.
- Keep tool-specific adapters thin.
- Put durable development plans in `docs/development_plans/`, not in tool-specific plan folders.
