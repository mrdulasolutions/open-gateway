"""Web Push for mobile / desktop notifications (optional pywebpush)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

log = logging.getLogger("opengateway.push")


def vapid_configured() -> bool:
    return bool(
        os.environ.get("OPENGATEWAY_VAPID_PRIVATE", "").strip()
        and os.environ.get("OPENGATEWAY_VAPID_PUBLIC", "").strip()
    )


def vapid_public_key() -> Optional[str]:
    return os.environ.get("OPENGATEWAY_VAPID_PUBLIC", "").strip() or None


def vapid_claims() -> dict[str, str]:
    contact = os.environ.get("OPENGATEWAY_VAPID_CONTACT", "mailto:ops@localhost").strip()
    return {"sub": contact}


def send_web_push(
    subscription: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[bool, Optional[str]]:
    """Send one Web Push. Returns (ok, error)."""
    if not vapid_configured():
        return False, "VAPID not configured"
    try:
        from pywebpush import webpush, WebPushException  # type: ignore
    except ImportError:
        return False, "pywebpush not installed — uv sync --extra push"

    private = os.environ["OPENGATEWAY_VAPID_PRIVATE"].strip()
    public = os.environ["OPENGATEWAY_VAPID_PUBLIC"].strip()
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=private,
            vapid_claims=vapid_claims(),
            vapid_public_key=public,
        )
        return True, None
    except Exception as e:  # WebPushException or other
        log.warning("web push failed: %s", e)
        return False, str(e)


def notification_for_message(
    *,
    room_id: str,
    from_name: str,
    preview: str,
    is_dm: bool,
) -> dict[str, Any]:
    title = f"DM from {from_name}" if is_dm else f"{from_name} in room"
    body = (preview or "").strip()[:160] or "New message"
    return {
        "title": title,
        "body": body,
        "room_id": room_id,
        "tag": f"og-msg-{room_id}",
        "data": {"room_id": room_id, "type": "message"},
    }


def notification_for_task(
    *,
    room_id: str,
    title: str,
    assignee: str,
) -> dict[str, Any]:
    return {
        "title": "Task assigned",
        "body": f"{title} → {assignee}"[:160],
        "room_id": room_id,
        "tag": f"og-task-{room_id}",
        "data": {"room_id": room_id, "type": "task"},
    }
