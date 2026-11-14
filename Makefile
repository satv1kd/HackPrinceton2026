PYTHON ?= python3.13

.PHONY: install dev test

install:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

dev:
	./run.sh

test:
	.venv/bin/pytest -q
