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

## Phone pair links

- Pair codes are short-lived (default 15 min, max uses capped) and in-memory only
- Trusted pair URLs may embed the gateway token in the URL hash for one-tap phone join — treat QR/links as **secrets**
- Invalid redeem attempts share the **per-IP auth failure rate limit** with wrong bearer tokens
- Prefer **Tailscale Serve** (tailnet only) over Funnel for phone access
- Cellular works when the phone has Tailscale connected to the same tailnet — not via raw LAN IP
