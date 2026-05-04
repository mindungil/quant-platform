FROM python:3.11-slim

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/market-data/requirements.txt          /tmp/reqs/market-data.txt
COPY services/feature-store/requirements.txt        /tmp/reqs/feature-store.txt
COPY services/signal-service/requirements.txt       /tmp/reqs/signal-service.txt
COPY services/external-data-service/requirements.txt /tmp/reqs/external-data-service.txt

RUN cat /tmp/reqs/*.txt \
    | sed 's/_/-/g' \
    | sort -t= -k1,1 -u \
    > /tmp/mp-reqs.txt \
    && pip install --no-cache-dir -r /tmp/mp-reqs.txt \
    && rm -rf /tmp/reqs /tmp/mp-reqs.txt

COPY shared/ /code/shared/
COPY services/market-data/          /code/services/market-data/
COPY services/feature-store/        /code/services/feature-store/
COPY services/signal-service/       /code/services/signal-service/
COPY services/external-data-service/ /code/services/external-data-service/

COPY scripts/_lib/ /code/scripts/_lib/
COPY scripts/start_market_pipeline.sh /code/start_market_pipeline.sh
RUN chmod +x /code/start_market_pipeline.sh /code/scripts/_lib/*.sh

EXPOSE 8001 8002 8003 8020

CMD ["/code/start_market_pipeline.sh"]
