# OpenGateway — multi-agent hub (API + Live Ops UI)
# UI is baked into the Python package (src/opengateway/static); no Node at runtime.
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
    OPENGATEWAY_NETWORK=lan

WORKDIR /app

# System deps minimal; uv for fast install
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Copy project and install as package (includes static UI)
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY webapp/dist ./webapp/dist
# Ensure package static is present even if builder skipped sync
RUN mkdir -p src/opengateway/static \
    && cp -R webapp/dist/* src/opengateway/static/ \
    && uv pip install --system --no-cache .

# Data dir for SQLite. Do NOT use Docker VOLUME — Railway rejects it.
# Attach a Railway Volume (or Fly mount) at /data for persistence.
RUN mkdir -p /data && chmod 777 /data
ENV OPENGATEWAY_DB=/data/state.db

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${OPENGATEWAY_PORT}/ping" || exit 1

# Token required for public bind — set OPENGATEWAY_AUTH_TOKEN at run time
ENTRYPOINT ["opengateway", "serve"]
CMD ["--mode", "public", "--via", "open", "--network", "lan", "--host", "0.0.0.0", "--port", "8765"]
