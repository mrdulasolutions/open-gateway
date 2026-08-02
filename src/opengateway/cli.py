"""OpenGateway CLI — serve, mcp, status, demo, realtime chat helpers."""

from __future__ import annotations

import json
import os
import sys
from typing import Optional
from urllib.parse import urlparse

import httpx
import typer
import uvicorn
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="opengateway",
    help="OpenGateway — multi-agent collaboration hub (ACP + MCP).",
    no_args_is_help=True,
)
console = Console()


def _base_url(url: Optional[str] = None) -> str:
    return (url or os.environ.get("OPENGATEWAY_URL", "http://127.0.0.1:8765")).rstrip("/")


def _auth_headers() -> dict[str, str]:
    token = (os.environ.get("OPENGATEWAY_AUTH_TOKEN") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _http(timeout: float = 15.0, url: Optional[str] = None) -> httpx.Client:
    """Shared HTTP client with optional Bearer auth for public/LAN gateways."""
    return httpx.Client(
        base_url=_base_url(url),
        timeout=timeout,
        headers=_auth_headers(),
    )


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        None,
        help="Bind host (default: 127.0.0.1 internal/serve, 0.0.0.0 open public)",
    ),
    port: Optional[int] = typer.Option(
        None,
        help="Bind port (default: OPENGATEWAY_PORT or PORT or 8765)",
    ),
    mode: str = typer.Option(
        "internal",
        help="Mode: internal | public | serve (alias: tailscale) | funnel",
    ),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network label: loopback | lan | tailscale | funnel | public",
    ),
    via: Optional[str] = typer.Option(
        None,
        "--via",
        help="Exposure path: serve (Tailscale Serve → localhost) | funnel | open",
    ),
    token: Optional[str] = typer.Option(
        None,
        help="Bearer auth token (required for public unless OPENGATEWAY_REQUIRE_AUTH=false)",
    ),
    public_url: Optional[str] = typer.Option(
        None,
        help="Advertised URL (e.g. https://mybox.tailnet.ts.net)",
    ),
    trust_tailscale_identity: Optional[bool] = typer.Option(
        None,
        "--trust-tailscale-identity/--no-trust-tailscale-identity",
        help="Accept Tailscale-User-* headers when bound to localhost (Serve)",
    ),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
    db: Optional[str] = typer.Option(
        None,
        help="SQLite path for room persistence (default: ~/.opengateway/state.db). "
        "Use 'none' for memory-only.",
    ),
) -> None:
    """Start the OpenGateway HTTP server (ACP + collaboration API).

    Modes:
      internal  — loopback only, multi-agent on this machine (default)
      public    — network exposure + bearer auth
      serve     — public via Tailscale Serve (bind 127.0.0.1, print serve cmd)
      funnel    — public via Tailscale Funnel (bind 127.0.0.1, print funnel cmd)

    Examples:
      opengateway serve
      opengateway serve --mode public --network tailscale --via serve --token $TOKEN
      opengateway serve --mode serve --token $TOKEN
    """
    # Normalize serve/funnel aliases into mode + via
    mode_l = (mode or "internal").lower()
    if mode_l in {"serve", "tailscale", "tailnet"}:
        os.environ["OPENGATEWAY_MODE"] = "public"
        os.environ.setdefault("OPENGATEWAY_VIA", "serve")
        os.environ.setdefault("OPENGATEWAY_NETWORK", "tailscale")
    elif mode_l == "funnel":
        os.environ["OPENGATEWAY_MODE"] = "public"
        os.environ.setdefault("OPENGATEWAY_VIA", "funnel")
        os.environ.setdefault("OPENGATEWAY_NETWORK", "funnel")
    else:
        os.environ["OPENGATEWAY_MODE"] = mode_l

    # Resolve port: CLI flag > OPENGATEWAY_PORT > PORT (Railway) > 8765
    if port is None:
        port = int(
            os.environ.get("OPENGATEWAY_PORT")
            or os.environ.get("PORT")
            or "8765"
        )
    os.environ["OPENGATEWAY_PORT"] = str(port)
    if via:
        os.environ["OPENGATEWAY_VIA"] = via
    if network:
        os.environ["OPENGATEWAY_NETWORK"] = network
    if host:
        os.environ["OPENGATEWAY_HOST"] = host
    if token:
        os.environ["OPENGATEWAY_AUTH_TOKEN"] = token
    if public_url:
        os.environ["OPENGATEWAY_PUBLIC_URL"] = public_url
    if trust_tailscale_identity is not None:
        os.environ["OPENGATEWAY_TRUST_TAILSCALE_IDENTITY"] = (
            "true" if trust_tailscale_identity else "false"
        )
    if db is not None:
        os.environ["OPENGATEWAY_DB"] = db

    from opengateway.config import load_gateway_config, network_ui_label
    from opengateway.persistence import default_db_path

    cfg = load_gateway_config()
    bind_host = host or cfg.host
    db_path = default_db_path()
    label = network_ui_label(cfg.network)
    console.print(
        f"[bold orange1]OpenGateway[/] {cfg.mode.value} · {label} · http://{bind_host}:{port}"
    )
    console.print(f"  [bold]Network:[/] {cfg.network} · advertised {cfg.base_url}")
    if cfg.require_auth:
        tok = cfg.auth_token or ""
        masked = (tok[:8] + "…") if len(tok) > 10 else "(set)"
        console.print(f"  [bold]Auth:[/] required · token {masked}")
        console.print("  Clients: Authorization: Bearer $OPENGATEWAY_AUTH_TOKEN")
        console.print("  SSE/EventSource may use ?token=$OPENGATEWAY_AUTH_TOKEN")
    else:
        console.print("  [yellow]Auth:[/] off")
    if cfg.trust_tailscale_identity:
        console.print(
            "  [bold]Identity:[/] Tailscale-User-* headers trusted (localhost bind only)"
        )
    if cfg.tailscale_hostname:
        console.print(f"  [bold]Tailscale:[/] {cfg.tailscale_hostname}")
    if cfg.serve_hint:
        console.print(f"  [bold cyan]Run:[/] {cfg.serve_hint}")
        if cfg.network == "tailscale":
            console.print(
                "  [dim]Tailnet (Serve): edge TLS + ACLs; app stays on 127.0.0.1[/]"
            )
            console.print(
                "  [dim]Do not rely on raw 100.x:PORT — host firewalls often block it. "
                "Use Serve + MagicDNS. Diagnose: opengateway doctor[/]"
            )
        elif cfg.network == "funnel":
            console.print(
                "  [yellow]Internet (Funnel): world-reachable — keep a strong token[/]"
            )
    if cfg.network == "lan":
        console.print(
            "  [dim]LAN open bind. For Tailscale mesh prefer --mode serve + "
            "`tailscale serve` (see opengateway doctor).[/]"
        )
    if db_path:
        console.print(f"  [bold]Persistence:[/] {db_path}")
    else:
        console.print("  [yellow]Persistence:[/] off (memory-only)")
    console.print(f"  Web UI:      {cfg.base_url}/ui/")
    console.print("  Gateways:   GET /v1/gateways")
    console.print("  Search:     GET /v1/search?q=")
    uvicorn.run(
        "opengateway.server:build_app",
        factory=True,
        host=bind_host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command()
def ui(
    url: str = typer.Option(None, help="Gateway base URL"),
    no_open: bool = typer.Option(False, "--no-open", help="Print URL only"),
) -> None:
    """Open the Live Ops web UI in your browser."""
    import webbrowser

    base = _base_url(url)
    target = f"{base}/ui/"
    console.print(f"[bold green]OpenGateway UI[/] {target}")
    try:
        with _http(3.0, base) as c:
            c.get("/ping").raise_for_status()
    except Exception as e:
        console.print(f"[yellow]Gateway may be down[/] ({e})")
        console.print("Start it with: [bold]opengateway serve[/]")
    if not no_open:
        webbrowser.open(target)


@app.command()
def mcp() -> None:
    """Run the MCP stdio server (for Claude Code / Cursor / Grok CLI).

    Set OPENGATEWAY_URL (default http://127.0.0.1:8765) to point at the HTTP hub.
    Optional: OPENGATEWAY_HARNESS, OPENGATEWAY_AGENT_NAME, OPENGATEWAY_AUTH_TOKEN
    (required for public/LAN/serve gateways).
    """
    from opengateway.mcp_server import main as mcp_main

    mcp_main()


@app.command()
def status(
    url: str = typer.Option(
        os.environ.get("OPENGATEWAY_URL", "http://127.0.0.1:8765"),
        help="Gateway base URL",
    ),
) -> None:
    """Ping the gateway and list rooms / agents.

    For public/LAN gateways set OPENGATEWAY_AUTH_TOKEN (or clients get 401).
    """
    try:
        with _http(5.0, url) as c:
            ping = c.get("/ping").json()
            rooms = c.get("/v1/rooms").json().get("rooms", [])
            agents = c.get("/agents").json().get("agents", [])
    except Exception as e:
        console.print(f"[red]Cannot reach OpenGateway at {url}: {e}[/]")
        console.print("[dim]If mode is public/LAN, export OPENGATEWAY_AUTH_TOKEN[/]")
        raise typer.Exit(1)

    console.print(f"[green]OK[/] {ping}")
    table = Table(title="Rooms")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Goal")
    for r in rooms:
        table.add_row(r.get("id", ""), r.get("name", ""), r.get("status", ""), (r.get("goal") or "")[:60])
    console.print(table if rooms else "[dim]No rooms yet[/]")

    at = Table(title="ACP Agents")
    at.add_column("Name")
    at.add_column("Description")
    for a in agents:
        at.add_row(a.get("name", ""), (a.get("description") or "")[:80])
    console.print(at)


@app.command("create-room")
def create_room(
    name: str = typer.Argument(..., help="Room name"),
    goal: str = typer.Option("", help="Shared goal"),
    project_path: Optional[str] = typer.Option(None, help="Project path"),
    url: str = typer.Option(os.environ.get("OPENGATEWAY_URL", "http://127.0.0.1:8765")),
) -> None:
    """Create a room from the CLI (handy before launching agents)."""
    body = {"name": name, "goal": goal, "created_by": "cli"}
    if project_path:
        body["project_path"] = project_path
    with _http(10.0, url) as c:
        r = c.post("/v1/rooms", json=body)
        r.raise_for_status()
        data = r.json()
    console.print(json.dumps(data, indent=2))
    console.print(f"\n[bold]Room id:[/] {data['id']}")
    console.print("Agents can join with MCP join_room or:")
    console.print(f"  curl -X POST {url}/v1/rooms/{data['id']}/join -H 'Content-Type: application/json' \\")
    console.print('    -d \'{"name":"my-agent","harness":"claude-code"}\'')


@app.command()
def demo(
    url: str = typer.Option(os.environ.get("OPENGATEWAY_URL", "http://127.0.0.1:8765")),
) -> None:
    """Run an in-process two-agent simulation against a live gateway."""
    with _http(15.0, url) as c:
        try:
            c.get("/ping").raise_for_status()
        except Exception as e:
            console.print(f"[red]Start the gateway first: opengateway serve[/]\n{e}")
            raise typer.Exit(1)

        room = c.post(
            "/v1/rooms",
            json={
                "name": "demo-collab",
                "goal": "Build a hello-world CLI together",
                "project_path": os.getcwd(),
                "created_by": "demo",
            },
        ).json()
        room_id = room["id"]
        console.print(f"[cyan]Room[/] {room_id}")

        alice = c.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "alice-claude", "harness": "claude-code", "role": "coordinator", "capabilities": ["planning"]},
        ).json()
        bob = c.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": "bob-grok", "harness": "grok", "role": "contributor", "capabilities": ["coding"]},
        ).json()
        console.print(f"[green]Joined[/] alice={alice['id']} bob={bob['id']}")

        c.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": alice["id"],
                "content": "I'll plan the CLI; Bob, please implement main.py after I post the task.",
            },
        )
        task = c.post(
            f"/v1/rooms/{room_id}/tasks",
            json={
                "title": "Implement main.py hello CLI",
                "description": "argparse, --name flag, print greeting",
                "created_by": alice["id"],
            },
        ).json()
        c.patch(
            f"/v1/rooms/{room_id}/tasks/{task['id']}",
            json={"claimed_by": bob["id"], "status": "in_progress"},
        )
        c.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": bob["id"],
                "content": "Claimed the task. Implementing main.py now.",
            },
        )
        c.post(
            f"/v1/rooms/{room_id}/artifacts",
            json={
                "name": "main.py",
                "shared_by": bob["id"],
                "content": '#!/usr/bin/env python3\nimport argparse\n\ndef main():\n    p = argparse.ArgumentParser()\n    p.add_argument("--name", default="world")\n    a = p.parse_args()\n    print(f"Hello, {a.name}!")\n\nif __name__ == "__main__":\n    main()\n',
                "content_type": "text/x-python",
                "description": "Hello CLI implementation",
            },
        )
        c.patch(
            f"/v1/rooms/{room_id}/tasks/{task['id']}",
            json={"status": "done", "result": "Shared main.py artifact"},
        )
        c.post(
            f"/v1/rooms/{room_id}/messages",
            json={
                "from_participant_id": bob["id"],
                "content": "Done — artifact main.py is in the room.",
            },
        )

        # ACP facilitator summary
        run = c.post(
            "/runs",
            json={
                "agent_name": "room-facilitator",
                "input": [
                    {
                        "role": "user",
                        "parts": [{"content_type": "text/plain", "content": f"room:{room_id}"}],
                    }
                ],
            },
        ).json()
        summary = run["output"][0]["parts"][0]["content"] if run.get("output") else "(no output)"
        console.print("\n[bold]Facilitator summary[/]\n")
        console.print(summary)
        console.print(f"\n[bold]Snapshot:[/] {url}/v1/rooms/{room_id}/snapshot")


