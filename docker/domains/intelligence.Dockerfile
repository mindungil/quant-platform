FROM python:3.11-slim

WORKDIR /code

# Intelligence domain hosts the trading agents. Heaviest dep: LangGraph
# (crypto-agent's StateGraph) + pandas/scipy/sklearn for shared.regime and
# shared.alpha. No GPU deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gfortran libopenblas-dev liblapack-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/crypto-agent/requirements.txt       /tmp/reqs/crypto-agent.txt
COPY services/stock-agent/requirements.txt        /tmp/reqs/stock-agent.txt
COPY services/etf-agent/requirements.txt          /tmp/reqs/etf-agent.txt
COPY services/orchestrator-agent/requirements.txt /tmp/reqs/orchestrator-agent.txt

RUN cat /tmp/reqs/*.txt \
    | sed 's/_/-/g' \
    | sort -t= -k1,1 -u \
    > /tmp/int-reqs.txt \
    && pip install --no-cache-dir -r /tmp/int-reqs.txt \
    && rm -rf /tmp/reqs /tmp/int-reqs.txt

COPY shared/ /code/shared/
COPY services/crypto-agent/       /code/services/crypto-agent/
COPY services/stock-agent/        /code/services/stock-agent/
COPY services/etf-agent/          /code/services/etf-agent/
COPY services/orchestrator-agent/ /code/services/orchestrator-agent/

COPY scripts/_lib/ /code/scripts/_lib/
COPY scripts/start_intelligence.sh /code/start_intelligence.sh
RUN chmod +x /code/start_intelligence.sh /code/scripts/_lib/*.sh

EXPOSE 8006 8014 8015 8016

CMD ["/code/start_intelligence.sh"]
