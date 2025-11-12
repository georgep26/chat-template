# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2025-11-11
### Summary
This release adds infrastructure documentation, GitHub workflow templates, and comprehensive developer onboarding materials to improve project contribution and collaboration.

### Added
- Example IAM role for Lambda execution with documentation for cloud roles
- Copilot version of intro prompt
- Pull request template to standardize submission process and improve clarity for contributors
- New issue templates for bug reports, feature requests, and general tasks to enhance issue tracking and project management
- Code standards documentation
- CONTRIBUTING.md with guidelines for onboarding, development workflow, pull requests, and issue creation
- Comprehensive onboarding guide for new developers covering project overview, development process, environment setup, and code standards
- Development process documentation including GitHub project integration, task tracking features, and detailed issue categorization

### Changed
- Update commit message guidelines to limit full message length
- Revamp environment setup process by replacing conda commands in Makefile with a script, enhancing OS detection and installation in setup_env.sh
- Update commit message guidelines to replace 'perf' with 'performance' for clarity and consistency

### Fixed
- Fix filename for template_outline.md

## [1.1.0] - 2025-11-11
### Summary
This release adds comprehensive documentation and development tooling enhancements, including agent instructions, Cursor integration features, and improved configuration management.

### Added
- CHANGELOG update instructions with versioning and formatting guidelines
- AGENT.md with instructions for using the Python template repository, including prerequisites, working guidelines, and key principles
- Cursor rules for commit message generation following conventional commit format
- Commands for diagram and presentation generation based on external repositories
- Example pre-commit configuration as optional setup
- .cursorignore and updated .gitignore to exclude feature ideas and other development files

### Changed
- Refactor application configuration: merge logging settings into app_config.yml and update README for clarity
- Update Python version in environment.yml from 3.11 to 3.14
- Update README.md to reflect project purpose and directory structure for Python template repository

### Fixed
- Update commit message generation rules to clarify conventional commit style requirements

## [1.0.0] - 2025-10-07
### Added
- Initial template repository structure.
