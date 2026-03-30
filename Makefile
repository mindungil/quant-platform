PYTHON ?= python3
VENV ?= .venv
NPM ?= npm

.PHONY: venv install test compile smoke compose-config

venv:
	$(PYTHON) -m venv $(VENV)

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
	pip install pytest
	cd services/frontend && $(NPM) install

test:
	. $(VENV)/bin/activate && \
	PYTHONPATH=.:services/market-data pytest services/market-data/tests && \
	PYTHONPATH=.:services/feature-store pytest services/feature-store/tests && \
	PYTHONPATH=.:services/signal-service pytest services/signal-service/tests && \
	PYTHONPATH=.:services/memory-service pytest services/memory-service/tests && \
	PYTHONPATH=.:services/strategy-registry pytest services/strategy-registry/tests && \
	PYTHONPATH=.:services/crypto-agent pytest services/crypto-agent/tests && \
	PYTHONPATH=.:services/backtest-service pytest services/backtest-service/tests && \
	PYTHONPATH=.:services/exchange-adapter pytest services/exchange-adapter/tests && \
	PYTHONPATH=.:services/order-service pytest services/order-service/tests && \
	PYTHONPATH=.:services/risk-service pytest services/risk-service/tests && \
	PYTHONPATH=.:services/credential-store pytest services/credential-store/tests && \
	PYTHONPATH=.:services/orchestrator-agent pytest services/orchestrator-agent/tests && \
	PYTHONPATH=.:services/etf-agent pytest services/etf-agent/tests && \
	PYTHONPATH=.:services/stock-agent pytest services/stock-agent/tests && \
	PYTHONPATH=.:services/portfolio-service pytest services/portfolio-service/tests && \
	PYTHONPATH=.:services/statistics-service pytest services/statistics-service/tests && \
	PYTHONPATH=.:services/auth-service pytest services/auth-service/tests && \
	PYTHONPATH=.:services/external-data-service pytest services/external-data-service/tests && \
	PYTHONPATH=.:services/llm-gateway pytest services/llm-gateway/tests && \
	PYTHONPATH=.:services/api-gateway pytest services/api-gateway/tests
	cd services/frontend && $(NPM) run typecheck && $(NPM) run build

compile:
	$(PYTHON) -m compileall .

compose-config:
	docker-compose -f docker-compose.yml config

smoke: compose-config compile
