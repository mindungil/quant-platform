FROM python:3.11-slim

WORKDIR /code

# Execution domain: ccxt (multi-exchange), pyportfolioopt (MVO), scipy (risk).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gfortran libopenblas-dev liblapack-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/order-service/requirements.txt      /tmp/reqs/order-service.txt
COPY services/exchange-adapter/requirements.txt   /tmp/reqs/exchange-adapter.txt
COPY services/risk-service/requirements.txt       /tmp/reqs/risk-service.txt
COPY services/credential-store/requirements.txt   /tmp/reqs/credential-store.txt
COPY services/portfolio-service/requirements.txt  /tmp/reqs/portfolio-service.txt

RUN cat /tmp/reqs/*.txt \
    | sed 's/_/-/g' \
    | sort -t= -k1,1 -u \
    > /tmp/ex-reqs.txt \
    && pip install --no-cache-dir -r /tmp/ex-reqs.txt \
    && rm -rf /tmp/reqs /tmp/ex-reqs.txt

COPY shared/ /code/shared/
COPY services/order-service/      /code/services/order-service/
COPY services/exchange-adapter/   /code/services/exchange-adapter/
COPY services/risk-service/       /code/services/risk-service/
COPY services/credential-store/   /code/services/credential-store/
COPY services/portfolio-service/  /code/services/portfolio-service/

COPY scripts/_lib/ /code/scripts/_lib/
COPY scripts/start_execution.sh /code/start_execution.sh
RUN chmod +x /code/start_execution.sh /code/scripts/_lib/*.sh

EXPOSE 8008 8009 8010 8011 8012

CMD ["/code/start_execution.sh"]