def _fmt_time(iso: Optional[str]) -> str:
    if not iso:
        return "--:--:--"
    # 2026-08-02T01:51:40.726939Z → 01:51:40
    try:
        return iso[11:19]
    except Exception:
        return iso[:8]


def _print_message_line(m: dict) -> None:
    body = ""
    parts = (m.get("message") or {}).get("parts") or []
    if parts:
        body = parts[0].get("content") or ""
    target = ""
    if m.get("to_participant_id"):
        target = f" [dim]→ dm[/]"
    t = _fmt_time(m.get("created_at"))
    name = m.get("from_name") or "?"
    console.print(f"[dim]{t}[/] [bold cyan]{name}[/]{target}: {body}")


def _print_event_line(event_type: str, payload: dict) -> None:
    t = _fmt_time(payload.get("created_at"))
    if event_type == "message" and "message" in payload.get("payload", payload):
        # support both wrapped GatewayEvent and raw
        inner = payload.get("payload", payload)
        msg = inner.get("message") if isinstance(inner, dict) else None
        if msg:
            _print_message_line(msg)
            return
    if event_type == "participant":
        action = (payload.get("payload") or {}).get("action", "")
        p = (payload.get("payload") or {}).get("participant") or {}
        name = p.get("name") or (payload.get("payload") or {}).get("participant_id", "?")
        harness = p.get("harness", "")
        if action == "joined":
            console.print(f"[dim]{t}[/] [green]● joined[/]  {name} [dim]({harness})[/]")
        elif action == "left":
            console.print(f"[dim]{t}[/] [yellow]○ left[/]    {name}")
        else:
            console.print(f"[dim]{t}[/] [green]participant[/] {action} {name}")
        return
    if event_type == "task":
        action = (payload.get("payload") or {}).get("action", "")
        task = (payload.get("payload") or {}).get("task") or {}
        console.print(
            f"[dim]{t}[/] [magenta]task[/] {action}: {task.get('title', '?')} "
            f"[dim]({task.get('status', '')})[/]"
        )
        return
    if event_type == "artifact":
        art = (payload.get("payload") or {}).get("artifact") or {}
        console.print(f"[dim]{t}[/] [blue]artifact[/] {art.get('name', '?')}")
        return
    if event_type in {"ping", ""}:
        return
    console.print(f"[dim]{t}[/] [dim]{event_type}[/] {json.dumps(payload.get('payload', {}), default=str)[:120]}")


