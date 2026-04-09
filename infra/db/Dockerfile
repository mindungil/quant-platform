FROM pgvector/pgvector:pg16

COPY init.sql /docker-entrypoint-initdb.d/01-init.sql
COPY postgres-init.sql /docker-entrypoint-initdb.d/02-postgres-init.sql
COPY timescale-init.sql /docker-entrypoint-initdb.d/03-timescale-init.sql
COPY rls-init.sql /docker-entrypoint-initdb.d/04-rls-init.sql
