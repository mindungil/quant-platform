FROM python:3.11-slim

WORKDIR /code

# strategy-lab pulls in heavier numerical deps: nautilus-trader (backtest),
# sentence-transformers (memory embeddings), quantstats, backtrader, statsmodels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake gfortran libopenblas-dev liblapack-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/strategy-registry/requirements.txt  /tmp/reqs/strategy-registry.txt
COPY services/statistics-service/requirements.txt /tmp/reqs/statistics-service.txt
COPY services/memory-service/requirements.txt     /tmp/reqs/memory-service.txt
COPY services/backtest-service/requirements.txt   /tmp/reqs/backtest-service.txt

RUN cat /tmp/reqs/*.txt \
    | sed 's/_/-/g' \
    | sort -t= -k1,1 -u \
    > /tmp/sl-reqs.txt \
    && pip install --no-cache-dir -r /tmp/sl-reqs.txt \
    && rm -rf /tmp/reqs /tmp/sl-reqs.txt

COPY shared/ /code/shared/
COPY services/strategy-registry/  /code/services/strategy-registry/
COPY services/statistics-service/ /code/services/statistics-service/
COPY services/memory-service/     /code/services/memory-service/
COPY services/backtest-service/   /code/services/backtest-service/

COPY scripts/_lib/ /code/scripts/_lib/
COPY scripts/start_strategy_lab.sh /code/start_strategy_lab.sh
# V3 #1 — learning loop daemon entry (used by the learning-loop sidecar
# service in docker-compose.yml; same image, different command).
COPY scripts/run_learning_loop.py /code/scripts/run_learning_loop.py
# V4-4 — reoptimizer daemon entry (sidecar service).
COPY scripts/run_reoptimizer_daemon.py /code/scripts/run_reoptimizer_daemon.py
# V4-3 — GP discovery daemon entry (sidecar service).
COPY scripts/run_gp_discovery.py /code/scripts/run_gp_discovery.py
# D7 — drawdown auto-kill daemon entry.
COPY scripts/run_drawdown_monitor.py /code/scripts/run_drawdown_monitor.py
# D8 — attribution per-cycle daemon.
COPY scripts/run_attribution_daemon.py /code/scripts/run_attribution_daemon.py
RUN chmod +x /code/start_strategy_lab.sh /code/scripts/_lib/*.sh

EXPOSE 8004 8005 8007 8013

CMD ["/code/start_strategy_lab.sh"]
