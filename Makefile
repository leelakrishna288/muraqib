.PHONY: help install dev test cov lint typecheck security eval demo serve mcp docker clean ci

PY ?= python3

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package
	$(PY) -m pip install -e .

dev:      ## Install with dev extras
	$(PY) -m pip install -e ".[dev]"

test:     ## Run the test suite (offline, no keys, no cost)
	$(PY) -m pytest -q

cov:      ## Test with coverage report
	$(PY) -m pytest --cov=src/muraqib --cov-report=term-missing --cov-report=xml

lint:     ## Lint
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

fmt:      ## Auto-format
	$(PY) -m ruff format src tests && $(PY) -m ruff check --fix src tests

typecheck: ## Static types
	$(PY) -m mypy src/muraqib

security:  ## SAST + dependency audit
	$(PY) -m bandit -q -r src/muraqib -ll
	$(PY) -m pip_audit || true

eval:      ## Run the evaluation harness against the golden set
	$(PY) -m muraqib.cli evaluate

demo:      ## Run the bundled example assessment
	$(PY) -m muraqib.cli assess examples/aldar_tenant_assistant.yaml \
		--format md --format html --format json --format plan -o reports

govern:    ## Run the two example transactions through the runtime gates
	-$(PY) -m muraqib.cli govern examples/transaction_allowed.yaml
	-$(PY) -m muraqib.cli govern examples/transaction_blocked.yaml

serve:     ## Run the API on :8000
	$(PY) -m muraqib.cli serve --reload

mcp:       ## Run the MCP server on stdio
	$(PY) -m muraqib.mcp.server

docker:    ## Build the container
	docker build -t muraqib:1.0.0 .

ci: lint typecheck security test eval  ## Everything CI runs

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml data reports
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