@app.command("rooms")
def list_rooms_cmd(
    url: str = typer.Option(None, help="Gateway base URL"),
) -> None:
    """List collaboration rooms (ids for monitor/watch)."""
    base = _base_url(url)
    with _http(10.0, base) as c:
        r = c.get("/v1/rooms")
        r.raise_for_status()
        rooms = r.json().get("rooms") or []
    if not rooms:
        console.print("[dim]No rooms. Create one: opengateway create-room NAME --goal '…'[/]")
        return
    table = Table(title="OpenGateway rooms")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Goal")
    for room in rooms:
        table.add_row(
            room.get("id", ""),
            room.get("name", ""),
            room.get("status", ""),
            (room.get("goal") or "")[:50],
        )
    console.print(table)


@app.command()
def monitor(
    room_id: Optional[str] = typer.Argument(
        None,
        help="Room UUID. Omit to list rooms, or pass 'all' for global event feed.",
    ),
    url: str = typer.Option(None, help="Gateway base URL"),
    history: int = typer.Option(15, help="How many recent messages to print first"),
    messages_only: bool = typer.Option(False, "--messages-only", help="Hide joins/tasks/artifacts"),
) -> None:
    """Realtime CLI monitor for agent communication (SSE push). Ctrl-C to stop.

    Examples:
      opengateway rooms
      opengateway monitor                         # list rooms
      opengateway monitor ROOM_ID                 # live feed for one room
      opengateway monitor all                     # all rooms / system events
    """
    base = _base_url(url)

    if not room_id:
        list_rooms_cmd(url=url)
        console.print("\n[dim]Run: opengateway monitor <ROOM_ID>[/]")
        return

    # Resolve short / name match
    with _http(10.0, base) as c:
        rooms = c.get("/v1/rooms").json().get("rooms") or []
        if room_id != "all":
            match = next((r for r in rooms if r["id"] == room_id or r["id"].startswith(room_id)), None)
            if not match:
                match = next((r for r in rooms if r.get("name") == room_id), None)
            if not match:
                console.print(f"[red]Room not found:[/] {room_id}")
                list_rooms_cmd(url=url)
                raise typer.Exit(1)
            room_id = match["id"]
            console.print(
                f"[bold green]● LIVE[/] [bold]{match.get('name')}[/]  "
                f"[dim]{room_id}[/]\n"
                f"  goal: {match.get('goal') or '(none)'}  ·  {base}\n"
                f"  [dim]Ctrl-C to stop · shows messages, joins, tasks, artifacts[/]\n"
            )
            # History dump
            if history > 0:
                msgs = (
                    c.get(f"/v1/rooms/{room_id}/messages", params={"limit": history})
                    .json()
                    .get("messages")
                    or []
                )
                if msgs:
                    console.print("[dim]── recent ──[/]")
                    for m in msgs[-history:]:
                        _print_message_line(m)
                    console.print("[dim]── live ────[/]")
            sse_url = f"{base}/v1/rooms/{room_id}/events"
        else:
            console.print(f"[bold green]● LIVE[/] all rooms  [dim]{base}[/]\n")
            sse_url = f"{base}/v1/events/stream"

    _run_sse_monitor(sse_url, messages_only=messages_only)


