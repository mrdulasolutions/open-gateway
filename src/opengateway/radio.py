"""Always-on radio — delegates to unified seat_runtime (cursor + delivery dedupe).

See docs/AGENTS_RADIO.md.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from opengateway.seat_runtime import seat_runtime


def _auth_headers(token: str = "") -> dict[str, str]:
    t = (
        token
        or os.environ.get("OPENGATEWAY_AUTH_TOKEN")
        or os.environ.get("OPENGATEWAY_TOKEN")
        or ""
    ).strip()
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}


class RadioManager:
    """Compatibility wrapper around SeatRuntimeManager."""

    def start(
        self,
        *,
        room_id: str,
        participant_id: str,
        name: str = "agent",
        base_url: str = "",
        auth_token: str = "",
        timeout: float = 45.0,
        restart: bool = True,
        on_wake: Optional[Callable[[list[dict[str, Any]]], None]] = None,
        peer_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return seat_runtime.start(
            room_id=room_id,
            participant_id=participant_id,
            name=name,
            base_url=base_url,
            auth_token=auth_token,
            timeout=timeout,
            restart=restart,
            register=True,
            on_wake=on_wake,
            peer_names=peer_names,
        )

    def stop(self, room_id: str = "", participant_id: str = "") -> dict[str, Any]:
        return seat_runtime.stop(room_id=room_id, participant_id=participant_id)

    def stop_all(self) -> dict[str, Any]:
        return seat_runtime.stop()

    def list_sessions(self) -> list[dict[str, Any]]:
        return seat_runtime.list_sessions()

    def drain_inbox(
        self, participant_id: str = "", room_id: str = "", max_items: int = 100
    ) -> dict[str, Any]:
        return seat_runtime.drain_inbox(
            participant_id=participant_id, room_id=room_id, max_items=max_items
        )

    def peek_inbox(
        self, participant_id: str = "", room_id: str = "", limit: int = 20
    ) -> dict[str, Any]:
        return seat_runtime.peek_inbox(
            participant_id=participant_id, room_id=room_id, limit=limit
        )


radio = RadioManager()
