.PHONY: dev-env install lint test clean all eval

dev-env:
	@bash scripts/setup_env.sh

install:
	conda env create -f environment.yml || conda env update -f environment.yml

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