@app.command()
def watch(
    room_id: str = typer.Argument(..., help="Room UUID or name"),
    url: str = typer.Option(None, help="Gateway base URL"),
    history: int = typer.Option(15, help="Recent messages to show first"),
    messages_only: bool = typer.Option(False, "--messages-only"),
) -> None:
    """Alias for `monitor` — realtime tail of a room. Ctrl-C to stop."""
    monitor(room_id=room_id, url=url, history=history, messages_only=messages_only)


def _run_sse_monitor(sse_url: str, *, messages_only: bool = False) -> None:
    """Connect to SSE and print events until Ctrl-C."""
    try:
        from httpx_sse import connect_sse
    except ImportError:
        console.print("[yellow]httpx-sse missing; falling back to long-poll[/]")
        _run_longpoll_monitor(sse_url)
        return

    # EventSource cannot set Authorization headers — append ?token= for public mode
    token = (os.environ.get("OPENGATEWAY_AUTH_TOKEN") or "").strip()
    if token and "token=" not in sse_url:
        sep = "&" if "?" in sse_url else "?"
        sse_url = f"{sse_url}{sep}token={token}"

    try:
        with httpx.Client(timeout=None, headers=_auth_headers()) as client:
            with connect_sse(client, "GET", sse_url) as source:
                for sse in source.iter_sse():
                    if sse.event in {"ping", ""} and not sse.data:
                        continue
                    if sse.event == "ping":
                        continue
                    try:
                        data = json.loads(sse.data) if sse.data else {}
                    except json.JSONDecodeError:
                        continue
                    etype = sse.event or data.get("type") or "event"
                    if messages_only and etype != "message":
                        continue
                    # GatewayEvent shape: {type, payload, created_at, room_id}
                    if "payload" in data and etype == "message":
                        msg = data["payload"].get("message")
                        if msg:
                            _print_message_line(msg)
                            continue
                    if etype == "message" and "message" in data:
                        _print_message_line(data["message"])
                        continue
                    if not messages_only:
                        _print_event_line(etype, data)
    except KeyboardInterrupt:
        console.print("\n[dim]monitor stopped[/]")
    except httpx.HTTPError as e:
        console.print(f"[red]SSE error:[/] {e}")
        raise typer.Exit(1)


