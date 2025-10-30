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
