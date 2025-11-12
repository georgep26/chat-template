# Contributing 

Feel free to fork this code. If you want to submit a PR please use the PR template (`.github/pull_request_template.md`). When creating issues, please use the appropriate issue template from `.github/ISSUE_TEMPLATE/`.

## Getting Started

1. **Setup Environment**: Run `make install` to set up the development environment
2. **Review Documentation**: Read `docs/development_process.md` for the development workflow
3. **Code Standards**: Follow the guidelines in `docs/code_standards.md`

## Development Workflow

- Create feature branches from `development`
- Submit PRs from feature branches to `development` (use PR template)
- After QA review, PRs are merged from `development` to `main`
- See `docs/development_process.md` for detailed workflow

## Pull Requests

- Use the PR template (`.github/pull_request_template.md`)
- Ensure all tests pass (`make test`)
- Run linting (`make lint`)
- Update documentation as needed
- Include tests for new features

## Issues

When creating issues, use the appropriate template from `.github/ISSUE_TEMPLATE/`:
- Feature requests
- Bug reports
- Tasks

## New Developers

New developers can use the "intro" slash command for onboarding.

- Review `docs/development_process.md` for the development workflow
- Review `docs/code_standards.md` for coding guidelines

## AI Agents

AI agents should refer to `AGENT.md` for specific instructions.