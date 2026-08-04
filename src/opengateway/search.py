"""Predictive global search across rooms, messages, tasks, participants, etc."""

from __future__ import annotations

import re
from typing import Any, Optional

from opengateway.store import Store


def _score(query: str, *fields: Optional[str]) -> float:
    """Simple predictive rank: prefix > word-start > substring; shorter field boosts."""
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    best = 0.0
    tokens = [t for t in re.split(r"\s+", q) if t]
    for field in fields:
        if not field:
            continue
        f = field.lower()
        s = 0.0
        if f == q:
            s = 100.0
        elif f.startswith(q):
            s = 80.0 - min(len(f), 40) * 0.2
        elif f" {q}" in f" {f}":
            s = 60.0
        elif q in f:
            s = 40.0 + (10.0 if f.find(q) < 8 else 0.0)
        # multi-token AND soft match
        if tokens and all(t in f for t in tokens):
            s = max(s, 55.0)
        # fuzzy-ish: ordered char coverage
        if s < 30 and len(q) >= 2:
            i = 0
            for ch in f:
                if i < len(q) and ch == q[i]:
                    i += 1
            if i == len(q):
                s = max(s, 25.0)
        best = max(best, s)
    return best


_TYPE_ALIASES = {
    "room": "room",
    "rooms": "room",
    "participant": "participant",
    "participants": "participant",
    "agent": "participant",
    "agents": "participant",
    "people": "participant",
    "message": "message",
    "messages": "message",
    "msg": "message",
    "chat": "message",
    "room-message": "message",
    "dm": "dm",
    "dms": "dm",
    "direct": "dm",
    "private": "dm",
    "task": "task",
    "tasks": "task",
    "artifact": "artifact",
    "artifacts": "artifact",
    "file": "artifact",
    "files": "artifact",
    "bookmark": "bookmark",
    "bookmarks": "bookmark",
    "fork": "fork",
    "forks": "fork",
}

# Human-readable labels for UI
TYPE_LABELS = {
    "room": "Room",
    "participant": "Agent",
    "message": "Room msg",
    "dm": "DM",
    "task": "Task",
    "artifact": "File",
    "bookmark": "Bookmark",
    "fork": "Fork",
}


def _parse_query(query: str) -> tuple[str, Optional[str]]:
    """Support predictive filters: type:room, in:room-name, #task."""
    q = (query or "").strip()
    type_filter: Optional[str] = None
    # type:xxx or t:xxx
    m = re.search(r"\b(?:type|t):([a-zA-Z_-]+)\b", q, re.I)
    if m:
        type_filter = _TYPE_ALIASES.get(m.group(1).lower())
        q = (q[: m.start()] + q[m.end() :]).strip()
    # Leading "rooms " style
    for alias, canon in _TYPE_ALIASES.items():
        if q.lower().startswith(alias + " ") and len(q) > len(alias) + 1:
            type_filter = type_filter or canon
            q = q[len(alias) :].strip()
            break
    return q, type_filter


