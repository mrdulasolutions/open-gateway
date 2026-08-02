"""OpenGateway CLI — serve, mcp, status, demo helpers."""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8765, help="Bind port"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
) -> None:
    """Start the OpenGateway HTTP server (ACP + collaboration API)."""
    console.print(f"[bold green]OpenGateway[/] listening on http://{host}:{port}")
    console.print("  ACP agents:  GET  /agents")
    console.print("  ACP runs:    POST /runs")
    console.print("  Rooms:       /v1/rooms")
    console.print("  Docs:        /docs")
    console.print("  MCP bridge:  opengateway mcp   (stdio, point harnesses at it)")
    uvicorn.run(
        "opengateway.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@app.command()
def mcp() -> None:
    """Run the MCP stdio server (for Claude Code / Cursor / Grok CLI).

    Set OPENGATEWAY_URL (default http://127.0.0.1:8765) to point at the HTTP hub.
    Optional: OPENGATEWAY_HARNESS, OPENGATEWAY_AGENT_NAME.
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
    """Ping the gateway and list rooms / agents."""
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=5.0) as c:
            ping = c.get("/ping").json()
            rooms = c.get("/v1/rooms").json().get("rooms", [])
            agents = c.get("/agents").json().get("agents", [])
    except Exception as e:
        console.print(f"[red]Cannot reach OpenGateway at {url}: {e}[/]")
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
    with httpx.Client(base_url=url.rstrip("/"), timeout=10.0) as c:
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
    with httpx.Client(base_url=url.rstrip("/"), timeout=15.0) as c:
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


@app.callback()
def main() -> None:
    """OpenGateway CLI entrypoint."""
    # Typer callback for package script wiring
    pass


if __name__ == "__main__":
    app()