def _run_longpoll_monitor(sse_url: str) -> None:
    """Fallback if SSE client unavailable — only works for room message wait URLs."""
    # Extract room id from .../rooms/{id}/events
    parts = sse_url.rstrip("/").split("/")
    if "rooms" not in parts:
        console.print("[red]Long-poll fallback only supports a single room SSE URL[/]")
        raise typer.Exit(1)
    room_id = parts[parts.index("rooms") + 1]
    base = sse_url.split("/v1/")[0]
    cursor = None
    try:
        while True:
            params: dict = {"timeout": 45, "limit": 50}
            if cursor:
                params["since"] = cursor
            with _http(60.0, base) as c:
                r = c.get(f"/v1/rooms/{room_id}/messages/wait", params=params)
                r.raise_for_status()
                data = r.json()
            for m in data.get("messages") or []:
                cursor = m["id"]
                _print_message_line(m)
            if data.get("next_since"):
                cursor = data["next_since"]
    except KeyboardInterrupt:
        console.print("\n[dim]monitor stopped[/]")


@app.command()
def chat(
    room_id: str = typer.Argument(..., help="Room UUID"),
    name: str = typer.Option("human", help="Your display name in the room"),
    url: str = typer.Option(None, help="Gateway base URL"),
    harness: str = typer.Option("human", help="Harness tag"),
) -> None:
    """Interactive IM over WebSocket. Type messages; remote agents appear live.

    Requires: pip/uv websockets (comes with uvicorn). Join is automatic.
    """
    try:
        import websockets  # type: ignore
    except ImportError:
        console.print("[red]Need websockets package: uv add websockets[/]")
        raise typer.Exit(1)

    import asyncio

    base = _base_url(url)
    # HTTP join first
    with _http(15.0, base) as c:
        c.get("/ping").raise_for_status()
        p = c.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": name, "harness": harness, "role": "contributor"},
        )
        p.raise_for_status()
        participant = p.json()
    pid = participant["id"]
    console.print(f"[green]Joined[/] as {name} ({pid})")
    console.print("[dim]Type a line + Enter to send. Ctrl-C to quit.[/]")

    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = f"{scheme}://{parsed.netloc}/v1/rooms/{room_id}/ws?participant_id={pid}"

    async def run() -> None:
        async with websockets.connect(ws_url) as ws:
            # Drain + print incoming in background
            stop = asyncio.Event()

            async def reader() -> None:
                try:
                    async for raw in ws:
                        data = json.loads(raw)
                        if data.get("type") == "message":
                            m = data["message"]
                            if m.get("from_participant_id") == pid:
                                continue  # skip own echo via bus
                            body = ""
                            parts = (m.get("message") or {}).get("parts") or []
                            if parts:
                                body = parts[0].get("content") or ""
                            console.print(f"\n[cyan]{m.get('from_name')}[/]: {body}")
                            console.print("> ", end="")
                        elif data.get("type") == "hello_ok":
                            pass
                        elif data.get("type") == "error":
                            console.print(f"[red]error:[/] {data.get('detail')}")
                except Exception:
                    stop.set()

            reader_task = asyncio.create_task(reader())

            loop = asyncio.get_event_loop()

            def stdin_lines():
                for line in sys.stdin:
                    yield line.rstrip("\n")

            try:
                while not stop.is_set():
                    console.print("> ", end="")
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if not line:
                        break
                    text = line.rstrip("\n")
                    if not text:
                        continue
                    if text in {"/quit", "/exit"}:
                        break
                    await ws.send(json.dumps({"type": "message", "content": text}))
            except KeyboardInterrupt:
                pass
            finally:
                reader_task.cancel()

    asyncio.run(run())