async def global_search(
    store: Store,
    query: str,
    *,
    limit: int = 40,
    room_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    include_dms: bool = False,
    for_participant: Optional[str] = None,
) -> dict[str, Any]:
    raw = (query or "").strip()
    q, type_filter = _parse_query(raw)
    hits: list[dict[str, Any]] = []
    if len(raw) < 1:
        return {"query": raw, "hits": [], "suggestions": await _empty_suggestions(store)}

    # type-only query (e.g. "type:task") — list recent of that type
    search_q = q if q else raw
    rooms = await store.list_rooms(tenant_id=tenant_id)
    if room_id:
        rooms = [r for r in rooms if r.id == room_id]
    # Tenant-scoped callers: hide legacy null-tenant rooms
    if tenant_id is not None:
        rooms = [r for r in rooms if r.tenant_id == tenant_id]

    def _want(t: str) -> bool:
        return type_filter is None or type_filter == t

    for room in rooms:
        if _want("room"):
            sc = _score(search_q, room.name, room.goal, room.project_path or "", room.id)
            # type-only: include all rooms with baseline score
            if not q and type_filter == "room":
                sc = max(sc, 50.0)
            if sc >= 25:
                hits.append(
                    {
                        "type": "room",
                        "type_label": TYPE_LABELS["room"],
                        "score": sc,
                        "id": room.id,
                        "title": room.name,
                        "subtitle": room.goal or "Room",
                        "room_id": room.id,
                        "path": f"/ui/#room={room.id}",
                    }
                )

        if _want("participant"):
            for p in await store.list_participants(room.id):
                sc = _score(search_q, p.name, p.harness.value, p.role, " ".join(p.capabilities))
                if not q and type_filter == "participant":
                    sc = max(sc, 50.0)
                if sc >= 25:
                    hits.append(
                        {
                            "type": "participant",
                            "type_label": TYPE_LABELS["participant"],
                            "score": sc,
                            "id": p.id,
                            "title": p.name,
                            "subtitle": f"Agent · {p.harness.value} · {p.status.value} · {room.name}",
                            "room_id": room.id,
                            "path": f"/ui/#room={room.id}&dm={p.id}",
                        }
                    )

        # Room messages vs private DMs — DMs only when explicitly allowed for a participant
        if _want("message") or (_want("dm") and (include_dms or for_participant)):
            msgs = await store.list_messages(
                room.id,
                limit=300,
                for_participant=for_participant,
                include_dms=bool(include_dms and not for_participant),
            )
            for m in msgs:
                text = m.message.text() if hasattr(m.message, "text") else ""
                is_dm = bool(m.to_participant_id)
                kind = "dm" if is_dm else "message"
                if not _want(kind):
                    continue
                # Never surface foreign DMs
                if is_dm and for_participant and not (
                    m.to_participant_id == for_participant
                    or m.from_participant_id == for_participant
                ):
                    continue
                if is_dm and not for_participant and not include_dms:
                    continue
                sc = _score(search_q, text, m.from_name, "dm" if is_dm else "room")
                # type-only listing
                if not q and type_filter == kind:
                    sc = max(sc, 50.0)
                if sc >= 30 or (not q and type_filter == kind and sc >= 25):
                    peer_name = ""
                    if is_dm and m.to_participant_id:
                        peer = await store.get_participant(m.to_participant_id)
                        peer_name = peer.name if peer else m.to_participant_id[:8]
                    hits.append(
                        {
                            "type": kind,
                            "type_label": TYPE_LABELS.get(kind, kind),
                            "score": sc,
                            "id": m.id,
                            "title": (text[:80] + "…") if len(text) > 80 else text or "(empty)",
                            "subtitle": (
                                f"DM · {m.from_name} → {peer_name} · {room.name}"
                                if is_dm
                                else f"Room · {m.from_name} · {room.name}"
                            ),
                            "room_id": room.id,
                            "path": (
                                f"/ui/#room={room.id}&dm_from={m.from_participant_id}"
                                f"&dm_to={m.to_participant_id}&msg={m.id}"
                                if is_dm
                                else f"/ui/#room={room.id}&msg={m.id}"
                            ),
                            "meta": {
                                "is_dm": is_dm,
                                "from_name": m.from_name,
                                "from_participant_id": m.from_participant_id,
                                "to_participant_id": m.to_participant_id,
                            },
                        }
                    )

        if _want("task"):
            for t in await store.list_tasks(room.id):
                sc = _score(search_q, t.title, t.description, t.status.value, t.result or "")
                if not q and type_filter == "task":
                    sc = max(sc, 50.0)
                if sc >= 25:
                    hits.append(
                        {
                            "type": "task",
                            "type_label": TYPE_LABELS["task"],
                            "score": sc,
                            "id": t.id,
                            "title": t.title,
                            "subtitle": f"Task · {t.status.value} · {room.name}",
                            "room_id": room.id,
                            "path": f"/ui/#room={room.id}&task={t.id}",
                        }
                    )

        if _want("artifact"):
            for a in await store.list_artifacts(room.id):
                sc = _score(search_q, a.name, a.content_type, a.description)
                if not q and type_filter == "artifact":
                    sc = max(sc, 50.0)
                if sc >= 25:
                    hits.append(
                        {
                            "type": "artifact",
                            "type_label": TYPE_LABELS["artifact"],
                            "score": sc,
                            "id": a.id,
                            "title": a.name,
                            "subtitle": f"File · {a.content_type} · {room.name}",
                            "room_id": room.id,
                            "path": f"/ui/#room={room.id}",
                        }
                    )

        if _want("bookmark"):
            for b in await store.list_bookmarks(room.id):
                sc = _score(search_q, b.title, b.excerpt)
                if not q and type_filter == "bookmark":
                    sc = max(sc, 50.0)
                if sc >= 25:
                    hits.append(
                        {
                            "type": "bookmark",
                            "type_label": TYPE_LABELS["bookmark"],
                            "score": sc,
                            "id": b.id,
                            "title": b.title,
                            "subtitle": b.excerpt[:80] if b.excerpt else room.name,
                            "room_id": room.id,
                            "path": f"/ui/#room={room.id}&msg={b.message_id}",
                        }
                    )

        if _want("fork"):
            for f in await store.list_forks(room.id):
                sc = _score(search_q, f.title, f.note, f.created_by_name)
                if not q and type_filter == "fork":
                    sc = max(sc, 50.0)
                if sc >= 25:
                    hits.append(
                        {
                            "type": "fork",
                            "type_label": TYPE_LABELS["fork"],
                            "score": sc,
                            "id": f.id,
                            "title": f.title,
                            "subtitle": f"Fork · {room.name}",
                            "room_id": room.id,
                            "path": (
                                f"/ui/#room={f.forked_room_id}"
                                if f.forked_room_id
                                else f"/ui/#room={room.id}&msg={f.root_message_id}"
                            ),
                        }
                    )

    hits.sort(key=lambda h: (-h["score"], h["type"], h["title"]))
    hits = hits[:limit]

    # Predictive suggestions: top titles + type filters
    suggestions = _suggestions(raw, hits)
    return {
        "query": raw,
        "parsed": {"text": q, "type": type_filter},
        "hits": hits,
        "suggestions": suggestions,
    }


