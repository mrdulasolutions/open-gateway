# OpenGateway — multi-agent hub (API + Live Ops UI)
# Railway-safe: no Docker VOLUME; honors $PORT via docker-entrypoint.sh
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="OpenGateway" \
      org.opencontainers.image.description="Multi-agent collaboration hub (ACP + MCP + Live Ops UI)" \
      org.opencontainers.image.source="https://github.com/mrdulasolutions/open-gateway" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENGATEWAY_HOST=0.0.0.0 \
    OPENGATEWAY_PORT=8765 \
    OPENGATEWAY_MODE=public \
    OPENGATEWAY_VIA=open \
    OPENGATEWAY_NETWORK=public \
    OPENGATEWAY_DB=/data/state.db \
    OPENGATEWAY_AUDIT=true \
    OPENGATEWAY_OPEN_REGISTRATION=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY webapp/dist ./webapp/dist
COPY docker-entrypoint.sh /docker-entrypoint.sh

# Install core + deploy extras (Postgres + Redis + Web Push) so one-click works
RUN mkdir -p src/opengateway/static \
    && cp -R webapp/dist/* src/opengateway/static/ \
    && uv pip install --system --no-cache ".[deploy]" \
    && mkdir -p /data \
    && chmod 777 /data \
    && chmod +x /docker-entrypoint.sh

# No Docker VOLUME (Railway rejects it). Prefer Postgres plugin for persistence.
EXPOSE 8765

# Healthcheck uses PORT if set (Railway injects it at runtime; build default 8765)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8765}/ping" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
