.PHONY: dev-env install lint test clean all eval branch-protection

dev-env:
	@bash scripts/setup/setup_local_dev_env.sh

branch-protection:
	@bash scripts/setup/setup_github.sh

lint:
	ruff .
	mypy src

test:
	pytest

eval:
	python -m evals.run_ragas

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} +

all: lint test
