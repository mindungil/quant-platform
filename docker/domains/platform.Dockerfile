FROM python:3.11-slim

WORKDIR /code

# Slim base — platform domain (api-gateway + auth-service) has no native deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy only the two services this domain owns.
COPY services/api-gateway/requirements.txt  /tmp/reqs/api-gateway.txt
COPY services/auth-service/requirements.txt /tmp/reqs/auth-service.txt

# Merge + dedup. Stable sort, case-sensitive, keeps the highest
# pin we happen to see (acceptable because both services share a
# curated minor version today — verified at Phase 1 build time).
RUN cat /tmp/reqs/*.txt \
    | sed 's/_/-/g' \
    | sort -t= -k1,1 -u \
    > /tmp/platform-reqs.txt \
    && pip install --no-cache-dir -r /tmp/platform-reqs.txt \
    && rm -rf /tmp/reqs /tmp/platform-reqs.txt

# Shared library (Python package used across the platform).
COPY shared/ /code/shared/

# Domain-owned services.
COPY services/api-gateway/  /code/services/api-gateway/
COPY services/auth-service/ /code/services/auth-service/

# Start script + shared domain helper.
COPY scripts/_lib/          /code/scripts/_lib/
COPY scripts/start_platform.sh /code/start_platform.sh
RUN chmod +x /code/start_platform.sh /code/scripts/_lib/*.sh

# 8019 auth (internal), 8017 gateway (public)
EXPOSE 8017 8019

CMD ["/code/start_platform.sh"]
