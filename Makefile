.PHONY: dev-env lint test clean all eval

dev-env:
	@bash scripts/setup_env.sh

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
