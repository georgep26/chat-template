.PHONY: install lint test clean all

install:
	@bash -c "eval \"$$(conda shell.bash hook)\" && bash scripts/setup_env.sh --activate"

lint:
	ruff .
	mypy src

test:
	pytest

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} +

all: lint test
