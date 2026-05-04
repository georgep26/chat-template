# Coding Standards Context Index

Use this index before code changes.

Read:

- `docs/code_standards.md` for Python style, docstrings, typing, error handling, logging, testing, and security guidance.
- `docs/github_standards.md` for Git, commit, PR, and changelog standards.
- `README.md` for project commands.
- `pyproject.toml` for formatter, linter, and type-checker configuration.

Typical verification:

- `make test` for the unit test suite.
- `make lint` when changing Python or shared code style.
- Targeted `pytest` commands for focused backend changes.
- Deployment dry runs for infrastructure script changes, for example `./scripts/deploy/deploy_all.sh <env> --set <set> --dry-run`.