@app.command()
def doctor(
    url: str = typer.Option(None, help="Gateway base URL"),
    port: int = typer.Option(8765, help="Port to probe locally"),
) -> None:
    """Diagnose network path — Tailscale Serve vs raw 100.x / LAN."""
    from opengateway.tailscale import network_diagnostics, tailscale_status

    base = _base_url(url)
    console.print(f"[bold]OpenGateway doctor[/] · {base}\n")

    # Local tailscale
    ts = tailscale_status()
    console.print(f"[bold]Tailscale installed:[/] {ts['installed']}")
    if ts["installed"]:
        console.print(f"  running: {ts.get('running')}  state: {ts.get('backend_state')}")
        console.print(f"  MagicDNS: {ts.get('dns_name') or '—'}")
        console.print(f"  Tailscale IPs: {', '.join(ts.get('tailscale_ips') or []) or '—'}")
        console.print(f"  Serve configured: {ts.get('serve_configured')}")
    for h in ts.get("hints") or []:
        console.print(f"  [yellow]•[/] {h}")

    diag = network_diagnostics(port)
    console.print("\n[bold]TCP probes[/]")
    for p in diag.get("probes") or []:
        mark = "[green]ok[/]" if p.get("ok") else f"[red]fail[/] ({p.get('error')})"
        console.print(f"  {p.get('host')}:{p.get('port')} → {mark}")

    rec = diag.get("recommended") or {}
    console.print(f"\n[bold]Recommended multi-machine path:[/] {rec.get('mode')}")
    console.print(f"  [dim]{rec.get('why')}[/]")
    for cmd in rec.get("commands") or []:
        console.print(f"  [cyan]$[/] {cmd}")

    # Live gateway if reachable
    try:
        with _http(5.0, base) as c:
            ping = c.get("/ping").json()
            net = c.get("/v1/network").json()
        console.print(f"\n[green]Gateway reachable[/] mode={ping.get('mode')} network={ping.get('network')}")
        console.print(f"  base_url={ping.get('base_url')}")
        if net.get("tailscale", {}).get("dns_name"):
            console.print(f"  tailnet DNS={net['tailscale']['dns_name']}")
    except Exception as e:
        console.print(f"\n[yellow]Gateway not reachable at {base}:[/] {e}")
        console.print("  Start with: opengateway serve --mode serve --token $TOKEN")


