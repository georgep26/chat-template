# Python Template Repository

The purpose of this repository is to provide a template for future python projects. Below are some features built in to this repository.
- Standard directory structure
- Environment setup (currently set to python 3.14)
- Changelog definition to support releases

## Environment Setup

The full environment setup process is handled by the `scripts/setup_env.sh` script. This script will:
- Detect your operating system (macOS, Linux, or Windows)
- Check if conda is already installed
- Install Miniconda if it's not installed (with OS-specific installation methods)
- Create the conda environment from `environment.yml` with all required dependencies

### Quick Start

The easiest way to set up the environment is using the Makefile:

```bash
make install
```

Alternatively, you can run the setup script directly:

```bash
bash scripts/setup_env.sh
```

### Customizing the Environment Name

**Important**: Before running the setup, you should update the environment name in `environment.yml` to match your project name. The default name is `project-env`, but you should change it to something specific to your project (e.g., `my-awesome-project-env`).

### After Setup

Once the environment is created, activate it with:

```bash
conda activate <your-environment-name>
```

Replace `<your-environment-name>` with the name you specified in `environment.yml`.

## Directory Structure

```
python-template/
├── config/              # Configuration files for the application
│   └── app_config.yml   # Application configuration (includes app settings and logging)
├── docs/                # Documentation files
│   └── template-outline.md
├── infra/               # Infrastructure as Code (IaC) definitions
│   ├── cloudformation/  # AWS CloudFormation templates
│   │   ├── parameters.yaml
│   │   ├── template.yaml
│   │   └── README.md
│   ├── terraform/       # Terraform configuration files
│   │   ├── main.tf
│   │   ├── outputs.tf
│   │   └── variables.tf
│   └── README.md
├── notebooks/           # Jupyter notebooks for exploratory analysis
│   └── README.md
├── scripts/             # Utility and deployment scripts
│   ├── deploy/          # Deployment automation scripts
│   │   ├── cloudformation.sh
│   │   ├── deploy.sh
│   │   ├── terraform.sh
│   │   └── README.md
│   └── setup_env.sh     # Environment setup script
├── src/                 # Source code for the application
│   └── main.py
├── tests/               # Unit and integration tests
│   └── test_main.py
├── CHANGELOG.md         # Project changelog for version tracking
├── environment.yml      # Conda environment definition
├── Makefile             # Make commands for common tasks
├── pyproject.toml       # Python project configuration and dependencies
└── README.md            # This file
```

### Folder Descriptions

- **config/**: Contains the application configuration file (`app_config.yml`) which includes application settings, database configuration, API settings, AWS configuration, and logging configuration. This YAML file allows you to configure the application behavior without modifying code.

- **docs/**: Documentation files for the project, including guides, API documentation, and project outlines.

- **infra/**: Infrastructure as Code (IaC) definitions for deploying the application. Contains both CloudFormation and Terraform configurations for cloud infrastructure provisioning.

- **notebooks/**: Jupyter notebooks for data exploration, prototyping, and analysis. Useful for interactive development and sharing results.

- **scripts/**: Utility scripts for automation, deployment, and environment setup. The `deploy/` subdirectory contains scripts for deploying infrastructure and applications.

- **src/**: Main source code directory. Contains the core application logic and modules.

- **tests/**: Test files for unit testing, integration testing, and validation of the application code.

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
