# Portable-pgvector PostgreSQL for AgentShield on x86 nodes WITHOUT AVX-512.
#
# WHY THIS EXISTS
# Bitnami's stock postgres image ships pgvector 0.8.0 compiled with
# `-march=native` on an AVX-512-capable builder. pgvector's Makefile defaults
# OPTFLAGS to `-march=native`, so the resulting vector.so emits AVX-512
# instructions. On older x86 CPUs that lack AVX-512 — e.g. the Intel Xeon
# E5-2686 v4 (Broadwell) behind t2 instances, or AMD EPYC Zen 1-3 (c5a/c6a) —
# building an ivfflat index crashes the backend with:
#     server process ... terminated by signal 4: Illegal instruction
#     Failed process was running: CREATE INDEX ... USING ivfflat (...)
# That kills postgres mid-migration, so the whole Alembic transaction rolls
# back and registry-api can never start.
#
# THE FIX
# Rebuild pgvector with OPTFLAGS="" (pgvector's documented portability switch —
# it disables -march=native) and swap the resulting vector.so into the Bitnami
# image. The extension ABI is stable within a PostgreSQL major version, so a
# postgres:17 build drops cleanly into bitnami's 17.6. Result: runs on ANY
# x86-64 node regardless of AVX-512.
#
# Build (must be amd64 — EC2 nodes are x86_64):
#   docker buildx build --platform linux/amd64 \
#     -t <ecr>/agentshield/postgresql-pgvector:17.6.0-portable --push \
#     services/postgresql-pgvector/

FROM postgres:17-bookworm AS builder
ARG PGVECTOR_VERSION=v0.8.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git ca-certificates postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --branch "${PGVECTOR_VERSION}" --depth 1 \
      https://github.com/pgvector/pgvector.git /tmp/pgvector
WORKDIR /tmp/pgvector
# OPTFLAGS="" => no -march=native => portable baseline x86-64 binary.
RUN make OPTFLAGS="" && make install
RUN mkdir -p /out/lib /out/ext \
    && cp "$(pg_config --pkglibdir)/vector.so" /out/lib/ \
    && cp "$(pg_config --sharedir)"/extension/vector* /out/ext/

FROM bitnamilegacy/postgresql:17.6.0
USER root
# Replace bitnami's AVX-512 vector.so with the portable build.
COPY --from=builder /out/lib/vector.so /opt/bitnami/postgresql/lib/vector.so
COPY --from=builder /out/ext/ /opt/bitnami/postgresql/share/extension/
RUN chmod 644 /opt/bitnami/postgresql/lib/vector.so
USER 1001