@app.command()
def pair(
    room: Optional[str] = typer.Option(None, help="Room UUID or name to deep-link"),
    label: str = typer.Option("mobile", help="Device label"),
    url: str = typer.Option(None, help="Gateway base URL"),
    ttl: int = typer.Option(900, help="Code lifetime seconds"),
) -> None:
    """Create a phone pair link (open on mobile for Live Ops).

    Prints a URL + short code. Phone opens the UI, joins the room as harness=mobile.
    Requires OPENGATEWAY_AUTH_TOKEN when the gateway is public/LAN.
    """
    base = _base_url(url)
    room_id = None
    with _http(10.0, base) as c:
        try:
            c.get("/ping").raise_for_status()
        except Exception as e:
            console.print(f"[red]Cannot reach {base}: {e}[/]")
            raise typer.Exit(1)
        if room:
            rooms = c.get("/v1/rooms").json().get("rooms") or []
            match = next(
                (
                    r
                    for r in rooms
                    if r.get("id") == room
                    or r.get("name") == room
                    or str(r.get("id", "")).startswith(room)
                ),
                None,
            )
            if not match:
                console.print(f"[red]Room not found:[/] {room}")
                raise typer.Exit(1)
            room_id = match["id"]
            console.print(f"[dim]Room[/] {match.get('name')} ({room_id[:8]}…)")
        body: dict = {"label": label, "ttl_seconds": ttl}
        if room_id:
            body["room_id"] = room_id
        r = c.post("/v1/pair", json=body)
        r.raise_for_status()
        data = r.json()
    console.print(f"\n[bold green]Pair code[/]  {data.get('code')}")
    console.print(f"[bold]URL[/]       {data.get('url')}")
    console.print(f"[dim]Expires in {data.get('ttl_seconds')}s · max {data.get('max_uses')} uses[/]")
    for line in data.get("instructions") or []:
        console.print(f"  • {line}")
    console.print(
        "\n[dim]Tip: multi-machine over Tailscale → "
        "`opengateway serve --mode serve` then `tailscale serve --bg 8765`[/]"
    )


