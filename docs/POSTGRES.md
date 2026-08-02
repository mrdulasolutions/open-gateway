# Postgres multi-writer

Use PostgreSQL when multiple processes or machines must share one system of record (SQLite is single-writer friendly; Postgres is concurrent).

## Setup

```bash
# Create DB
createdb opengateway

export OPENGATEWAY_DATABASE_URL="postgresql://user:pass@localhost:5432/opengateway"
# or:
export OPENGATEWAY_DB="postgresql://user:pass@localhost:5432/opengateway"

uv sync --extra postgres
uv run opengateway serve --mode public --token "$OPENGATEWAY_AUTH_TOKEN"
```

`GET /ping` reports `"backend": "postgres"`.

## With Redis

```bash
export OPENGATEWAY_DATABASE_URL="postgresql://…"
export OPENGATEWAY_REDIS_URL="redis://…"
uv sync --extra postgres --extra redis
# multi-worker uvicorn or several hosts — shared Postgres + Redis fan-out
```

## Schema

Same JSON-document tables as SQLite (`rooms`, `messages`, `api_keys`, `push_subs`, `audit_log`, …). Created automatically on first connect.

## Migration from SQLite

There is no automatic migrator in 0.0.x — start a new Postgres hub or export/import via the REST API for small datasets.
