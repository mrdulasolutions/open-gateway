"""Who may see a directed (private) room message on fan-out paths."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def dm_visible_to_participant(
    msg: Mapping[str, Any],
    participant_id: Optional[str],
    *,
    admin: bool = False,
) -> bool:
    """Return True if this message payload may be delivered to the seat."""
    to_id = msg.get("to_participant_id")
    if not to_id:
        return True
    if admin:
        return True
    if not participant_id:
        return False
    from_id = msg.get("from_participant_id")
    return participant_id in {to_id, from_id}
