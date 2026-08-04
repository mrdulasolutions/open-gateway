# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.0.x   | ✅ (current line) |
| 0.1.x   | Planned |

## Reporting a vulnerability

Email **security@mrdula.solutions** (or open a private GitHub security advisory on this repo).

Please include:

- Affected version / commit
- Reproduction steps
- Impact (auth bypass, data leak, RCE, etc.)

We aim to acknowledge within 72 hours.

## Hardening defaults

| Mode | Bind | Auth |
|------|------|------|
| `internal` | `127.0.0.1` | off (local only) |
| `serve` / Tailscale | `127.0.0.1` | bearer **required** |
| `public` open | `0.0.0.0` | bearer **required** |
| `funnel` | `127.0.0.1` | bearer **required** |

Never expose an unauthenticated gateway to the internet. Prefer [Tailscale Serve](docs/GATEWAYS.md) over raw port-forward.

## Secrets

- Set a strong `OPENGATEWAY_AUTH_TOKEN` for any non-loopback deployment
- Do not commit tokens, pair codes, or production DB files
- Rotate tokens if a harness config or phone pair link leaks
- Prefer **scoped device keys** (`POST /v1/keys`) for agents — keep master for operators only

## Production env knobs

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENGATEWAY_AUTH_TOKEN` | (required for public) | Master bearer |
| `OPENGATEWAY_TRUST_PROXY` | `false` | Honor `X-Forwarded-For` only behind a trusted reverse proxy |
| `OPENGATEWAY_PUBLIC_DOCS` | `false` | Expose `/docs` + OpenAPI when auth is on (DX only) |
| `OPENGATEWAY_DISABLE_SETUP_CLAIM` | `false` | Disable one-time UI bootstrap of master token |
| `OPENGATEWAY_OPEN_REGISTRATION` | `false` | Allow signups without invite after first admin |
| `OPENGATEWAY_CORS_ORIGINS` | `*` | Comma-separated origins; avoid `*` with credentialed browsers |

## Multi-tenant isolation

Session users and tenant-bound API keys **cannot** read or join another tenant’s rooms by UUID.  
Room list, search, snapshot, messages, tasks, files, and WebSockets all enforce the same check.

## Private DMs

Server-side: unscoped `GET .../messages`, `snapshot`, and global search return **public room messages only**.  
Pass `for_participant=<id>` to include that seat’s DMs.

## Audit log

Public / authenticated gateways write an append-only audit trail (room create, join, messages, pair).  
Query: `GET /v1/audit` requires **admin** (master token, admin session, or `admin` scope).  
Disable with `OPENGATEWAY_AUDIT=false`. Detail payloads redact common secret field names.

## Phone pair links

- Pair codes are short-lived (default 15 min, max uses capped), **16 hex chars** (~64-bit)
- Redeem mints a **scoped device key** (`read`/`write`/`pair`/`push`) — **never** the master token
- Pair QR/URLs do **not** embed master tokens
- Invalid redeem attempts share the **per-IP auth failure rate limit** with wrong bearer tokens
- Prefer **Tailscale Serve** (tailnet only) over Funnel for phone access
- Cellular works when the phone has Tailscale connected to the same tailnet — not via raw LAN IP

## WebSocket

When `require_auth` is on, `/v1/rooms/{id}/ws` requires the same bearer as HTTP  
(`?token=` query or `Authorization` header). Unauthenticated connects are closed with code `4401`.

## File downloads

Downloads are restricted to `~/.opengateway/files/{room_id}/{file_id}_*`.  
Client-supplied `metadata.path` on artifacts is ignored / stripped (no LFI).
