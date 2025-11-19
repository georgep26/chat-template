# Code Standards

This document outlines the coding standards and best practices for this project. All code should adhere to these guidelines to ensure consistency, maintainability, and quality.

## Documentation

- **Include docstrings with functions**: All functions, classes, and modules should have docstrings following Google or NumPy style conventions
- Use type hints in function signatures to improve code clarity and enable static type checking
- Document complex algorithms and business logic with inline comments when necessary
- Keep comments up-to-date with code changes

## Error Handling

- **Do not use a try except unless you are trying to handle a specific error**: Avoid bare `except:` clauses; always catch specific exceptions
- Use `except ExceptionType as e:` to capture and handle specific exceptions
- Log errors with appropriate context before re-raising or handling
- Fail fast: validate inputs early and raise clear error messages
- Use custom exceptions for domain-specific errors when appropriate

## Performance

- **Be mindful of running time (Big O notation) - e.g. we don't want nested loops**: Optimize algorithms for time and space complexity
- Use list comprehensions and generator expressions when appropriate
- Prefer built-in functions and libraries over custom implementations
- Use appropriate data structures (sets for membership tests, dicts for lookups, etc.)

## Code Organization

- Keep functions focused and single-purpose (Single Responsibility Principle)
- Use meaningful variable and function names that describe their purpose
- Follow PEP 8 style guidelines (enforced by Black with 100 character line length)
- Organize imports: standard library, third-party, local imports (enforced by isort)
- Group related functionality into modules and packages

## Type Safety

- Use type hints for function parameters and return values
- Use `typing` module for complex types (Optional, List, Dict, Union, etc.)
- Run mypy for static type checking (when enabled in pre-commit)
- Prefer type hints over runtime type checking when possible

## Testing

- Write unit tests for all functions and classes
- Aim for high test coverage of critical paths
- Use descriptive test names that explain what is being tested
- Use fixtures for common test setup
- Test edge cases and error conditions
- Keep tests independent and isolated

## Security

- Never commit secrets, API keys, or credentials to version control
- Use environment variables or secure configuration management for sensitive data
- Validate and sanitize all user inputs
- Use parameterized queries for database operations to prevent SQL injection
- Keep dependencies up-to-date and review security advisories
- Run bandit for security vulnerability scanning (when enabled in pre-commit)

## Code Quality

- Remove unused imports and variables
- Avoid deep nesting (prefer early returns and guard clauses)
- Follow DRY (Don't Repeat Yourself) principle
- Prefer explicit over implicit code
- Use context managers (`with` statements) for resource management

## Naming Conventions

- Use `snake_case` for functions, variables, and module names
- Use `PascalCase` for class names
- Use `UPPER_CASE` for constants
- Use descriptive names; avoid abbreviations unless widely understood
- Use `_` prefix for private/internal functions and variables (convention only)

## Import Organization

- Group imports: standard library, third-party, local (enforced by isort)
- Use absolute imports over relative imports when possible
- Avoid wildcard imports (`from module import *`)
- Keep imports at the top of the file (after module docstring)
- No imports within functions or classes

## Version Control

- Write clear, descriptive commit messages
- Keep commits focused on a single logical change
- Review code before committing
- Use meaningful branch names that describe the feature or fix

## Dependencies

- Pin dependency versions in `environment.yml` or `requirements.txt`
- Document why dependencies are needed
- Regularly update dependencies and test compatibility
- Avoid unnecessary dependencies; keep the dependency tree minimal

## Configuration

- Avoid hardcoded values that are expected to change often. These should be pulled into a config file (.json or .yaml)
- Store configuration in `config/` directory, not hardcoded in source
- Use environment variables for environment-specific settings
- Validate configuration on application startup
- Document all configuration options

## Logging

- Use the logging module instead of print statements for production code
- Use appropriate log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Include context in log messages (user ID, request ID, etc.)
- Configure logging through configuration files, not code
