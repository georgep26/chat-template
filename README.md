# Project Name

Brief description of what this project does.

## Environment Setup
```bash
conda env create -f environment.yml
conda activate project-env
```

## Code Quality Tools (Optional)

This template includes a comprehensive pre-commit configuration (`.pre-commit-config.yaml`) with various code quality tools that are **disabled by default**. You can enable any combination of these tools based on your project needs.

### Available Tools

**Basic File Quality:**
- Remove trailing whitespace
- Ensure files end with newlines
- Validate YAML syntax
- Prevent large file commits
- Check for merge conflicts
- Remove debug statements

**Python Code Quality:**
- **Black**: Auto-format Python code
- **isort**: Sort and organize imports
- **flake8**: Lint for style and errors
- **mypy**: Static type checking
- **bandit**: Security vulnerability scanning
- **pydocstyle**: Docstring formatting

**Other File Types:**
- **Prettier**: Format YAML files
- **hadolint**: Lint Dockerfiles
- **Terraform hooks**: Format and validate Terraform files
- **nbQA**: Apply formatting to Jupyter notebooks

### How to Enable

1. Install pre-commit:
   ```bash
   pip install pre-commit
   ```

2. Edit `.pre-commit-config.yaml` and uncomment the sections you want to use

3. Install the hooks:
   ```bash
   pre-commit install
   ```

4. Run manually (optional):
   ```bash
   pre-commit run --all-files
   ```

Once enabled, these tools will automatically run before each commit, ensuring consistent code quality across your project.
