PYTHON ?= python3
VENV ?= .venv
NPM ?= npm
PYTEST ?= pytest
DOCKER_COMPOSE ?= docker compose

.PHONY: venv operator-deps install test test-integration compile smoke compose-config compose-up compose-down seed-admin seed-data demo-flow smoke-e2e release-check migration-smoke dlq-reprocess dlq-stats cycle-run cycle-report

venv:
	$(PYTHON) -m venv $(VENV)

operator-deps: venv
	. $(VENV)/bin/activate && pip install --upgrade pip && pip install requests aiohttp

install: venv
	. $(VENV)/bin/activate && pip install --upgrade pip && \
	pip install -r services/market-data/requirements.txt && \
	pip install -r services/feature-store/requirements.txt && \
	pip install -r services/signal-service/requirements.txt && \
	pip install -r services/external-data-service/requirements.txt && \
	pip install -r services/memory-service/requirements.txt && \
	pip install -r services/strategy-registry/requirements.txt && \
	pip install -r services/crypto-agent/requirements.txt && \
	pip install -r services/backtest-service/requirements.txt && \
	pip install -r services/exchange-adapter/requirements.txt && \
	pip install -r services/order-service/requirements.txt && \
	pip install -r services/risk-service/requirements.txt && \
	pip install -r services/credential-store/requirements.txt && \
	pip install -r services/orchestrator-agent/requirements.txt && \
	pip install -r services/etf-agent/requirements.txt && \
	pip install -r services/stock-agent/requirements.txt && \
	pip install -r services/portfolio-service/requirements.txt && \
	pip install -r services/statistics-service/requirements.txt && \
	pip install -r services/auth-service/requirements.txt && \
	pip install -r services/llm-gateway/requirements.txt && \
	pip install -r services/api-gateway/requirements.txt && \
	pip install pytest requests aiohttp alembic
	cd services/frontend && $(NPM) install

test:
	PYTHONPATH=.:services/market-data $(PYTEST) services/market-data/tests && \
	PYTHONPATH=.:services/feature-store $(PYTEST) services/feature-store/tests && \
	PYTHONPATH=.:services/signal-service $(PYTEST) services/signal-service/tests && \
	PYTHONPATH=.:services/memory-service $(PYTEST) services/memory-service/tests && \
	PYTHONPATH=.:services/strategy-registry $(PYTEST) services/strategy-registry/tests && \
	PYTHONPATH=.:services/crypto-agent $(PYTEST) services/crypto-agent/tests && \
	PYTHONPATH=.:services/backtest-service $(PYTEST) services/backtest-service/tests && \
	PYTHONPATH=.:services/exchange-adapter $(PYTEST) services/exchange-adapter/tests && \
	PYTHONPATH=.:services/order-service $(PYTEST) services/order-service/tests && \
	PYTHONPATH=.:services/risk-service $(PYTEST) services/risk-service/tests && \
	PYTHONPATH=.:services/credential-store $(PYTEST) services/credential-store/tests && \
	PYTHONPATH=.:services/orchestrator-agent $(PYTEST) services/orchestrator-agent/tests && \
	PYTHONPATH=.:services/etf-agent $(PYTEST) services/etf-agent/tests && \
	PYTHONPATH=.:services/stock-agent $(PYTEST) services/stock-agent/tests && \
	PYTHONPATH=.:services/portfolio-service $(PYTEST) services/portfolio-service/tests && \
	PYTHONPATH=.:services/statistics-service $(PYTEST) services/statistics-service/tests && \
	PYTHONPATH=.:services/auth-service $(PYTEST) services/auth-service/tests && \
	PYTHONPATH=.:services/external-data-service $(PYTEST) services/external-data-service/tests && \
	PYTHONPATH=.:services/llm-gateway $(PYTEST) services/llm-gateway/tests && \
	PYTHONPATH=.:services/api-gateway $(PYTEST) services/api-gateway/tests
	cd services/frontend && $(NPM) run typecheck && $(NPM) run build

test-integration:
	PYTHONPATH=. $(PYTEST) tests/integration -v

compile:
	$(PYTHON) -m compileall shared migrations scripts services

compose-config:
	$(DOCKER_COMPOSE) -f docker-compose.yml config

smoke: compose-config compile

compose-up: operator-deps
	$(DOCKER_COMPOSE) up -d --build
	@echo "Waiting for platform gateway to become healthy..."
	@timeout 120 bash -c 'until $(DOCKER_COMPOSE) exec -T platform python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8017/health\").read()" 2>/dev/null; do sleep 3; done' || true
	@echo "Stack is up. Gateway: http://localhost:8017  UI: http://localhost:8018"

compose-down:
	$(DOCKER_COMPOSE) down --remove-orphans -v

seed-admin: operator-deps
	$(PYTHON) scripts/seed_admin.py

seed-data:
	$(PYTHON) scripts/seed_data.py

demo-flow: operator-deps
	$(PYTHON) scripts/demo_flow.py

smoke-e2e: operator-deps
	$(PYTHON) scripts/smoke_e2e.py

migration-smoke: operator-deps
	$(PYTHON) scripts/migration_smoke.py

dlq-reprocess:
	PYTHONPATH=. $(PYTHON) scripts/dlq_reprocess.py

dlq-stats:
	PYTHONPATH=. $(PYTHON) scripts/dlq_reprocess.py --dry-run

release-check: compile test compose-config migration-smoke smoke-e2e

cycle-run:
	$(PYTHON) scripts/research/quant_cycle_runner.py

cycle-report:
	$(PYTHON) scripts/research/cycle_report.py
