# Scale-out notes

## Single process (default)

One `opengateway serve` + SQLite at `~/.opengateway/state.db` is enough for
lab hubs, phone pair, and small teams.

## Multi-worker with Redis

```text
        ┌─────────────┐
   ───► │ worker 1    │──┐
HTTP    │ SQLite R/W* │  │   Redis pub/sub
   ───► │ worker 2    │──┼──► events + pair codes
        └─────────────┘  │
                         ▼
                    SSE / wait clients
```

\*SQLite multi-writer is **not** recommended on network filesystems. Prefer:

1. **One process** for state + Redis only if you later split read replicas, or  
2. Sticky sessions to one writer, or  
3. Future Postgres backend (not in 0.0.x).

What Redis **does** ship today:

- Cross-worker **GatewayEvent** fan-out (SSE / long-poll see sibling posts)
- Shared **pair codes** so any worker can redeem a phone QR

```bash
export OPENGATEWAY_REDIS_URL="redis://…"
uv run --extra redis opengateway serve --mode public --token "$TOKEN"
```

`GET /ping` includes `"redis": true` when the bus is connected.
