# 🧱 Python Template Repository — Design Outline

This document describes the structure, automation, and configuration of the **Python Project Template Repository**.  
It defines how to organize files, configure CI/CD, document changes, and manage GitHub projects.

---

## 📂 Repository Structure

```
project-template/
├─ README.md
├─ CHANGELOG.md
├─ environment.yml
├─ .gitignore
├─ pyproject.toml
├─ Makefile
│
├─ config/                # YAML configuration files for the repo
├─ docs/                  # Additional documentation beyond README
│  ├─ architecture.drawio # Architecture diagram (auto-exported to PNG)
│  └─ architecture.png
│
├─ infra/                 # Infrastructure-as-Code (Terraform, CDK, SAM, etc.)
│
├─ notebooks/             # Jupyter notebooks for experimentation
│
├─ scripts/               # Bash or Python scripts for deployment and setup
│  ├─ deploy.sh
│  └─ setup_env.sh
│
├─ src/                   # Application source code (modules or microservices)
│  └─ main.py
│
├─ tests/                 # Unit tests (pytest)
│  └─ test_main.py
│
└─ .github/
   ├─ ISSUE_TEMPLATE/
   │  ├─ bug_report.yml
   │  ├─ feature_request.yml
   │  └─ config.yml
   ├─ pull_request_template.md
   ├─ workflows/
   │  ├─ drawio-export.yml
   │  └─ deploy.yml
   └─ project-setup.yml
```

---

## 🧾 README.md

The `README.md` should include:

- **Project description** — overview and purpose.  
- **Environment setup** instructions:

```bash
conda env create -f environment.yml
conda activate project-env
```

---

## 🧩 environment.yml

Minimal environment for reproducibility:

```yaml
name: project-env
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pytest
  - pyaml
  - awscli
  - boto3
```

---

## 🪵 CHANGELOG.md

Follow [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)  
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Example:

```markdown
# Changelog

## [Unreleased]

## [1.0.0] - 2025-10-07
### Added
- Initial template repository structure.
```

---

## 🧹 .gitignore

Include standard Python ignores and personal scratch files:

```
__pycache__/
*.pyc
*.pyo
*.pyd
*.egg-info/
.venv/
.env/
.ipynb_checkpoints/
.vscode/
scratchpad*
```

---

## ⚙️ pyproject.toml

Configuration for formatting, testing, and packaging:

```toml
[build-system]
requires = ["setuptools", "wheel"]
build-backend = "setuptools.build_meta"

[tool.black]
line-length = 100

[tool.isort]
profile = "black"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q"
```

---

## 🧰 Makefile

Defines standard developer commands:

```makefile
.PHONY: install lint test clean all

install:
	conda env create -f environment.yml || conda env update -f environment.yml

lint:
	ruff .
	mypy src

test:
	pytest

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} +

all: lint test
```

---

## ⚙️ GitHub Actions

### 1️⃣ **Draw.io Auto Export**

Automatically converts `.drawio` files to `.png` images embedded in documentation.

```yaml
# .github/workflows/drawio-export.yml
name: Export Draw.io diagrams
on:
  push:
    paths:
      - "docs/**/*.drawio"
jobs:
  export:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Export Draw.io to PNG
        uses: rlespinasse/drawio-export-action@v2
        with:
          source: docs/architecture.drawio
          output: docs/architecture.png
```

---

### 2️⃣ **Deployment Workflow**

Runs deployment script when a pull request merges from `development` → `main`.

```yaml
# .github/workflows/deploy.yml
name: Deploy to Production
on:
  pull_request:
    branches: [main]
    types: [closed]
jobs:
  deploy:
    if: github.event.pull_request.merged == true && github.event.pull_request.base.ref == 'main' && github.event.pull_request.head.ref == 'development'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run deployment script
        run: bash scripts/deploy.sh
```

---

## 🧾 Pull Request Template

```markdown
## Description
[Provide a clear and concise description of the changes introduced by this pull request.]

## Related Issues/PRs
- Closes #IssueNumber
- Related to #IssueNumber

## Changes Made
- [Change 1]
- [Change 2]
- [Change 3]

## Testing
[Describe how you tested these changes.]

## Checklist
- [ ] Code follows style guidelines.
- [ ] Self-reviewed and documented.
- [ ] Added or updated tests.
- [ ] Tests pass locally.
```

---

## 🪲 Issue Templates

### **Bug Report**

```yaml
name: Bug Report
description: File a bug report
labels: [bug]
body:
  - type: textarea
    id: description
    attributes:
      label: Describe the bug
      description: What happened and what did you expect?
```

### **Feature Request**

```yaml
name: Feature Request
description: Suggest a new feature or enhancement
labels: [enhancement]
body:
  - type: textarea
    id: proposal
    attributes:
      label: Describe your feature idea
      description: What problem does it solve?
```

---

## 🚀 CI/CD and Branching Model

### Branches

- `development` — active work branch.  
- `main` — production branch.

### Workflow

- Pull Requests from `development` → `main` trigger **deployments**.  
- Deployment handled via `scripts/deploy.sh`.

---

## 🗂️ GitHub Project Configuration

Create a **GitHub Project** with the following tabs:

| Tab | Description |
|-----|--------------|
| **Incoming Backlog** | Table view of all new issues. |
| **Sprint Planning** | Group by iteration; prioritize items. |
| **Current Iteration** | Kanban board for daily standups. |
| **QA Review** | Shows all issues in “In Review” state, ready for integration testing. |

---

## ✅ Summary

This template provides:
- A reproducible **Conda environment**
- A structured **project layout**
- Automated **diagram generation**
- CI/CD for **deployment**
- **Pull request and issue templates**
- **Changelog** and versioning standards
- GitHub **Project integration**

---
