FROM python:3.11-slim

WORKDIR /code

# numpy/scipy/ta need build tools for compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gfortran libopenblas-dev liblapack-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/llm-gateway/requirements.txt /tmp/reqs/llm-gateway.txt

RUN cat /tmp/reqs/*.txt \
    | sed 's/_/-/g' \
    | sort -t= -k1,1 -u \
    > /tmp/llm-tools-reqs.txt \
    && pip install --no-cache-dir -r /tmp/llm-tools-reqs.txt \
    && rm -rf /tmp/reqs /tmp/llm-tools-reqs.txt

COPY shared/ /code/shared/
COPY services/llm-gateway/ /code/services/llm-gateway/

COPY scripts/_lib/ /code/scripts/_lib/
COPY scripts/start_llm_tools.sh /code/start_llm_tools.sh
RUN chmod +x /code/start_llm_tools.sh /code/scripts/_lib/*.sh

EXPOSE 8021

CMD ["/code/start_llm_tools.sh"]
