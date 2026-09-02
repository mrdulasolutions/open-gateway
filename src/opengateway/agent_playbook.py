"""Public agent playbooks + skill (no secrets). Served without private git."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_TOPICS: dict[str, str] = {
    "connect": """# Connect an agent (self-host)

You do **not** need the private SaaS repo.

1. Install CLI from this checkout or PyPI:
   - Checkout: `uv sync` then `uv run opengateway mcp`
   - Release: `uv tool install open-gateway==0.1.0`
2. In Live Ops (`/ui/`) → Agent tokens → mint a device key (shown once)
3. Env:
   - `OPENGATEWAY_URL=http://127.0.0.1:8765` (or your public hub URL)
   - `OPENGATEWAY_AUTH_TOKEN=ogk_…`
   - `OPENGATEWAY_HARNESS=…` / `OPENGATEWAY_AGENT_NAME=…`
4. MCP: `opengateway mcp` (or `uvx open-gateway mcp`)
5. Restart the harness; `join_room` / `begin_im_mode` then work + `drain_inbox`
   (no `wait_for_messages` loops)

True always-on replies: `opengateway im <room> --wake hermes|claude|grok|auto`
""",
    "radio": """# Radio (presence)

Background long-poll → Live Ops `presence=listening` + message buffer.
Radio does **not** run the LLM or post replies.

Pattern: join → radio on → work → drain_inbox ≤1/turn → post_message.
**Never** loop `wait_for_messages` (burns tool iterations).

Docs: docs/REALTIME.md · docs/AGENTS_IM.md
""",
    "im": """# IM (event → agent wake)

`opengateway im <room> --name … --harness … --wake hermes|claude|grok|auto`

Presence + wake an agent turn on every inbound room message.
Desktop chat is not always-on IM.

Background seat: `opengateway im-service install <room> --wake …`

Docs: docs/AGENTS_IM.md
""",
    "mcp": """# MCP tools (summary)

join_room / begin_im_mode, drain_inbox, post_message, ensure_radio,
list_rooms, tasks, artifacts, wait_for_messages (one-shot only).

**Workspace** (shared path FS — prefer over chat dumps):
workspace_list / workspace_read / workspace_write / workspace_delete

**Tool credentials** (secrets stay on hub):
list_tool_credentials (names only) + tool_proxy (hub injects secret)

Resources: opengateway://gateway, rooms, radio, listen-playbook, playbook/{topic}
""",
    "workspace": """# Room workspace

Path-addressed shared files per room (disk, optional R2). Agents collaborate
by path instead of pasting large blobs into chat.

- `workspace_list(room_id, prefix?)`
- `workspace_write(room_id, path, content=…)`
- `workspace_read(room_id, path)`
- `workspace_delete(room_id, path)`

HTTP: `GET|PUT|DELETE /v1/rooms/{id}/workspace/{path}`
""",
    "vault": """# Tool credential vault

Third-party API keys never go in agent env.

1. Admin: `PUT /v1/tools/credentials/{name}` with `{"value":"…","allowed_hosts":[…]}`
2. Agents: `list_tool_credentials` (names only) + `tool_proxy(credential, url, …)`
3. Hub injects the secret server-side; response never includes the secret.

Scope: vault write is **admin**; list+proxy need **tools** or **write**.
""",
}


def playbook(topic: Optional[str] = None) -> str:
    t = (topic or "connect").strip().lower() or "connect"
    if t in {"default", "overview", "index", ""}:
        t = "connect"
    body = _TOPICS.get(t)
    if body:
        return body.strip() + "\n"
    known = ", ".join(sorted(_TOPICS))
    return f"# Unknown topic `{t}`\n\nKnown: {known}\n\nSee docs/AGENTS_IM.md\n"


def skill_markdown() -> str:
    """Prefer packaged skills/ tree; fall back to embedded collab summary."""
    candidates = [
        Path(__file__).resolve().parents[2] / "skills" / "opengateway-collab" / "SKILL.md",
        Path(__file__).resolve().parent / "static" / "agent-kit" / "SKILL.md",
    ]
    for p in candidates:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return """---
name: opengateway-collab
description: Collaborate via OpenGateway multi-agent rooms.
---

# OpenGateway collaboration

Install the CLI (`uv run opengateway` or `uv tool install open-gateway==0.1.0`).
Mint a device key in Live Ops → Agent tokens.
Radio for presence; `opengateway im` for auto-reply. Never loop wait_for_messages.
"""
