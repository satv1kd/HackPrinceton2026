PYTHON ?= python3.13

.PHONY: install dev test test-client

install:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

dev:
	./run.sh

test:
	.venv/bin/pytest -q

test-client:
	node --experimental-default-type=module --test tests/*.test.mjs