async def _empty_suggestions(store: Store) -> list[dict[str, str]]:
    rooms = await store.list_rooms()
    out: list[dict[str, str]] = [
        {"label": "type:dm", "query": "type:dm ", "type": "dm"},
        {"label": "type:message", "query": "type:message ", "type": "message"},
        {"label": "type:room", "query": "type:room ", "type": "room"},
        {"label": "type:task", "query": "type:task ", "type": "task"},
        {"label": "type:agent", "query": "type:agent ", "type": "participant"},
    ]
    for r in rooms[:5]:
        out.append({"label": r.name, "query": r.name})
    return out


def _suggestions(q: str, hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    ql = q.lower()
    # Type completions first when predictive
    for t, label in (
        ("dm", "DM"),
        ("message", "Room msg"),
        ("room", "Room"),
        ("participant", "Agent"),
        ("task", "Task"),
        ("artifact", "File"),
        ("bookmark", "Bookmark"),
        ("fork", "Fork"),
    ):
        if (
            "type:" in ql
            or t.startswith(ql)
            or ql.startswith("t:")
            or label.lower().startswith(ql)
            or (len(ql) >= 1 and t.startswith(ql.replace("type:", "").replace("t:", "")))
        ):
            out.append({"label": f"type:{t}", "query": f"type:{t} ", "type": t})
    for h in hits[:12]:
        label = h["title"][:60]
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append(
            {
                "label": f"[{h.get('type_label') or h['type']}] {label}"[:50],
                "query": label,
                "type": h["type"],
            }
        )
    return out[:15]