@app.command("agent-loop")
def agent_loop(
    room: str = typer.Argument(..., help="Room UUID or name"),
    name: str = typer.Option(
        os.environ.get("OPENGATEWAY_AGENT_NAME") or "agent-loop",
        help="Join as this name",
    ),
    harness: str = typer.Option(
        os.environ.get("OPENGATEWAY_HARNESS") or "grok",
        help="Harness tag",
    ),
    role: str = typer.Option("contributor", help="Role"),
    url: str = typer.Option(None, help="Gateway base URL"),
    timeout: float = typer.Option(45.0, help="Long-poll timeout seconds"),
) -> None:
    """Stay present in a room: join + long-poll forever (production remote agents).

    Use when MCP is not loaded (fresh Grok session) or you want a dumb listen loop.
    Set OPENGATEWAY_URL + OPENGATEWAY_AUTH_TOKEN for public/LAN hubs.

    Cursor: always use the top-level ``next_since`` / ``last_id`` from the wait
    response as the next ``since`` value (never dig nested fields).
    """
    base = _base_url(url)
    with _http(15.0, base) as c:
        try:
            c.get("/ping").raise_for_status()
        except Exception as e:
            console.print(f"[red]Cannot reach {base}: {e}[/]")
            raise typer.Exit(1)
        rooms = c.get("/v1/rooms").json().get("rooms") or []
        room_id = room
        if not any(r.get("id") == room for r in rooms):
            match = next(
                (r for r in rooms if r.get("name") == room or r.get("id", "").startswith(room)),
                None,
            )
            if not match:
                console.print(f"[red]Room not found:[/] {room}")
                raise typer.Exit(1)
            room_id = match["id"]
        p = c.post(
            f"/v1/rooms/{room_id}/join",
            json={"name": name, "harness": harness, "role": role},
        )
        p.raise_for_status()
        participant = p.json()
    pid = participant["id"]
    console.print(f"[green]agent-loop[/] joined {name} ({pid[:8]}…) room {room_id[:8]}…")
    console.print(f"[dim]{base} · long-poll {timeout}s · Ctrl-C to stop[/]\n")

    since: Optional[str] = None
    try:
        while True:
            params: dict = {
                "timeout": timeout,
                "limit": 50,
                "for_participant": pid,
            }
            if since:
                params["since"] = since
            with _http(timeout + 15.0, base) as c:
                r = c.get(f"/v1/rooms/{room_id}/messages/wait", params=params)
                r.raise_for_status()
                data = r.json()
            msgs = data.get("messages") or []
            # Prefer top-level cursor (production-ready contract)
            since = data.get("next_since") or data.get("last_id") or since
            if msgs:
                for m in msgs:
                    since = m.get("id") or since
                    _print_message_line(m)
            elif data.get("timed_out"):
                console.print("[dim]…listening[/]", end="\r")
    except KeyboardInterrupt:
        console.print("\n[dim]agent-loop stopped[/]")


@app.callback()
def main() -> None:
    """OpenGateway CLI entrypoint."""
    # Typer callback for package script wiring
    pass


if __name__ == "__main__":
    app()
