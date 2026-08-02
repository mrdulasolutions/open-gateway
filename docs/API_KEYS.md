# Per-device API keys

Mint scoped bearer tokens for phones, agents, and CI — separate from the master `OPENGATEWAY_AUTH_TOKEN`.

## Scopes

| Scope | Allows |
|-------|--------|
| `admin` | Everything (including key management) |
| `write` | Create rooms, post messages, tasks, pair |
| `read` | GET / HEAD only |
| `pair` | Pair create (and read where needed) |
| `push` | Push subscription management |

Master env token always has **admin**.

## Mint a device key

```bash
export TOKEN="$OPENGATEWAY_AUTH_TOKEN"
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"iphone","device_label":"iPhone 15","scopes":["write","read","pair","push"],"role":"observer"}' \
  https://your-hub/v1/keys
# → { "id": "...", "token": "ogk_…", "key_prefix": "ogk_…", "scopes": [...] }
# Save token — shown once only.
```

Use on the phone / agent:

```bash
export OPENGATEWAY_AUTH_TOKEN="ogk_…"   # or paste in Live Ops Settings
```

## List / revoke

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://your-hub/v1/keys
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "https://your-hub/v1/keys/{id}?revoke=true"
```

Secrets are **SHA-256 hashed** at rest (SQLite or Postgres). Audit events: `api_key.create`, `api_key.revoke`, `api_key.delete`.
