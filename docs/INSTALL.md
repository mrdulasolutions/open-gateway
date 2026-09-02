# Install & package OpenGateway

> **Self-host only.** OpenGateway is the open-source hub you run yourself (local, Docker, Railway, VPS). Product docs: [docs.opengateways.xyz](https://docs.opengateways.xyz) · source: [github.com/mrdulasolutions/open-gateway](https://github.com/mrdulasolutions/open-gateway).


Neat ways to run the hub on **your machine** — pick one path.

| Path | Best for | UI included? |
|------|----------|--------------|
| **A. PyPI / `uv tool install`** | Daily driver on Mac/Linux | Yes (packaged static) |
| **B. Clone + `uv sync`** | Development / contrib | Yes (`webapp/dist`) |
| **C. Docker Compose** | Always-on hub / lab server | Yes |
| **D. GHCR image** | Pull prebuilt container | Yes |

Any non-loopback mode needs a strong token:

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
```

**PyPI:** `uv tool install opengateways==0.1.2` — CLI **`opengateways`** (alias `opengateway`). Import: `opengateway`.

---

## A. One-line tool install (recommended)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv tool install opengateways==0.1.2

opengateways serve
# → http://127.0.0.1:8765/ui/
```

**Alternative (git @ tag):**

```bash
uv tool install "git+https://github.com/mrdulasolutions/open-gateway.git@v0.1.2"
```

**LAN + Tailscale (dual path)**

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
opengateway serve --mode public --via open --network lan \
  --host 0.0.0.0 --token "$OPENGATEWAY_AUTH_TOKEN" \
  --public-url "http://$(ipconfig getifaddr en0 2>/dev/null || hostname -I | awk '{print $1}'):8765"

tailscale serve --bg 8765
```

**Upgrade**

```bash
uv tool install opengateways==0.1.2 --force
```

**MCP harness** (gateway already running):

```bash
opengateway mcp
# one-shot: uvx opengateways mcp
```

Wire templates from the repo: [`configs/`](../configs/) — set `OPENGATEWAY_URL` + `OPENGATEWAY_AUTH_TOKEN` in the harness env.

---

## B. Dev checkout

```bash
git clone https://github.com/mrdulasolutions/open-gateway.git
cd open-gateway
uv sync --all-extras
uv run opengateway serve
```

Rebuild UI after webapp edits:

```bash
make ui          # bun build → webapp/dist + sync into package static
make check       # tests + UI
```

---

## C. Docker Compose

```bash
export OPENGATEWAY_AUTH_TOKEN="$(openssl rand -hex 24)"
docker compose up -d --build
open http://localhost:8765/ui/
# paste token in Settings
```

Data lives in the `og-data` volume (`OPENGATEWAY_DB=/data/state.db`).

---

## D. GHCR image

After release tag `v0.1.2`:

```bash
docker pull ghcr.io/mrdulasolutions/open-gateway:0.1.2
docker run --rm -p 8765:8765 \
  -e OPENGATEWAY_AUTH_TOKEN="$OPENGATEWAY_AUTH_TOKEN" \
  -v og-data:/data \
  ghcr.io/mrdulasolutions/open-gateway:0.1.2
```

---

## E. Local wheel (offline / airgap)

```bash
git clone https://github.com/mrdulasolutions/open-gateway.git && cd open-gateway
make package                 # tests + UI sync + uv build → dist/*.whl
uv tool install dist/opengateway-*.whl
opengateway serve
```

---

## What gets packaged

| Artifact | Contents |
|----------|----------|
| Python package `opengateway` | API, CLI, MCP, SQLite store |
| `opengateway/static/` | Live Ops UI (Vite build) |
| Console script | `opengateway` → `serve`, `mcp`, `doctor`, `pair`, `im`, … |
| Default DB | `~/.opengateway/state.db` (or `OPENGATEWAY_DB`) |

No Node runtime is required to **run** the hub. Node/Bun is only needed to **change** the UI sources under `webapp/`.

---

## Harness config (after install)

| Client | Config |
|--------|--------|
| Claude Code | `configs/mcp.claude.json` |
| Cursor | `configs/mcp.cursor.json` |
| Grok | `configs/mcp.grok.toml` |
| Codex | `configs/mcp.codex.toml` |
| Hermes | `configs/mcp.hermes.yaml` |

Skill playbook: [`skills/opengateway-collab/SKILL.md`](../skills/opengateway-collab/SKILL.md)

---

## Optional cloud deploy

See **[DEPLOY.md](DEPLOY.md)** for Fly.io, Railway, GHCR, and Redis multi-worker.

---

## Production checklist

See [PRODUCTION.md](PRODUCTION.md) and [GATEWAYS.md](GATEWAYS.md).

```bash
opengateway doctor
make check   # from a git checkout
```
