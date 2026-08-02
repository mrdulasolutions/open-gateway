# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

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
