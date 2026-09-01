"""OpenGateway HTTP server — ACP-compatible + collaboration REST API."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from opengateway import __version__
from opengateway.acp_handlers import BUILTIN_AGENTS, dispatch_acp_run
from opengateway.audit import audit_enabled
from opengateway.auth import (
    BearerAuthMiddleware,
    auth_failures_blocked,
    clear_auth_failures,
    client_ip,
    record_auth_failure,
)
from opengateway.config import GatewayConfig, load_gateway_config, local_ips, network_ui_label
from opengateway.redis_bus import try_create_bus
from opengateway.security import (
    cors_credentials_for_origins,
    is_admin_principal,
    resolve_auth,
    resolve_room_file_path,
    room_visible_to,
    safe_upload_filename,
    tenant_must_isolate,
    token_from_websocket,
)
from opengateway.models import (
    AgentManifest,
    AgentsListResponse,
    Artifact,
    Bookmark,
    CreateBookmarkRequest,
    CreateForkRequest,
    CreateRoomRequest,
    CreateTaskRequest,
    Fork,
    GatewayEvent,
    GatewayRecord,
    Harness,
    JoinRoomRequest,
    Message,
    MessagePart,
    Participant,
    ParticipantStatus,
    PostMessageRequest,
    RegisterAgentRequest,
    RegisterGatewayRequest,
    RegisteredAgent,
    Room,
    RoomMessage,
    RoomStatus,
    Run,
    RunCreateRequest,
    RunStatus,
    ShareArtifactRequest,
    Task,
    TaskStatus,
    UpdateParticipantRequest,
    UpdateRoomRequest,
    UpdateTaskRequest,
    new_id,
    text_message,
    utcnow,
)
from opengateway.search import global_search
from opengateway.store import Store
from opengateway.tailscale import network_diagnostics


def _find_web_dir() -> Path | None:
    """Locate Live Ops UI assets.

    Order:
    1. Packaged wheel data: ``opengateway/static`` (pip/uv tool install)
    2. Dev tree: ``webapp/dist`` (Vite production build)
    3. Legacy ``web/`` SPA
    """
    here = Path(__file__).resolve()
    pkg_static = here.parent / "static"
    # parents[2] is repo root only when running from a checkout (src/opengateway/…)
    root = here.parents[2] if len(here.parents) > 2 else here.parent
    candidates = [
        pkg_static,  # shipped inside the installed package
        root / "webapp" / "dist",
        Path.cwd() / "webapp" / "dist",
        root / "web",
        here.parents[1] / "web" if len(here.parents) > 1 else None,
        Path.cwd() / "web",
        Path.home() / "Code" / "OpenGateway" / "webapp" / "dist",
        Path.home() / "Code" / "OpenGateway" / "web",
    ]
    for c in candidates:
        if c is None:
            continue
        if (c / "index.html").is_file():
            return c
    return None


def create_app(
    store: Store | None = None,
    config: GatewayConfig | None = None,
) -> FastAPI:
    """Build the FastAPI app. Pass a Store, or a fresh default (SQLite unless OPENGATEWAY_DB=none)."""
    cfg = config or load_gateway_config()
    if store is not None:
        st = store
    else:
        st = Store(audit=audit_enabled(cfg.require_auth))
    # Honor config even when a custom store is passed without audit flag
    if audit_enabled(cfg.require_auth):
        st.enable_audit(True)

    # Register self in gateway registry (also used at lifespan startup)
    async def _ensure_self_gateway() -> GatewayRecord:
        self_rec = GatewayRecord(
            id=cfg.gateway_id,
            name=cfg.name,
            mode=cfg.mode.value,
            network=cfg.network,
            base_url=cfg.base_url,
            require_auth=cfg.require_auth,
            is_self=True,
            notes=network_ui_label(cfg.network) if cfg.network else "This process",
            metadata={
                "bind": f"{cfg.host}:{cfg.port}",
                "label": network_ui_label(cfg.network),
                "serve_hint": cfg.serve_hint,
                "trust_tailscale_identity": cfg.trust_tailscale_identity,
            },
        )
        for g in list(await st.list_gateways()):
            if g.is_self:
                await st.delete_gateway(g.id)
        return await st.upsert_gateway(self_rec)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus = None
        try:
            bus = await try_create_bus()
        except Exception:
            bus = None
        if bus is not None:
            st.attach_redis(bus)
            app.state.redis_bus = bus
        else:
            app.state.redis_bus = None
        await _ensure_self_gateway()
        yield
        if bus is not None:
            await bus.close()

    app = FastAPI(
        title="OpenGateway",
        description=(
            "Multi-agent collaboration hub. ACP-compatible REST for agent runs, "
            "plus room-based messaging so Grok, Claude Code, Cursor, and any "
            "ACP/MCP agent can work on the same project. "
            "Rooms persist to SQLite by default (~/.opengateway/state.db)."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.allow_origins,
        # Browsers reject credentials with origin *; never enable both
        allow_credentials=cors_credentials_for_origins(cfg.allow_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if cfg.require_auth:
        # Master OPENGATEWAY_AUTH_TOKEN and/or per-device API keys
        app.add_middleware(BearerAuthMiddleware, config=cfg, store=st)

    app.state.store = st
    app.state.gateway_config = cfg

    def _auth_ctx(request: Request) -> dict[str, Any]:
        return {
            "auth_kind": getattr(request.state, "auth_kind", None),
            "auth_scopes": getattr(request.state, "auth_scopes", None) or [],
            "tenant_id": getattr(request.state, "tenant_id", None),
            "user": getattr(request.state, "user", None),
            "api_key": getattr(request.state, "api_key", None),
        }

    async def _require_room(request: Request, room_id: str) -> Room:
        """Load room or 404; enforce tenant isolation for session/tenant API keys."""
        room = await st.get_room(room_id)
        if not room or not room_visible_to(room, _auth_ctx(request)):
            raise HTTPException(status_code=404, detail="Room not found")
        return room

    def _require_admin(request: Request) -> None:
        if not is_admin_principal(_auth_ctx(request)):
            # Master / admin scope / session admin only
            user = getattr(request.state, "user", None)
            if getattr(request.state, "auth_kind", None) == "master":
                return
            if user and user.get("role") == "admin":
                return
            scopes = getattr(request.state, "auth_scopes", None) or []
            if "admin" in scopes:
                return
            # Internal mode without auth: allow
            if not cfg.require_auth:
                return
            raise HTTPException(status_code=403, detail="Admin only")

    # ── Health / meta ──────────────────────────────────────────────────────

    @app.get("/ping")
    async def ping() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "opengateway",
            "version": __version__,
            "persistent": st.persistent,
            # Never expose credentials on public health
            "db_path": (
                "postgres"
                if getattr(st, "backend_kind", "") == "postgres"
                else ("sqlite" if st.persistent else None)
            ),
            "backend": getattr(st, "backend_kind", "memory"),
            "rooms": len(st.rooms),
            "mode": cfg.mode.value,
            "network": cfg.network,
            "require_auth": cfg.require_auth,
            "base_url": cfg.base_url,
            "audit": st._audit_enabled,
            "redis": bool(getattr(app.state, "redis_bus", None)),
            "push": __import__("opengateway.push", fromlist=["vapid_configured"]).vapid_configured(),
            "nudge_grammar": __import__(
                "opengateway.nudge_policy", fromlist=["NudgeRateLimiter"]
            ).NudgeRateLimiter.document_grammar(),
        }

    @app.get("/v1/agent/playbook")
    async def agent_playbook(topic: str = "connect") -> PlainTextResponse:
        """Public markdown playbooks for agents (no private git required)."""
        from opengateway.agent_playbook import playbook

        return PlainTextResponse(
            playbook(topic),
            media_type="text/markdown; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/v1/agent/skill")
    async def agent_skill() -> PlainTextResponse:
        """Public SKILL.md for opengateway-collab."""
        from opengateway.agent_playbook import skill_markdown

        return PlainTextResponse(
            skill_markdown(),
            media_type="text/markdown; charset=utf-8",
            headers={"Cache-Control": "public, max-age=300"},
        )

    # ── User login (multi-user / multi-tenant) ────────────────────────────

    @app.get("/v1/auth/status")
    async def auth_status() -> dict[str, Any]:
        """Public: whether first-admin registration is available."""
        s = await st.auth_status()
        s["require_auth"] = cfg.require_auth
        return s

    @app.post("/v1/auth/register")
    async def auth_register(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            return await st.register_user(
                email=str(body.get("email") or ""),
                password=str(body.get("password") or ""),
                display_name=str(body.get("display_name") or body.get("name") or ""),
                org_name=str(body.get("org_name") or body.get("organization") or ""),
                invite_code=str(body.get("invite_code") or body.get("invite") or ""),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/v1/auth/login")
    async def auth_login(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            return await st.login_user(
                email=str(body.get("email") or ""),
                password=str(body.get("password") or ""),
            )
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

    @app.post("/v1/auth/logout")
    async def auth_logout(request: Request) -> dict[str, str]:
        from opengateway.auth import _extract_token

        token = _extract_token(request) or ""
        await st.logout_session(token)
        return {"status": "logged_out"}

    @app.get("/v1/auth/me")
    async def auth_me(request: Request) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        if not user:
            # Master token / api key path
            return {
                "auth_kind": getattr(request.state, "auth_kind", None),
                "user": None,
                "tenant_id": getattr(request.state, "tenant_id", None),
            }
        return {
            "auth_kind": "session",
            "user": user,
            "tenant_id": user.get("tenant_id"),
        }

    @app.post("/v1/auth/invite")
    async def auth_invite(request: Request) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        if not user or user.get("role") != "admin":
            # Also allow master
            if getattr(request.state, "auth_kind", None) != "master":
                raise HTTPException(status_code=403, detail="Admin only")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        # Session admins may only invite into their own tenant
        if user and user.get("tenant_id"):
            tenant_id = user["tenant_id"]
        else:
            tenant_id = body.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=400, detail="tenant_id required")
        try:
            inv = await st.create_invite(
                tenant_id=str(tenant_id),
                created_by=(user or {}).get("email") or "admin",
                role=str(body.get("role") or "member"),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return inv

    @app.get("/v1/setup")
    async def setup_status() -> dict[str, Any]:
        """Public: whether the UI can one-click claim the master token (first run)."""
        import os

        disabled = os.environ.get("OPENGATEWAY_DISABLE_SETUP_CLAIM", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        claimed = st.get_meta("setup_claimed") == "1"
        has_master = bool(cfg.auth_token)
        claimable = (
            cfg.require_auth
            and has_master
            and not claimed
            and not disabled
        )
        return {
            "require_auth": cfg.require_auth,
            "claimable": claimable,
            "claimed": claimed,
            "disabled": disabled,
            "hint": (
                "Open Live Ops and click Connect — one-time claim stores the token in this browser."
                if claimable
                else (
                    "Setup already claimed — paste the OPENGATEWAY_AUTH_TOKEN you set when starting this hub."
                    if claimed
                    else "Paste bearer token in Settings, or set OPENGATEWAY_AUTH_TOKEN when you start serve."
                )
            ),
        }

    @app.post("/v1/setup/claim")
    async def setup_claim(request: Request) -> dict[str, Any]:
        """One-time: hand the master token to the first UI visitor (Railway one-click UX).

        After claim, only normal Bearer auth works. Disable with OPENGATEWAY_DISABLE_SETUP_CLAIM=true.
        """
        import os

        from opengateway.auth import client_ip, record_auth_failure, auth_failures_blocked

        ip = client_ip(request)
        if auth_failures_blocked(ip):
            raise HTTPException(status_code=429, detail="Too many attempts — wait and retry")
        disabled = os.environ.get("OPENGATEWAY_DISABLE_SETUP_CLAIM", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if disabled:
            record_auth_failure(ip)
            raise HTTPException(status_code=403, detail="Setup claim disabled on this gateway")
        if not cfg.require_auth or not cfg.auth_token:
            raise HTTPException(status_code=400, detail="Gateway does not require / have a master token")
        won = await st.claim_setup()
        if not won:
            record_auth_failure(ip)
            raise HTTPException(
                status_code=410,
                detail="Setup already claimed — use this hub's OPENGATEWAY_AUTH_TOKEN",
            )
        await st.audit(
            "setup.claim",
            ip=ip,
            resource_type="setup",
            detail={"via": "ui_bootstrap"},
        )
        return {
            "ok": True,
            "token": cfg.auth_token,
            "message": "Token saved for this browser. Mint agent keys under Agent tokens.",
        }

    @app.post("/v1/setup/reset")
    async def setup_reset(request: Request) -> dict[str, Any]:
        """Admin: re-enable one-time UI claim (requires master/admin Bearer)."""
        _require_admin(request)
        st.set_meta("setup_claimed", "0")
        if st.get_meta("setup_claimed_at"):
            st.set_meta("setup_claimed_at", "")
        return {"ok": True, "claimable": True}

    @app.get("/v1/audit")
    async def list_audit(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        action: Optional[str] = Query(None),
        room_id: Optional[str] = Query(None),
        since_id: Optional[int] = Query(None),
    ) -> dict[str, Any]:
        """Append-only audit trail — admin / master only."""
        _require_admin(request)
        if not st._audit_enabled:
            raise HTTPException(
                status_code=404,
                detail="Audit log disabled — set OPENGATEWAY_AUDIT=true or use public auth mode",
            )
        items = await st.list_audit(
            limit=limit, action=action, room_id=room_id, since_id=since_id
        )
        return {
            "audit": items,
            "count": len(items),
            "enabled": True,
        }

    # ── Per-device API keys ────────────────────────────────────────────────

    @app.get("/v1/keys")
    async def list_keys() -> dict[str, Any]:
        """List device API keys (metadata only — secrets never re-shown)."""
        return {"keys": await st.list_api_keys()}

    @app.post("/v1/keys")
    async def create_key(request: Request) -> dict[str, Any]:
        """Mint a device API key. Secret returned once in ``token``."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        meta: dict = (
            body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        )
        user = getattr(request.state, "user", None)
        if user and user.get("tenant_id"):
            meta = {**meta, "tenant_id": user["tenant_id"]}
        created = await st.create_api_key(
            name=str(body.get("name") or "device"),
            scopes=body.get("scopes"),
            role=str(body.get("role") or "contributor"),
            device_label=str(body.get("device_label") or body.get("device") or ""),
            metadata=meta,
        )
        return created

    @app.get("/v1/keys/{key_id}/usage")
    async def key_usage(key_id: str) -> dict[str, Any]:
        """Participants currently bound to this key (revoke confirm)."""
        keys = await st.list_api_keys()
        meta = next((k for k in keys if k.get("id") == key_id), None)
        if not meta:
            raise HTTPException(status_code=404, detail="Key not found")
        used_by = st.participants_for_api_key(key_id)
        names = sorted({u["name"] for u in used_by if u.get("name")})
        return {
            "id": key_id,
            "name": meta.get("name"),
            "key_prefix": meta.get("key_prefix"),
            "used_by": used_by,
            "used_by_names": names,
            "warning": (
                f"This key is used by: {', '.join(names)}. All will disconnect."
                if names
                else "No live participants bound to this key."
            ),
        }

    @app.delete("/v1/keys/{key_id}")
    async def delete_key(key_id: str, revoke: bool = Query(False)) -> dict[str, str]:
        if revoke:
            ok = await st.revoke_api_key(key_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Key not found")
            return {"status": "revoked", "id": key_id}
        ok = await st.delete_api_key(key_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Key not found")
        return {"status": "deleted", "id": key_id}

    # ── Web Push ──────────────────────────────────────────────────────────

    @app.get("/v1/push/vapid")
    async def push_vapid() -> dict[str, Any]:
        from opengateway.push import vapid_configured, vapid_public_key

        pub = vapid_public_key()
        return {
            "configured": vapid_configured(),
            "public_key": pub,
            "hint": (
                None
                if pub
                else "Set OPENGATEWAY_VAPID_PUBLIC + OPENGATEWAY_VAPID_PRIVATE (see docs/PUSH.md)"
            ),
        }

    @app.post("/v1/push/subscribe")
    async def push_subscribe(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        sub = body.get("subscription") or body
        try:
            saved = await st.save_push_subscription(
                subscription=sub if isinstance(sub, dict) else {},
                participant_id=body.get("participant_id"),
                device_label=str(body.get("device_label") or body.get("label") or ""),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return saved

    @app.get("/v1/push/subscriptions")
    async def push_list(participant_id: Optional[str] = None) -> dict[str, Any]:
        return {
            "subscriptions": await st.list_push_subscriptions(participant_id),
        }

    @app.delete("/v1/push/subscriptions/{sub_id}")
    async def push_delete(sub_id: str) -> dict[str, str]:
        ok = await st.delete_push_subscription(sub_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"status": "deleted", "id": sub_id}

    @app.get("/")
    async def root() -> RedirectResponse:
        """Browser home → Live Ops web UI."""
        return RedirectResponse(url="/ui/", status_code=302)

    @app.get("/meta")
    async def meta() -> dict[str, Any]:
        return {
            "name": "OpenGateway",
            "version": __version__,
            "protocol": "ACP-compatible + OpenGateway collaboration API",
            "ui": "/ui/",
            "docs": "/docs",
            "gateway": cfg.to_public_dict(),
            "persistence": {
                "enabled": st.persistent,
                "backend": getattr(st, "backend_kind", "memory"),
                "hint": "OPENGATEWAY_DATABASE_URL (Postgres) or OPENGATEWAY_DB (SQLite path)",
            },
            "acp": {
                "agents": "/agents",
                "runs": "/runs",
                "ping": "/ping",
            },
            "collaboration": {
                "rooms": "/v1/rooms",
                "events": "/v1/events",
                "events_stream": "/v1/events/stream",
                "search": "/v1/search",
                "gateways": "/v1/gateways",
            },
            "mcp": "Run `opengateway mcp` to expose collaboration tools over stdio MCP.",
            "security": {
                "require_auth": cfg.require_auth,
                "auth_configured": bool(cfg.auth_token),
                "mode": cfg.mode.value,
                "network": cfg.network,
                "tailscale": {
                    "hostname": cfg.tailscale_hostname,
                    "hint": "Prefer Tailscale Serve/MagicDNS for multi-machine without public internet",
                },
            },
            "network_diagnostics": network_diagnostics(cfg.port),
            "mobile": {
                "pair": "POST /v1/pair  →  phone opens redeem URL",
                "ui": "/ui/",
                "pwa": True,
            },
        }

    @app.get("/v1/network")
    async def network_status() -> dict[str, Any]:
        """Tailscale / bind diagnostics for multi-machine hardening."""
        return {
            "gateway": cfg.to_public_dict(),
            "lan_ips": local_ips(),
            **network_diagnostics(cfg.port),
        }

    def _pair_access_bases() -> dict[str, str]:
        """Known safe bases for pair QR (LAN + Tailscale MagicDNS). Same hub, different paths."""
        bases: dict[str, str] = {"advertised": cfg.base_url.rstrip("/")}
        lan = local_ips()
        if lan:
            bases["lan"] = f"http://{lan[0]}:{cfg.port}"
        ts = (cfg.tailscale_hostname or "").strip().rstrip(".")
        if ts:
            # HTTPS via Tailscale Serve (cellular/Wi‑Fi away from LAN, tailnet only)
            bases["tailscale"] = f"https://{ts}"
        return bases

    def _resolve_pair_base(requested: Optional[str]) -> str:
        """Pick pair URL origin: explicit base_url if allowed, else advertised."""
        allowed = {v.rstrip("/") for v in _pair_access_bases().values() if v}
        # Also allow any current LAN IP + configured public_url host
        for ip in local_ips():
            allowed.add(f"http://{ip}:{cfg.port}")
        if cfg.public_url:
            allowed.add(cfg.public_url.rstrip("/"))
        if cfg.tailscale_hostname:
            ts = cfg.tailscale_hostname.strip().rstrip(".")
            allowed.add(f"https://{ts}")
            allowed.add(f"http://{ts}")
            allowed.add(f"http://{ts}:{cfg.port}")

        if requested:
            r = str(requested).strip().rstrip("/")
            if r in allowed:
                return r
            # Host-only match against allowed (ignore trailing path)
            try:
                from urllib.parse import urlparse

                rh = urlparse(r if "://" in r else f"http://{r}")
                for a in allowed:
                    ah = urlparse(a)
                    if rh.hostname and rh.hostname == ah.hostname:
                        # Prefer the allowed URL's scheme/port (e.g. https MagicDNS)
                        return a.rstrip("/")
            except Exception:
                pass
        return cfg.base_url.rstrip("/")

    def _build_pair_url(base: str, code: str, room: Optional[str]) -> str:
        # Never embed master token in QR/URL (redeem mints a scoped device key)
        pair_url = f"{base.rstrip('/')}/ui/#pair={code}"
        if room:
            pair_url += f"&room={room}"
        return pair_url

    @app.post("/v1/pair")
    async def create_pair(request: Request) -> dict[str, Any]:
        """Create a short-lived phone pair code (requires gateway auth when public).

        Optional body.base_url selects which path the QR advertises (LAN vs
        Tailscale MagicDNS). Same code works on every path — only the origin
        in the QR matters for cellular vs same-Wi‑Fi.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        room_id = body.get("room_id") or None
        label = str(body.get("label") or "mobile")
        ttl_seconds = int(body.get("ttl_seconds") or 900)
        ttl_seconds = max(60, min(ttl_seconds, 3600))
        # If room is stale/missing, still issue a pair code without room deep-link
        resolved_room: Optional[str] = None
        if room_id:
            rid = str(room_id)
            if await st.get_room(rid):
                resolved_room = rid
        rec = await st.create_pair_code(
            room_id=resolved_room,
            label=label,
            ttl_seconds=ttl_seconds,
        )
        access = _pair_access_bases()
        base = _resolve_pair_base(body.get("base_url") or body.get("via_url"))
        # Prefer Tailscale when client asks network=tailscale
        net_pref = str(body.get("network") or "").lower()
        if net_pref in {"tailscale", "tailnet", "serve"} and access.get("tailscale"):
            base = access["tailscale"]
        elif net_pref in {"lan", "wifi", "local"} and access.get("lan"):
            base = access["lan"]
        pair_url = _build_pair_url(base, rec["code"], resolved_room)
        urls = {
            key: _build_pair_url(b, rec["code"], resolved_room)
            for key, b in access.items()
            if b
        }
        urls["selected"] = pair_url
        return {
            **rec,
            "room_id": resolved_room,
            "url": pair_url,
            "qr_payload": pair_url,
            "base_url": base,
            "urls": urls,
            "access": access,
            "instructions": [
                "Open the URL (or scan QR) on your phone.",
                "LAN QR → same Wi‑Fi. Tailnet QR → Tailscale app ON (works on cellular).",
                "Pair deep-links room + auth for this gateway session.",
                f"Code expires in {rec['ttl_seconds']}s · max {rec['max_uses']} uses.",
            ],
        }

    @app.post("/v1/pair/redeem")
    async def redeem_pair(request: Request) -> dict[str, Any]:
        """Redeem a pair code — returns room target + join hints.

        Public endpoint (code is the secret). Rate-limited per IP on bad codes
        using the same failure budget as bearer auth (production hardening).
        """
        ip = client_ip(request)
        if auth_failures_blocked(ip):
            raise HTTPException(
                status_code=429,
                detail="Too many failed pair attempts — wait and retry",
                headers={"Retry-After": "60"},
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = str(body.get("code") or "").strip().upper()
        name = str(body.get("name") or body.get("label") or "mobile").strip() or "mobile"
        # Normalize multi-word mobile names (spaces OK)
        name = " ".join(name.split()) or "mobile"
        if not code or len(code) > 32:
            record_auth_failure(ip)
            raise HTTPException(status_code=404, detail="Invalid or expired pair code")
        rec = await st.redeem_pair_code(code)
        if not rec:
            record_auth_failure(ip)
            raise HTTPException(status_code=404, detail="Invalid or expired pair code")
        clear_auth_failures(ip)
        # Mint a scoped device key — never hand out the master token
        device_token: Optional[str] = None
        if cfg.require_auth:
            key = await st.create_api_key(
                name=f"pair-{name}"[:48],
                scopes=["read", "write", "pair", "push"],
                role="contributor",
                device_label=name,
                metadata={
                    "via": "pair",
                    "room_id": rec.get("room_id"),
                    "pair_label": rec.get("label") or name,
                },
            )
            device_token = key.get("token")
        return {
            "ok": True,
            "code": rec["code"],
            "room_id": rec.get("room_id"),
            "label": rec.get("label") or name,
            "suggested_name": name,
            "harness": "mobile",
            "base_url": cfg.base_url,
            "require_auth": cfg.require_auth,
            "auth_token": device_token,
            "auth_hint": (
                "Use returned auth_token as Authorization: Bearer … "
                "(scoped device key — not the master token)"
                if cfg.require_auth
                else None
            ),
            "access": _pair_access_bases(),
        }

    async def _ensure_tailscale_gateway_card() -> None:
        """Advertise MagicDNS path alongside LAN self-card (cellular / away networks)."""
        ts = (cfg.tailscale_hostname or "").strip().rstrip(".")
        if not ts:
            return
        base = f"https://{ts}"
        # Skip if already present
        for g in await st.list_gateways():
            if g.base_url.rstrip("/") == base or (
                g.network == "tailscale" and not g.is_self and "magicdns" in (g.metadata or {})
            ):
                # Refresh base_url if hostname changed
                if g.base_url.rstrip("/") != base:
                    g.base_url = base
                    g.metadata = {**(g.metadata or {}), "magicdns": ts, "via": "serve"}
                    await st.upsert_gateway(g)
                return
        await st.upsert_gateway(
            GatewayRecord(
                name="tailnet-serve",
                mode="public",
                network="tailscale",
                base_url=base,
                require_auth=cfg.require_auth,
                is_self=False,
                notes=(
                    "Tailscale Serve (HTTPS MagicDNS) — same hub as LAN. "
                    "Works on cellular when the phone has Tailscale connected."
                ),
                metadata={
                    "via": "serve",
                    "magicdns": ts,
                    "pairs_with": "lan",
                    "cellular": True,
                    "serve_hint": cfg.serve_hint or f"tailscale serve --bg {cfg.port}",
                },
            )
        )

    @app.get("/v1/gateways")
    async def list_gateways() -> dict[str, Any]:
        gws = await st.list_gateways()
        # Ensure self present
        if not any(g.is_self for g in gws):
            await _ensure_self_gateway()
            gws = await st.list_gateways()
        await _ensure_tailscale_gateway_card()
        gws = await st.list_gateways()
        return {
            "gateways": [g.model_dump(mode="json") for g in gws],
            "self": cfg.to_public_dict(),
            "lan_ips": local_ips(),
            "tips": {
                "internal": "opengateway serve --mode internal  (127.0.0.1, multi-agent on one machine)",
                "public": "opengateway serve --mode public --token $TOKEN  (bind 0.0.0.0 + auth)",
                "tailscale": "opengateway serve --mode serve --token $TOKEN  then: tailscale serve --bg 8765",
                "funnel": "opengateway serve --mode funnel --token $TOKEN  then: tailscale funnel --bg 8765",
            },
            "labels": {
                "loopback": "Internal (loopback)",
                "lan": "LAN",
                "tailscale": "Tailnet (Serve)",
                "funnel": "Internet (Funnel)",
                "public": "Internet (open bind)",
            },
        }

    @app.post("/v1/gateways")
    async def register_gateway(body: RegisterGatewayRequest) -> GatewayRecord:
        gw = GatewayRecord(
            name=body.name,
            mode=body.mode,
            network=body.network,
            base_url=body.base_url.rstrip("/"),
            require_auth=body.require_auth,
            is_self=False,
            notes=body.notes,
            metadata=body.metadata,
        )
        return await st.upsert_gateway(gw)

    @app.delete("/v1/gateways/{gateway_id}")
    async def remove_gateway(gateway_id: str) -> dict[str, str]:
        g = next((x for x in await st.list_gateways() if x.id == gateway_id), None)
        if g and g.is_self:
            raise HTTPException(status_code=400, detail="Cannot delete this process's gateway card")
        ok = await st.delete_gateway(gateway_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Gateway not found")
        return {"status": "deleted"}

    @app.get("/v1/search")
    async def search(
        request: Request,
        q: str = Query("", min_length=0),
        limit: int = Query(40, ge=1, le=100),
        room_id: Optional[str] = None,
        for_participant: Optional[str] = None,
    ) -> dict[str, Any]:
        """Predictive global search — tenant-scoped; DMs only for for_participant."""
        auth = _auth_ctx(request)
        tenant_id = auth.get("tenant_id") if tenant_must_isolate(auth) else None
        if room_id:
            await _require_room(request, room_id)
        return await global_search(
            st,
            q,
            limit=limit,
            room_id=room_id,
            tenant_id=tenant_id,
            for_participant=for_participant,
            include_dms=False,
        )

    web_dir = _find_web_dir()
    if web_dir is not None:
        # html=True serves index.html for /ui/ (Hermes-style single process UI)
        app.mount(
            "/ui",
            StaticFiles(directory=str(web_dir), html=True),
            name="ui",
        )

    # ── ACP: agents ────────────────────────────────────────────────────────

    @app.get("/agents", response_model=AgentsListResponse)
    async def list_agents(
        limit: int = Query(10, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> AgentsListResponse:
        builtin = [
            AgentManifest(name=a["name"], description=a["description"], metadata=a.get("metadata", {}))
            for a in BUILTIN_AGENTS
        ]
        registered = [
            AgentManifest(
                name=a.name,
                description=a.description,
                metadata={**a.metadata, "harness": a.harness.value, "capabilities": a.capabilities},
            )
            for a in await st.list_agents()
        ]
        # Room participants also appear as live agents
        live: list[AgentManifest] = []
        seen = {m.name for m in builtin + registered}
        for room in await st.list_rooms():
            for p in await st.list_participants(room.id):
                key = f"{p.name}@{room.name}"
                if key not in seen:
                    live.append(
                        AgentManifest(
                            name=key,
                            description=f"Live participant in room '{room.name}' via {p.harness.value}",
                            metadata={
                                "org.open-gateway/participant_id": p.id,
                                "org.open-gateway/room_id": room.id,
                                "harness": p.harness.value,
                            },
                        )
                    )
                    seen.add(key)
        all_agents = builtin + registered + live
        return AgentsListResponse(agents=all_agents[offset : offset + limit])

    @app.get("/agents/{name}", response_model=AgentManifest)
    async def get_agent(name: str) -> AgentManifest:
        for a in BUILTIN_AGENTS:
            if a["name"] == name:
                return AgentManifest(name=a["name"], description=a["description"], metadata=a.get("metadata", {}))
        reg = next((a for a in await st.list_agents() if a.name == name), None)
        if reg:
            return AgentManifest(
                name=reg.name,
                description=reg.description,
                metadata={**reg.metadata, "harness": reg.harness.value},
            )
        raise HTTPException(status_code=404, detail=f"Agent not found: {name}")

    # ── ACP: runs ──────────────────────────────────────────────────────────

    @app.post("/runs")
    async def create_run(body: RunCreateRequest) -> Any:
        run = Run(
            agent_name=body.agent_name,
            session_id=body.session_id or new_id(),
            status=RunStatus.CREATED,
        )
        await st.save_run(run)
        if body.mode == "async":
            asyncio.create_task(dispatch_acp_run(run, body.input, st))
            run.status = RunStatus.IN_PROGRESS
            await st.save_run(run)
            return JSONResponse(status_code=202, content=run.model_dump(mode="json"))
        return await dispatch_acp_run(run, body.input, st)

    @app.get("/runs/{run_id}", response_model=Run)
    async def get_run(run_id: str) -> Run:
        run = await st.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @app.post("/runs/{run_id}/cancel", response_model=Run)
    async def cancel_run(run_id: str) -> JSONResponse:
        run = await st.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return JSONResponse(status_code=202, content=run.model_dump(mode="json"))
        run.status = RunStatus.CANCELLED
        run.finished_at = utcnow()
        await st.save_run(run)
        return JSONResponse(status_code=202, content=run.model_dump(mode="json"))

    # ── OpenGateway: agent registration ────────────────────────────────────

    @app.post("/v1/agents/register", response_model=RegisteredAgent)
    async def register_agent(body: RegisterAgentRequest) -> RegisteredAgent:
        agent = RegisteredAgent(
            name=body.name,
            description=body.description,
            harness=body.harness,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
        return await st.register_agent(agent)

    @app.get("/v1/agents")
    async def list_registered_agents() -> dict[str, Any]:
        return {"agents": [a.model_dump(mode="json") for a in await st.list_agents()]}

    # ── OpenGateway: rooms ─────────────────────────────────────────────────

    def _request_tenant_id(request: Request) -> Optional[str]:
        return getattr(request.state, "tenant_id", None)

    @app.post("/v1/rooms", response_model=Room)
    async def create_room(request: Request, body: CreateRoomRequest) -> Room:
        tenant_id = _request_tenant_id(request)
        room = Room(
            name=body.name,
            goal=body.goal,
            project_path=body.project_path,
            created_by=body.created_by,
            tenant_id=tenant_id,
            metadata=body.metadata,
        )
        return await st.create_room(room)

    @app.get("/v1/rooms")
    async def list_rooms(
        request: Request,
        include_archived: bool = Query(False),
    ) -> dict[str, Any]:
        auth = _auth_ctx(request)
        tenant_id = auth.get("tenant_id") if tenant_must_isolate(auth) else None
        rooms = await st.list_rooms(
            tenant_id=tenant_id, include_archived=include_archived
        )
        return {"rooms": [r.model_dump(mode="json") for r in rooms]}

    @app.get("/v1/rooms/{room_id}", response_model=Room)
    async def get_room(request: Request, room_id: str) -> Room:
        return await _require_room(request, room_id)

    @app.patch("/v1/rooms/{room_id}", response_model=Room)
    async def update_room(
        request: Request, room_id: str, body: UpdateRoomRequest
    ) -> Room:
        """Rename, edit goal, or archive/unarchive a room (id stays stable)."""
        await _require_room(request, room_id)
        try:
            return await st.patch_room(
                room_id,
                name=body.name,
                goal=body.goal,
                project_path=body.project_path,
                status=body.status,
                metadata=body.metadata,
                actor=(body.actor or "system").strip() or "system",
                announce=body.announce,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/v1/rooms/{room_id}/archive", response_model=Room)
    async def archive_room(
        request: Request,
        room_id: str,
        actor: Optional[str] = Query(None),
    ) -> Room:
        """Archive a room (hidden from default list; id preserved)."""
        await _require_room(request, room_id)
        try:
            return await st.patch_room(
                room_id,
                status=RoomStatus.ARCHIVED,
                actor=(actor or "system").strip() or "system",
                announce=True,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/v1/rooms/{room_id}/unarchive", response_model=Room)
    async def unarchive_room(
        request: Request,
        room_id: str,
        actor: Optional[str] = Query(None),
    ) -> Room:
        """Restore an archived room to open."""
        await _require_room(request, room_id)
        try:
            return await st.patch_room(
                room_id,
                status=RoomStatus.OPEN,
                actor=(actor or "system").strip() or "system",
                announce=True,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.post("/v1/rooms/{room_id}/join", response_model=Participant)
    async def join_room(request: Request, room_id: str, body: JoinRoomRequest) -> Participant:
        await _require_room(request, room_id)
        from opengateway.identity import participant_key_metadata, resolve_join_name

        api_key = getattr(request.state, "api_key", None)
        key_name = ""
        if isinstance(api_key, dict):
            key_name = str(api_key.get("name") or "").strip()
        name, name_err = resolve_join_name(
            request_name=(body.name or "").strip(),
            key_agent_name=key_name,
            harness=body.harness.value if hasattr(body.harness, "value") else str(body.harness),
            enforce_key_name=bool(key_name),
        )
        if name_err:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "agent_name_mismatch",
                    "message": name_err,
                    "token_agent_name": key_name,
                    "requested_name": (body.name or "").strip(),
                    "hint": "Mint one token per agent persona, or join with the token's name.",
                },
            )
        name = name or "agent"
        key_meta = participant_key_metadata(api_key if isinstance(api_key, dict) else None)
        meta = {**(body.metadata or {}), **key_meta}
        user = getattr(request.state, "user", None)
        if user and user.get("user_id"):
            meta.setdefault("user_id", user["user_id"])
        elif user and user.get("id"):
            meta.setdefault("user_id", user["id"])
        if not meta.get("primary_room_id"):
            meta["primary_room_id"] = room_id
        # 1) Explicit id always wins (stable client session) — same room only
        if body.participant_id and (existing := await st.get_participant(body.participant_id)):
            if existing.room_id and existing.room_id != room_id:
                # Moving seats is allowed but old roster is cleaned in store.join_room
                pass
            existing.name = name
            existing.harness = body.harness
            existing.role = body.role
            existing.capabilities = body.capabilities
            existing.metadata = {**existing.metadata, **meta}
            return await st.join_room(room_id, existing)
        # 2) Same API key in this room → reattach
        key_id = key_meta.get("api_key_id")
        if key_id:
            by_key = st.find_participant_by_api_key(room_id, str(key_id))
            if by_key:
                by_key.name = name
                by_key.harness = body.harness
                by_key.role = body.role
                by_key.capabilities = body.capabilities or by_key.capabilities
                by_key.metadata = {**by_key.metadata, **meta}
                return await st.join_room(room_id, by_key)
        # 3) Reuse same name+harness seat (kills ghost duplicates on leave/rejoin)
        twin = st.find_participant_by_identity(room_id, name, body.harness.value)
        if twin:
            twin.name = name
            twin.harness = body.harness
            twin.role = body.role
            twin.capabilities = body.capabilities or twin.capabilities
            twin.metadata = {**twin.metadata, **meta}
            return await st.join_room(room_id, twin)
        participant = Participant(
            name=name,
            harness=body.harness,
            role=body.role,
            capabilities=body.capabilities,
            metadata=meta,
        )
        return await st.join_room(room_id, participant)

    @app.patch("/v1/rooms/{room_id}/participants/{participant_id}", response_model=Participant)
    async def update_participant(
        request: Request,
        room_id: str,
        participant_id: str,
        body: UpdateParticipantRequest,
    ) -> Participant:
        """Rename or patch a participant without creating a new one."""
        await _require_room(request, room_id)
        p = await st.get_participant(participant_id)
        if not p or p.room_id != room_id:
            raise HTTPException(status_code=404, detail="Participant not found in room")
        updated = await st.update_participant(
            participant_id,
            name=body.name,
            role=body.role,
            status=body.status,
            capabilities=body.capabilities,
            metadata=body.metadata,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Participant not found")
        return updated

    @app.post("/v1/rooms/{room_id}/leave")
    async def leave_room(
        request: Request, room_id: str, participant_id: str = Query(...)
    ) -> dict[str, str]:
        await _require_room(request, room_id)
        result = await st.leave_room(room_id, participant_id)
        if not result:
            raise HTTPException(status_code=404, detail="Room or participant not found")
        return {"status": "left", "participant_id": participant_id}

    def _sort_participants(people: list[Participant]) -> list[Participant]:
        """Online first, then most recently active."""

        def key(p: Participant) -> tuple:
            online = 0 if p.status.value == "online" else 1
            seen = p.last_seen_at.timestamp() if p.last_seen_at else 0.0
            return (online, -seen, p.name.lower())

        return sorted(people, key=key)

    @app.get("/v1/rooms/{room_id}/participants")
    async def list_participants(request: Request, room_id: str) -> dict[str, Any]:
        await _require_room(request, room_id)
        people = _sort_participants(await st.list_participants(room_id))
        return {"participants": [p.model_dump(mode="json") for p in people]}

    # ── Messages ───────────────────────────────────────────────────────────

    def _is_all_call(text: str) -> bool:
        t = (text or "").lower()
        needles = (
            "everyone",
            "everybody",
            "@all",
            "@everyone",
            "all agents",
            "hey all",
            "hey everyone",
            "all of you",
            "you all",
        )
        return any(n in t for n in needles)

    def _mentioned_peers(text: str, peers: list[Participant]) -> list[Participant]:
        """Resolve @Name mentions (case-insensitive, longest name first).

        Supports multi-word identities (e.g. @Mark Dula) by matching the full
        name string after @ without requiring a single-token word boundary.
        """
        if not text or not peers:
            return []
        found: list[Participant] = []
        lower = text
        # Sort longer names first so @Mark Dula beats @Mark
        for p in sorted(peers, key=lambda x: len(x.name), reverse=True):
            name = (p.name or "").strip()
            if not name:
                continue
            # Multi-word: require end at whitespace/punct/EOL; single-token: word boundary
            if " " in name:
                pat = re.compile(
                    rf"@{re.escape(name)}(?=$|[\s,.!?;:])",
                    re.IGNORECASE,
                )
            else:
                pat = re.compile(rf"@{re.escape(name)}\b", re.IGNORECASE)
            if pat.search(lower):
                found.append(p)
                lower = pat.sub(" ", lower)
        return found

    files_root = Path.home() / ".opengateway" / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    def _guess_content_type(filename: str, declared: Optional[str]) -> str:
        import mimetypes

        if declared and declared != "application/octet-stream":
            return declared
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or declared or "application/octet-stream"

    @app.post("/v1/rooms/{room_id}/files")
    async def upload_room_file(
        request: Request,
        room_id: str,
        shared_by: str = Form(...),
        file: UploadFile = File(...),
    ) -> Artifact:
        """Upload a chat/file attachment (humans + agents).

        Small files may include inline content for agents; larger files use disk
        (and optional R2 when OPENGATEWAY_FILES_URL is configured).
        """
        from opengateway.file_store import (
            FILE_MAX_BYTES,
            artifact_meta,
            put_blob,
        )

        await _require_room(request, room_id)
        if not await st.get_participant(shared_by):
            raise HTTPException(status_code=400, detail="Unknown shared_by participant")
        raw = await file.read()
        if len(raw) > FILE_MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"File too large (max {FILE_MAX_BYTES // (1024 * 1024)}MB)",
            )
        safe_name = safe_upload_filename(file.filename)
        art_id = str(uuid.uuid4())
        content_type = _guess_content_type(safe_name, file.content_type)
        try:
            blob = put_blob(
                room_id=room_id,
                file_id=art_id,
                filename=safe_name,
                raw=raw,
                content_type=content_type,
                files_root=files_root,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        # Defense in depth: only allow metadata paths under files_root
        meta = artifact_meta(blob, content_type)
        if meta.get("path"):
            try:
                p = Path(str(meta["path"])).resolve()
                root = files_root.resolve()
                if not (str(p).startswith(str(root) + "/") or p.parent == root):
                    meta.pop("path", None)
            except Exception:
                meta.pop("path", None)

        artifact = Artifact(
            id=art_id,
            room_id=room_id,
            name=safe_name,
            content_type=content_type,
            content=blob.inline_content,
            content_url=f"/v1/rooms/{room_id}/files/{art_id}/download",
            content_encoding=blob.content_encoding if blob.inline_content else "plain",
            shared_by=shared_by,
            description=f"Chat attachment ({blob.bytes} bytes, storage={blob.storage})",
            metadata=meta,
        )
        saved = await st.share_artifact(artifact)
        await st.audit(
            "file.upload",
            actor=shared_by,
            room_id=room_id,
            resource_type="file",
            resource_id=art_id,
            detail={
                "bytes": blob.bytes,
                "storage": blob.storage,
                "durable": blob.durable,
                "name": safe_name,
            },
        )
        # Never echo multi-MB base64 back to the browser
        if saved.content and len(saved.content) > 80_000:
            saved = saved.model_copy(
                update={"content": None, "content_encoding": "plain"}
            )
        return saved

    @app.get("/v1/rooms/{room_id}/files/{file_id}")
    async def file_metadata(
        request: Request, room_id: str, file_id: str
    ) -> dict[str, Any]:
        """Metadata only (agent-friendly; no body)."""
        await _require_room(request, room_id)
        arts = await st.list_artifacts(room_id)
        art = next((a for a in arts if a.id == file_id), None)
        if not art:
            raise HTTPException(status_code=404, detail="File not found")
        meta = art.metadata or {}
        return {
            "id": art.id,
            "room_id": room_id,
            "name": art.name,
            "content_type": art.content_type,
            "content_url": art.content_url
            or f"/v1/rooms/{room_id}/files/{file_id}/download",
            "bytes": meta.get("bytes"),
            "sha256": meta.get("sha256"),
            "storage": meta.get("storage") or ("inline" if art.content else "unknown"),
            "durable": meta.get("durable", bool(art.content)),
            "has_inline_content": bool(art.content),
            "category": meta.get("category"),
            "shared_by": art.shared_by,
            "created_at": art.created_at.isoformat()
            if hasattr(art.created_at, "isoformat")
            else art.created_at,
        }

    @app.get("/v1/rooms/{room_id}/files/{file_id}/download")
    async def download_room_file(
        request: Request,
        room_id: str,
        file_id: str,
        format: str = Query("binary", description="binary | json (small/inline only)"),
    ) -> Any:
        from fastapi.responses import Response

        from opengateway.file_store import FILE_INLINE_BINARY, get_blob_bytes

        await _require_room(request, room_id)
        arts = await st.list_artifacts(room_id)
        art = next((a for a in arts if a.id == file_id), None)
        if not art:
            raise HTTPException(status_code=404, detail="File not found")
        meta = art.metadata or {}
        # Prefer safe path resolution under files_root (blocks LFI via metadata.path)
        raw: Optional[bytes] = None
        resolved = resolve_room_file_path(
            files_root,
            room_id,
            file_id,
            metadata_path=meta.get("path"),
        )
        if resolved:
            raw = resolved.read_bytes()
        if raw is None:
            raw = get_blob_bytes(
                r2_key=meta.get("r2_key"),
                disk_path=None,  # never trust arbitrary path; only allowlist above
                inline_content=art.content,
                content_encoding=art.content_encoding or "plain",
            )
        if raw is None:
            raise HTTPException(status_code=404, detail="File missing on disk")

        if format == "json":
            if len(raw) > FILE_INLINE_BINARY:
                return {
                    "id": art.id,
                    "name": art.name,
                    "content_type": art.content_type,
                    "bytes": len(raw),
                    "sha256": meta.get("sha256"),
                    "storage": meta.get("storage"),
                    "content_url": f"/v1/rooms/{room_id}/files/{file_id}/download",
                    "has_inline_content": False,
                    "error": "file_too_large_for_json",
                    "hint": (
                        "Use GET .../download (binary) with Authorization: Bearer. "
                        "Do not base64 large files into the agent context."
                    ),
                }
            return {
                "id": art.id,
                "name": art.name,
                "content_type": art.content_type,
                "content_encoding": "base64",
                "content": base64.b64encode(raw).decode("ascii"),
                "bytes": len(raw),
                "sha256": meta.get("sha256"),
                "storage": meta.get("storage"),
                "content_url": f"/v1/rooms/{room_id}/files/{file_id}/download",
                "has_inline_content": True,
            }
        return Response(
            content=raw,
            media_type=art.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_upload_filename(art.name)}"',
                "X-OG-Storage": str(meta.get("storage") or ""),
                "X-OG-Bytes": str(len(raw)),
            },
        )

    def _file_id_from_url(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        m = re.search(r"/files/([^/?#]+)/download", url)
        return m.group(1) if m else None

    def _strip_auth_query(url: Optional[str]) -> Optional[str]:
        """Never persist browser Bearer tokens inside message content_url."""
        if not url:
            return url
        try:
            from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

            p = urlparse(url)
            q = [
                (k, v)
                for k, v in parse_qsl(p.query, keep_blank_values=True)
                if k.lower() not in {"token", "access_token"}
            ]
            return urlunparse(p._replace(query=urlencode(q)))
        except Exception:
            return re.sub(r"([?&])(token|access_token)=[^&]*", r"\1", url).rstrip("?&")

    async def _enrich_message_parts(
        room_id: str, parts: list[MessagePart]
    ) -> list[MessagePart]:
        """Attachment parts: strip tokens, relative URLs, inline small blobs."""
        arts = {a.id: a for a in await st.list_artifacts(room_id)}
        out: list[MessagePart] = []
        for p in parts:
            data = p.model_dump()
            data["content_url"] = _strip_auth_query(data.get("content_url"))
            url = data.get("content_url") or ""
            if url.startswith("http://") or url.startswith("https://"):
                m = re.search(r"(/v1/rooms/[^?#]+)", url)
                if m:
                    data["content_url"] = m.group(1)
                    url = data["content_url"]
            fid = _file_id_from_url(url)
            art = arts.get(fid) if fid else None
            if art:
                if not data.get("name"):
                    data["name"] = art.name
                if not data.get("content_type") or data["content_type"] == "application/octet-stream":
                    data["content_type"] = art.content_type or data.get("content_type")
                art_content = art.content or ""
                if (
                    not data.get("content")
                    and art_content
                    and len(art_content) <= 80_000
                ):
                    data["content"] = art.content
                    data["content_encoding"] = getattr(
                        art, "content_encoding", None
                    ) or "base64"
                if data.get("content") and len(str(data["content"])) > 80_000:
                    data["content"] = None
                    data["content_encoding"] = "plain"
                if not data.get("content_url"):
                    data["content_url"] = (
                        art.content_url
                        or f"/v1/rooms/{room_id}/files/{art.id}/download"
                    )
            out.append(MessagePart.model_validate(data))
        return out

    async def _messages_json(
        room_id: str, items: list[RoomMessage]
    ) -> list[dict[str, Any]]:
        """Serialize messages and re-hydrate attachment parts for agents."""
        out: list[dict[str, Any]] = []
        arts = {a.id: a for a in await st.list_artifacts(room_id)}
        for m in items:
            dump = m.model_dump(mode="json")
            try:
                parts = m.message.parts if m.message else []
                if parts:
                    enriched = await _enrich_message_parts(room_id, list(parts))
                    dump["message"]["parts"] = [
                        p.model_dump(mode="json") for p in enriched
                    ]
                    att = []
                    for p in enriched:
                        if not (
                            p.name
                            or p.content_url
                            or (
                                p.content_type
                                and not p.content_type.startswith("text/")
                            )
                        ):
                            continue
                        fid = _file_id_from_url(p.content_url)
                        art = arts.get(fid) if fid else None
                        am = (art.metadata if art else None) or {}
                        att.append(
                            {
                                "name": p.name or (art.name if art else None),
                                "content_type": p.content_type
                                or (art.content_type if art else None),
                                "content_url": p.content_url,
                                "has_inline_content": bool(p.content)
                                or bool(art and art.content),
                                "file_id": fid,
                                "bytes": am.get("bytes"),
                                "sha256": am.get("sha256"),
                                "storage": am.get("storage"),
                                "durable": am.get("durable"),
                            }
                        )
                    dump["attachments"] = att
            except Exception:
                pass
            out.append(dump)
        return out

    @app.post("/v1/rooms/{room_id}/messages")
    async def post_message(
        request: Request, room_id: str, body: PostMessageRequest
    ) -> Any:
        await _require_room(request, room_id)
        participant = await st.get_participant(body.from_participant_id)
        if not participant:
            raise HTTPException(status_code=400, detail="Unknown from_participant_id — join the room first")
        if participant.room_id and participant.room_id != room_id:
            raise HTTPException(
                status_code=400,
                detail="from_participant_id is not seated in this room",
            )
        if body.parts:
            parts = await _enrich_message_parts(room_id, list(body.parts))
            message = Message(
                role=body.role or f"agent/{participant.name}",
                parts=parts,
            )
        else:
            message = text_message(body.role or f"agent/{participant.name}", body.content, body.content_type)
        # Private DM only when caller explicitly sets to_participant_id.
        # @mentions stay visible in the room (Slack-style) and still trigger nudges below.
        to_id = body.to_participant_id
        all_peers = [
            p
            for p in await st.list_participants(room_id)
            if p.id != body.from_participant_id
        ]
        mentioned = _mentioned_peers(body.content or "", all_peers)

        import os as _os

        from opengateway.nudge_policy import (
            is_listen_only,
            message_kind_from_meta,
            nudge_target_eligible,
            should_create_nudge_task,
            should_skip_nudge,
        )

        meta = dict(body.metadata or {})
        if "kind" not in meta:
            meta["kind"] = message_kind_from_meta(meta, to_participant_id=to_id)

        msg = RoomMessage(
            room_id=room_id,
            from_participant_id=body.from_participant_id,
            from_name=participant.name,
            to_participant_id=to_id,  # None = public room thread
            message=message,
            metadata=meta,
        )
        saved = await st.post_message(msg)

        nudged: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        skipped_reason = ""
        # @all / everyone → nudge all agents. Single @name → nudge that agent.
        # Explicit nudge_all still supported for API clients.
        # Private DMs (explicit to_id) do not auto-nudge everyone.
        want_nudge_all = bool(body.nudge_all) or (
            not to_id and _is_all_call(body.content or "")
        )
        want_nudge_mentioned = (
            bool(mentioned) and not want_nudge_all and not to_id
        )
        skip, skip_why = should_skip_nudge(
            content=body.content or "",
            metadata=meta,
            to_participant_id=to_id,
        )
        if skip and (want_nudge_all or want_nudge_mentioned):
            skipped_reason = skip_why
            want_nudge_all = False
            want_nudge_mentioned = False

        include_joined = (
            str(_os.environ.get("OPENGATEWAY_NUDGE_INCLUDE_JOINED") or "")
            .lower()
            in {"1", "true", "yes", "on"}
            or bool(meta.get("nudge_include_joined"))
        )

        if want_nudge_all or want_nudge_mentioned:
            candidates = all_peers if want_nudge_all else list(mentioned)
            seen_keys: set[str] = set()
            unique_peers: list[Participant] = []
            for p in candidates:
                kid = str((p.metadata or {}).get("api_key_id") or p.id)
                if kid in seen_keys:
                    continue
                seen_keys.add(kid)
                unique_peers.append(p)

            create_tasks = should_create_nudge_task(
                want_nudge_all=want_nudge_all,
                content=body.content or "",
                metadata=meta,
            )
            for peer in unique_peers:
                ok, why = nudge_target_eligible(
                    peer, include_joined=include_joined
                )
                if not ok:
                    skipped.append(
                        {
                            "participant_id": peer.id,
                            "name": peer.name,
                            "reason": why,
                            "presence": getattr(peer, "presence", None),
                        }
                    )
                    continue
                allowed, rate_why = st.nudge_limiter.allow(
                    room_id, body.from_participant_id, peer.id
                )
                if not allowed:
                    skipped.append(
                        {
                            "participant_id": peer.id,
                            "name": peer.name,
                            "reason": rate_why,
                            "presence": getattr(peer, "presence", None),
                        }
                    )
                    continue
                listen_only = is_listen_only(peer)
                dm = RoomMessage(
                    room_id=room_id,
                    from_participant_id=body.from_participant_id,
                    from_name=participant.name,
                    to_participant_id=peer.id,
                    message=text_message(
                        f"agent/{participant.name}",
                        f"@nudge → {peer.name}: {body.content}",
                    ),
                    metadata={
                        "nudge": True,
                        "kind": "nudge",
                        "broadcast_id": saved.id,
                        "listen_only": listen_only,
                    },
                )
                await st.post_message(dm)
                st.nudge_limiter.record_edge(
                    room_id,
                    body.from_participant_id,
                    peer.id,
                    from_name=participant.name,
                    to_name=peer.name,
                )
                task_id = None
                if create_tasks and not listen_only:
                    task = Task(
                        room_id=room_id,
                        title=f"Respond to {participant.name}",
                        description=(
                            f"{participant.name} addressed the room:\n\n{body.content}\n\n"
                            f"Please reply in the room (post_message). This task is for {peer.name}."
                        ),
                        created_by=body.from_participant_id,
                        claimed_by=peer.id,
                        status=TaskStatus.CLAIMED,
                        metadata={
                            "nudge": True,
                            "assignee_id": peer.id,
                            "assignee_name": peer.name,
                            "source_message_id": saved.id,
                        },
                    )
                    await st.create_task(task)
                    task_id = task.id
                nudged.append(
                    {
                        "participant_id": peer.id,
                        "name": peer.name,
                        "task_id": task_id,
                        "presence": getattr(peer, "presence", None),
                        "listen_only": listen_only,
                    }
                )
            n_listen = len(nudged)
            n_off = sum(1 for s in skipped if s.get("reason") == "offline")
            n_joined = sum(
                1 for s in skipped if s.get("reason") == "joined_not_listening"
            )
            n_other = len(skipped) - n_off - n_joined
            toast = (
                f"{n_listen} listening"
                + (f", {n_joined} joined not listening" if n_joined else "")
                + (f", {n_off} offline" if n_off else "")
                + " — only listening agents get DMs"
            )
            note = RoomMessage(
                room_id=room_id,
                from_participant_id=body.from_participant_id,
                from_name="system",
                message=text_message(
                    "agent/system",
                    "Nudge: "
                    + toast
                    + (
                        " → "
                        + ", ".join(n["name"] for n in nudged)
                        if nudged
                        else " → (none)"
                    ),
                ),
                metadata={
                    "system": True,
                    "nudge_summary": True,
                    "kind": "system",
                    "nudge_counts": {
                        "nudged": n_listen,
                        "skipped_offline": n_off,
                        "skipped_joined_not_listening": n_joined,
                        "skipped_other": n_other,
                    },
                },
            )
            note.from_name = "system"
            await st.post_message(note)

        if want_nudge_all or want_nudge_mentioned or skipped_reason:
            storm = st.nudge_limiter.room_stats(room_id)
            delivered_n = len(nudged)
            return {
                "message": {
                    **saved.model_dump(mode="json"),
                    "kind": (saved.metadata or {}).get("kind") or "chat",
                },
                "nudged": nudged,
                "skipped": skipped,
                "nudge_count": delivered_n,
                "nudge_skipped": skipped_reason or None,
                "nudge_summary": {
                    "nudged": delivered_n,
                    "skipped_offline": sum(
                        1 for s in skipped if s.get("reason") == "offline"
                    ),
                    "skipped_joined_not_listening": sum(
                        1
                        for s in skipped
                        if s.get("reason") == "joined_not_listening"
                    ),
                },
                "nudge_stats": storm,
            }
        out = saved.model_dump(mode="json")
        out["kind"] = (saved.metadata or {}).get("kind") or "chat"
        return out

    @app.get("/v1/rooms/{room_id}/messages")
    async def list_messages(
        request: Request,
        room_id: str,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        await _require_room(request, room_id)
        # Public messages by default; DMs only when for_participant is set
        items = await st.list_messages(
            room_id,
            since=since,
            for_participant=for_participant,
            limit=limit,
            include_dms=False,
        )
        return {"messages": await _messages_json(room_id, items)}

    @app.get("/v1/rooms/{room_id}/messages/wait")
    async def wait_messages(
        request: Request,
        room_id: str,
        since: Optional[str] = None,
        for_participant: Optional[str] = None,
        timeout: float = Query(30.0, ge=0.0, le=120.0),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        """Long-poll for new messages (IM-style). Blocks until a message arrives or timeout.

        Agents should loop: wait → process → wait again, using the last message id as `since`.
        Attachment parts include content_url + inline base64 when small.
        """
        await _require_room(request, room_id)
        if for_participant:
            # long-poll = radio on (UI "listening" badge)
            await st.touch_participant(for_participant, listening=True)
        items = await st.wait_for_messages(
            room_id,
            since=since,
            for_participant=for_participant,
            timeout=timeout,
            limit=limit,
        )
        if for_participant:
            await st.touch_participant(for_participant, listening=True)
        # next_since = last message id for the client's wait cursor (top-level, not nested)
        last_id = items[-1].id if items else since
        return {
            "messages": await _messages_json(room_id, items),
            "timed_out": len(items) == 0,
            "since": since,
            "last_id": last_id,
            "next_since": last_id,
            "count": len(items),
        }

    # ── Tasks ──────────────────────────────────────────────────────────────

    @app.post("/v1/rooms/{room_id}/tasks", response_model=Task)
    async def create_task(
        request: Request, room_id: str, body: CreateTaskRequest
    ) -> Task:
        await _require_room(request, room_id)
        task = Task(
            room_id=room_id,
            title=body.title,
            description=body.description,
            created_by=body.created_by,
            depends_on=body.depends_on,
            metadata=body.metadata,
        )
        return await st.create_task(task)

    @app.get("/v1/rooms/{room_id}/nudge-stats")
    async def room_nudge_stats(request: Request, room_id: str) -> dict[str, Any]:
        """Live Ops storm banner + who-nudged-whom edges."""
        await _require_room(request, room_id)
        stats = st.nudge_limiter.room_stats(room_id)
        return {"room_id": room_id, **stats}

    @app.get("/v1/rooms/{room_id}/tasks")
    async def list_tasks(
        request: Request,
        room_id: str,
        status: Optional[TaskStatus] = None,
        include_nudge: bool = Query(
            False,
            description="Include auto-nudge tasks (default: hide)",
        ),
    ) -> dict[str, Any]:
        await _require_room(request, room_id)
        items = await st.list_tasks(room_id, status=status)
        if not include_nudge:
            items = [t for t in items if not (t.metadata or {}).get("nudge")]
        return {
            "tasks": [t.model_dump(mode="json") for t in items],
            "include_nudge": include_nudge,
        }

    @app.post("/v1/rooms/{room_id}/tasks/cancel-nudges")
    async def cancel_nudge_tasks(request: Request, room_id: str) -> dict[str, Any]:
        """Bulk-cancel open/claimed auto-nudge tasks."""
        await _require_room(request, room_id)
        cancelled = 0
        for t in await st.list_tasks(room_id):
            if not (t.metadata or {}).get("nudge"):
                continue
            if t.status not in {TaskStatus.OPEN, TaskStatus.CLAIMED}:
                continue
            await st.update_task(room_id, t.id, status=TaskStatus.CANCELLED)
            cancelled += 1
        return {"cancelled": cancelled, "room_id": room_id}

    @app.patch("/v1/rooms/{room_id}/tasks/{task_id}", response_model=Task)
    async def update_task(
        request: Request, room_id: str, task_id: str, body: UpdateTaskRequest
    ) -> Task:
        await _require_room(request, room_id)
        fields: dict[str, Any] = {}
        if body.status is not None:
            fields["status"] = body.status
        if body.claimed_by is not None:
            fields["claimed_by"] = body.claimed_by
            if body.status is None:
                fields["status"] = TaskStatus.CLAIMED
        if body.result is not None:
            fields["result"] = body.result
        if body.metadata is not None:
            fields["metadata"] = body.metadata
        task = await st.update_task(room_id, task_id, **fields)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task

    # ── Artifacts ──────────────────────────────────────────────────────────

    @app.post("/v1/rooms/{room_id}/artifacts", response_model=Artifact)
    async def share_artifact(
        request: Request, room_id: str, body: ShareArtifactRequest
    ) -> Artifact:
        await _require_room(request, room_id)
        if not body.content and not body.content_url:
            raise HTTPException(status_code=400, detail="Provide content or content_url")
        # Never allow clients to set filesystem paths for later download LFI
        meta = dict(body.metadata or {})
        meta.pop("path", None)
        artifact = Artifact(
            room_id=room_id,
            name=body.name,
            content_type=body.content_type,
            content=body.content,
            content_url=body.content_url,
            content_encoding=body.content_encoding,
            shared_by=body.shared_by,
            description=body.description,
            metadata=meta,
        )
        return await st.share_artifact(artifact)

    @app.get("/v1/rooms/{room_id}/artifacts")
    async def list_artifacts(request: Request, room_id: str) -> dict[str, Any]:
        await _require_room(request, room_id)
        return {
            "artifacts": [
                a.model_dump(mode="json") for a in await st.list_artifacts(room_id)
            ]
        }

    # ── Events (SSE + poll) ────────────────────────────────────────────────

    @app.get("/v1/events")
    async def poll_events(
        room_id: Optional[str] = None,
        after_id: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        events = st.recent_events(room_id=room_id, after_id=after_id, limit=limit)
        return {"events": [e.model_dump(mode="json") for e in events]}

    @app.get("/v1/events/stream")
    async def global_events_sse(request: Request) -> EventSourceResponse:
        """SSE stream of all gateway events (all rooms). For CLI monitor."""

        async def gen():
            q = st.subscribe()
            try:
                for e in st.recent_events(limit=30):
                    yield {"event": e.type, "id": e.id, "data": json.dumps(e.model_dump(mode="json"))}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event: GatewayEvent = await asyncio.wait_for(q.get(), timeout=25.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": event.type,
                        "id": event.id,
                        "data": json.dumps(event.model_dump(mode="json")),
                    }
            finally:
                st.unsubscribe(q)

        return EventSourceResponse(gen())

    @app.get("/v1/rooms/{room_id}/events")
    async def room_events_sse(room_id: str, request: Request) -> EventSourceResponse:
        await _require_room(request, room_id)

        async def gen():
            q = st.subscribe()
            try:
                # Replay recent — strip private DM payloads for non-targeted events
                for e in st.recent_events(room_id=room_id, limit=20):
                    yield {"event": e.type, "id": e.id, "data": json.dumps(e.model_dump(mode="json"))}
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event: GatewayEvent = await asyncio.wait_for(q.get(), timeout=25.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    if event.room_id is None or event.room_id == room_id:
                        yield {
                            "event": event.type,
                            "id": event.id,
                            "data": json.dumps(event.model_dump(mode="json")),
                        }
            finally:
                st.unsubscribe(q)

        return EventSourceResponse(gen())

    # ── WebSocket IM channel ───────────────────────────────────────────────

    @app.websocket("/v1/rooms/{room_id}/ws")
    async def room_ws(websocket: WebSocket, room_id: str) -> None:
        """Realtime room socket (requires same auth as HTTP when require_auth).

        Query: token / access_token (when auth required), participant_id.
        Client → server JSON:
          {"type":"hello","participant_id":"..."}
          {"type":"message","content":"...","to_participant_id":null}
          {"type":"ping"}
        """
        # Authenticate before accept when auth is required
        ws_auth: Optional[dict[str, Any]] = None
        if cfg.require_auth:
            token = token_from_websocket(websocket)
            ws_auth = await resolve_auth(
                token,
                config=cfg,
                store=st,
                method="GET",
                path=f"/v1/rooms/{room_id}/ws",
            )
            # Tailscale identity only on localhost binds (parity with HTTP middleware)
            if (not ws_auth or ws_auth.get("forbidden")) and (
                cfg.trust_tailscale_identity and cfg.is_localhost_bind
            ):
                login = websocket.headers.get("tailscale-user-login")
                if login:
                    ws_auth = {
                        "auth_kind": "tailscale",
                        "auth_scopes": ["admin"],
                        "tenant_id": None,
                    }
            if not ws_auth or ws_auth.get("forbidden"):
                await websocket.close(code=4401)
                return

        room = await st.get_room(room_id)
        if not room or (ws_auth and not room_visible_to(room, ws_auth)):
            await websocket.close(code=4404)
            return

        await websocket.accept()

        participant_id = websocket.query_params.get("participant_id")
        q = st.subscribe(maxsize=512)

        def _dm_allowed(msg_payload: dict[str, Any]) -> bool:
            """Fan-out private DMs only to the two participants (or admins)."""
            to_id = msg_payload.get("to_participant_id")
            if not to_id:
                return True
            if is_admin_principal(ws_auth):
                return True
            from_id = msg_payload.get("from_participant_id")
            return participant_id in {to_id, from_id}

        async def pump_events() -> None:
            try:
                while True:
                    event = await q.get()
                    if event.room_id is not None and event.room_id != room_id:
                        continue
                    if event.type == "message" and "message" in event.payload:
                        msg = event.payload["message"]
                        if isinstance(msg, dict) and not _dm_allowed(msg):
                            continue
                        payload = {"type": "message", "message": msg}
                    else:
                        payload = {
                            "type": "event",
                            "event": event.model_dump(mode="json"),
                        }
                    await websocket.send_json(payload)
            except Exception:
                return

        pump_task = asyncio.create_task(pump_events())
        try:
            # Optional immediate hello via query param
            if participant_id:
                p = await st.get_participant(participant_id)
                if p and (not p.room_id or p.room_id == room_id):
                    await st.touch_participant(participant_id, listening=True)
                    await websocket.send_json(
                        {"type": "hello_ok", "participant": p.model_dump(mode="json")}
                    )
                else:
                    await websocket.send_json({"type": "error", "detail": "unknown_participant"})

            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "detail": "invalid_json"})
                    continue

                msg_type = data.get("type")
                if msg_type == "ping":
                    if participant_id:
                        await st.touch_participant(participant_id, listening=True)
                    await websocket.send_json({"type": "pong"})
                    continue

                if msg_type == "hello":
                    participant_id = data.get("participant_id") or participant_id
                    p = await st.get_participant(participant_id) if participant_id else None
                    if not p or (p.room_id and p.room_id != room_id):
                        await websocket.send_json({"type": "error", "detail": "unknown_participant"})
                        continue
                    await st.touch_participant(participant_id, listening=True)
                    await websocket.send_json(
                        {"type": "hello_ok", "participant": p.model_dump(mode="json")}
                    )
                    continue

                if msg_type == "message":
                    if not participant_id:
                        await websocket.send_json(
                            {"type": "error", "detail": "send hello with participant_id first"}
                        )
                        continue
                    p = await st.get_participant(participant_id)
                    if not p or (p.room_id and p.room_id != room_id):
                        await websocket.send_json({"type": "error", "detail": "unknown_participant"})
                        continue
                    content = (data.get("content") or "").strip()
                    if not content:
                        await websocket.send_json({"type": "error", "detail": "empty_content"})
                        continue
                    room_msg = RoomMessage(
                        room_id=room_id,
                        from_participant_id=participant_id,
                        from_name=p.name,
                        to_participant_id=data.get("to_participant_id"),
                        message=text_message(f"agent/{p.name}", content),
                    )
                    saved = await st.post_message(room_msg)
                    await websocket.send_json(
                        {"type": "message_ack", "message": saved.model_dump(mode="json")}
                    )
                    continue

                await websocket.send_json({"type": "error", "detail": f"unknown_type:{msg_type}"})
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            st.unsubscribe(q)

    # ── Bookmarks / forks / DMs ────────────────────────────────────────────

    @app.get("/v1/rooms/{room_id}/bookmarks")
    async def list_bookmarks(request: Request, room_id: str) -> dict[str, Any]:
        await _require_room(request, room_id)
        return {
            "bookmarks": [b.model_dump(mode="json") for b in await st.list_bookmarks(room_id)]
        }

    @app.post("/v1/rooms/{room_id}/bookmarks")
    async def create_bookmark(
        request: Request, room_id: str, body: CreateBookmarkRequest
    ) -> Bookmark:
        await _require_room(request, room_id)
        msg = await st.get_message(room_id, body.message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        excerpt = body.excerpt
        if not excerpt:
            excerpt = (msg.message.parts[0].content or "")[:180] if msg.message.parts else ""
        title = body.title.strip() or f"Bookmark · {msg.from_name}"
        bookmark = Bookmark(
            room_id=room_id,
            message_id=body.message_id,
            title=title,
            excerpt=excerpt,
            created_by=body.created_by,
            metadata=body.metadata,
        )
        return await st.add_bookmark(bookmark)

    @app.delete("/v1/rooms/{room_id}/bookmarks/{bookmark_id}")
    async def remove_bookmark(
        request: Request, room_id: str, bookmark_id: str
    ) -> dict[str, str]:
        await _require_room(request, room_id)
        ok = await st.delete_bookmark(room_id, bookmark_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        return {"status": "deleted"}

    @app.get("/v1/rooms/{room_id}/forks")
    async def list_forks(request: Request, room_id: str) -> dict[str, Any]:
        await _require_room(request, room_id)
        return {"forks": [f.model_dump(mode="json") for f in await st.list_forks(room_id)]}

    @app.post("/v1/rooms/{room_id}/forks")
    async def create_fork(
        request: Request, room_id: str, body: CreateForkRequest
    ) -> Fork:
        """Fork a message into a **new room** (branch chat).

        Copies optional prior context + the root message into the new room, joins
        the creator, and records Fork.forked_room_id for UI/agents to open.
        """
        parent = await _require_room(request, room_id)
        msg = await st.get_message(room_id, body.root_message_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")

        text = ""
        if msg.message and msg.message.parts:
            text = (msg.message.parts[0].content or "")[:80]
        title = body.title.strip() or f"Fork · {msg.from_name}: {text}".strip()
        if len(title) > 120:
            title = title[:117] + "…"

        branch = Room(
            name=title[:80] or f"fork-{msg.id[:8]}",
            goal=(
                body.note.strip()
                or f'Fork of “{parent.name}” from message by {msg.from_name}'
            ),
            created_by=body.created_by or "system",
            tenant_id=parent.tenant_id,
            status=RoomStatus.OPEN,
            metadata={
                "is_fork": True,
                "parent_room_id": room_id,
                "parent_room_name": parent.name,
                "parent_message_id": body.root_message_id,
                "root_from": msg.from_name,
            },
        )
        branch = await st.create_room(branch)

        history = await st.list_messages(room_id, limit=500, include_dms=False)
        try:
            idx = next(i for i, m in enumerate(history) if m.id == msg.id)
        except StopIteration:
            idx = len(history) - 1
        start = max(0, idx - int(body.context_messages or 0))
        context_slice = history[start : idx + 1]

        seed_lines = [
            f'🔀 Forked from room “{parent.name}” ({room_id}).',
            f"Root message by {msg.from_name}.",
            "This is a new chat branch — continue the discussion here.",
        ]
        if body.note.strip():
            seed_lines.append(f"Note: {body.note.strip()}")
        await st.post_message(
            RoomMessage(
                room_id=branch.id,
                from_participant_id="system",
                from_name="system",
                message=text_message("agent/system", "\n".join(seed_lines)),
                metadata={
                    "system": True,
                    "fork": True,
                    "parent_room_id": room_id,
                    "parent_message_id": body.root_message_id,
                },
            )
        )

        for src in context_slice:
            is_root = src.id == msg.id
            copied = RoomMessage(
                room_id=branch.id,
                from_participant_id=src.from_participant_id,
                from_name=src.from_name,
                to_participant_id=None,
                message=src.message.model_copy(deep=True)
                if src.message
                else text_message(f"agent/{src.from_name}", ""),
                metadata={
                    **(src.metadata or {}),
                    "forked_from_message_id": src.id,
                    "fork_root": is_root,
                    "fork_context": not is_root,
                },
            )
            await st.post_message(copied)

        if body.join_creator and body.created_by:
            creator = await st.get_participant(body.created_by)
            if creator:
                await st.join_room(
                    branch.id,
                    Participant(
                        id=new_id(),
                        name=body.created_by_name or creator.name,
                        harness=creator.harness,
                        role=creator.role or "admin",
                        capabilities=list(
                            creator.capabilities or ["monitor", "chat"]
                        ),
                        metadata={"forked_from_participant": creator.id},
                    ),
                )
            else:
                await st.join_room(
                    branch.id,
                    Participant(
                        name=body.created_by_name or "human",
                        harness=Harness.HUMAN,
                        role="admin",
                        capabilities=["monitor", "chat"],
                    ),
                )

        fork = Fork(
            room_id=room_id,
            root_message_id=body.root_message_id,
            forked_room_id=branch.id,
            title=title,
            created_by=body.created_by,
            created_by_name=body.created_by_name,
            note=body.note,
            metadata={
                **body.metadata,
                "root_from": msg.from_name,
                "root_excerpt": text,
                "forked_room_name": branch.name,
                "context_count": len(context_slice),
            },
        )
        fork = await st.add_fork(fork)

        branch.metadata = {**(branch.metadata or {}), "fork_id": fork.id}
        await st.update_room(branch, action="updated")

        await st.post_message(
            RoomMessage(
                room_id=room_id,
                from_participant_id="system",
                from_name="system",
                message=text_message(
                    "agent/system",
                    f'Branch created: “{branch.name}” → open room {branch.id}'
                    + (
                        f" (by {body.created_by_name})"
                        if body.created_by_name
                        else ""
                    ),
                ),
                metadata={
                    "system": True,
                    "fork": True,
                    "fork_id": fork.id,
                    "forked_room_id": branch.id,
                },
            )
        )
        await st.emit(
            GatewayEvent(
                type="fork",
                room_id=branch.id,
                payload={
                    "action": "branch_opened",
                    "fork": fork.model_dump(mode="json"),
                    "room": branch.model_dump(mode="json"),
                },
            )
        )
        return fork

    @app.get("/v1/rooms/{room_id}/dms")
    async def list_dms(
        request: Request,
        room_id: str,
        participant_id: str = Query(...),
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        """List DM threads involving this participant (directed messages only)."""
        await _require_room(request, room_id)
        # Must use for_participant so private DMs are included for this seat only
        all_msgs = await st.list_messages(
            room_id, for_participant=participant_id, limit=500
        )
        dms = [
            m
            for m in all_msgs
            if m.to_participant_id
            and (
                m.to_participant_id == participant_id
                or m.from_participant_id == participant_id
            )
        ]
        # Group by peer
        peers: dict[str, list[dict[str, Any]]] = {}
        for m in dms[-limit:]:
            peer = (
                m.to_participant_id
                if m.from_participant_id == participant_id
                else m.from_participant_id
            )
            peers.setdefault(peer or "unknown", []).append(m.model_dump(mode="json"))
        threads = []
        for peer_id, msgs in peers.items():
            p = await st.get_participant(peer_id)
            threads.append(
                {
                    "peer_id": peer_id,
                    "peer_name": p.name if p else peer_id[:8],
                    "peer_harness": p.harness.value if p else "other",
                    "peer_status": p.status.value if p else "offline",
                    "peer_role": p.role if p else "",
                    "peer_last_seen": p.last_seen_at.isoformat() if p and p.last_seen_at else None,
                    "messages": msgs,
                    "last_at": msgs[-1]["created_at"] if msgs else None,
                }
            )
        # Online first, then most recent activity
        def _dm_sort(t: dict[str, Any]) -> tuple:
            online = 0 if (t.get("peer_status") or "") == "online" else 1
            return (online, -(1 if t.get("last_at") else 0), t.get("last_at") or "", t.get("peer_name") or "")

        threads.sort(key=_dm_sort)
        return {"threads": threads}

    # ── Snapshot helper for agents ─────────────────────────────────────────

    @app.get("/v1/rooms/{room_id}/snapshot")
    async def room_snapshot(
        request: Request,
        room_id: str,
        for_participant: Optional[str] = None,
    ) -> dict[str, Any]:
        await _require_room(request, room_id)
        people = _sort_participants(await st.list_participants(room_id))
        # Public room thread only unless for_participant (then + their DMs)
        msgs = await st.list_messages(
            room_id,
            limit=200,
            for_participant=for_participant,
            include_dms=False,
        )
        from opengateway.workspace import list_workspace as _ws_list

        return {
            "room": (await st.get_room(room_id)).model_dump(mode="json"),  # type: ignore[union-attr]
            "participants": [p.model_dump(mode="json") for p in people],
            "tasks": [
                t.model_dump(mode="json")
                for t in await st.list_tasks(room_id)
                if not (t.metadata or {}).get("nudge")
            ],
            "messages": [m.model_dump(mode="json") for m in msgs],
            "artifacts": [a.model_dump(mode="json") for a in await st.list_artifacts(room_id)],
            "bookmarks": [b.model_dump(mode="json") for b in await st.list_bookmarks(room_id)],
            "forks": [f.model_dump(mode="json") for f in await st.list_forks(room_id)],
            "workspace": _ws_list(st, room_id)[:200],
        }

    # ── Tool credential vault (secrets never leave hub to agents) ─────────

    @app.get("/v1/tools/credentials")
    async def list_tool_credentials() -> dict[str, Any]:
        from opengateway.tool_vault import list_credentials

        return {"credentials": list_credentials(st)}

    @app.put("/v1/tools/credentials/{name}")
    async def put_tool_credential(name: str, request: Request) -> dict[str, Any]:
        from opengateway.tool_vault import put_credential

        _require_admin(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        value = body.get("value")
        if value is None:
            raise HTTPException(status_code=400, detail="value is required")
        try:
            public = put_credential(
                st,
                name=name,
                value=str(value),
                description=str(body.get("description") or ""),
                header_name=str(body.get("header_name") or "Authorization"),
                inject=str(body.get("inject") or "bearer"),
                allowed_hosts=body.get("allowed_hosts")
                if isinstance(body.get("allowed_hosts"), list)
                else None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        await st.audit(
            "tool_credential.put",
            actor=str(
                getattr(request.state, "auth_kind", None)
                or getattr(request.state, "user", None)
                or "admin"
            ),
            resource_type="tool_credential",
            resource_id=public.get("name"),
            detail={"name": public.get("name"), "inject": public.get("inject")},
        )
        return public

    @app.delete("/v1/tools/credentials/{name}")
    async def delete_tool_credential(name: str, request: Request) -> dict[str, Any]:
        from opengateway.tool_vault import delete_credential, validate_name

        _require_admin(request)
        try:
            validate_name(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        ok = delete_credential(st, name)
        if not ok:
            raise HTTPException(status_code=404, detail="Credential not found")
        await st.audit(
            "tool_credential.delete",
            actor=str(
                getattr(request.state, "auth_kind", None)
                or getattr(request.state, "user", None)
                or "admin"
            ),
            resource_type="tool_credential",
            resource_id=name,
        )
        return {"ok": True, "name": name}

    @app.post("/v1/tools/proxy")
    async def tool_credential_proxy(request: Request) -> dict[str, Any]:
        import base64 as b64
        import httpx

        from opengateway.api_keys import SCOPE_ADMIN
        from opengateway.tool_vault import (
            MAX_PROXY_BODY,
            MAX_PROXY_RESPONSE,
            PROXY_TIMEOUT,
            apply_query_secret,
            assert_safe_url,
            build_auth_header,
            get_secret_value,
            host_allowed,
            load_vault,
            validate_name,
        )

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        cred_name = str(body.get("credential") or body.get("name") or "").strip()
        method = str(body.get("method") or "GET").upper()
        url = str(body.get("url") or "").strip()
        if not cred_name or not url:
            raise HTTPException(
                status_code=400, detail="credential and url are required"
            )
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise HTTPException(status_code=400, detail="Unsupported method")
        try:
            validate_name(cred_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        vault = load_vault(st)
        rec = vault.get(cred_name)
        if not rec:
            raise HTTPException(status_code=404, detail="Credential not found")
        secret = get_secret_value(st, cred_name)
        if secret is None:
            raise HTTPException(status_code=404, detail="Credential not found")
        scopes = list(getattr(request.state, "auth_scopes", None) or [])
        auth_kind = getattr(request.state, "auth_kind", None)
        allow_private = bool(body.get("allow_private")) and (
            SCOPE_ADMIN in scopes or auth_kind in {"master", "session"}
        )
        try:
            _, hostname = assert_safe_url(url, allow_private=allow_private)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not host_allowed(rec, hostname):
            raise HTTPException(
                status_code=403,
                detail=f"Host {hostname!r} not in allowed_hosts for this credential",
            )
        inject = (rec.get("inject") or "bearer").lower()
        out_url = url
        headers: dict[str, str] = {}
        extra = body.get("headers")
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k and v is not None:
                    headers[str(k)] = str(v)
        if inject == "query":
            out_url = apply_query_secret(url, rec, secret)
        else:
            headers.update(build_auth_header(rec, secret))

        req_body: bytes | None = None
        if body.get("body_base64"):
            try:
                req_body = b64.b64decode(str(body["body_base64"]))
            except Exception as e:
                raise HTTPException(status_code=400, detail="invalid body_base64") from e
        elif body.get("body") is not None:
            raw_b = body["body"]
            if isinstance(raw_b, (dict, list)):
                req_body = json.dumps(raw_b).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            else:
                req_body = str(raw_b).encode("utf-8")
        if req_body is not None and len(req_body) > MAX_PROXY_BODY:
            raise HTTPException(status_code=400, detail="Request body too large")

        timeout = min(float(body.get("timeout") or PROXY_TIMEOUT), 60.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                resp = await client.request(
                    method, out_url, headers=headers, content=req_body
                )
        except httpx.RequestError as e:
            await st.audit(
                "tool_proxy.error",
                actor="agent",
                resource_type="tool_credential",
                resource_id=cred_name,
                detail={"url_host": hostname, "error": type(e).__name__},
                outcome="error",
            )
            raise HTTPException(
                status_code=502, detail=f"Upstream request failed: {type(e).__name__}"
            ) from e

        content = resp.content[:MAX_PROXY_RESPONSE]
        truncated = len(resp.content) > MAX_PROXY_RESPONSE
        text_out: str | None = None
        b64_out: str | None = None
        try:
            text_out = content.decode("utf-8")
            if any(ord(c) < 9 for c in text_out[:200]):
                raise UnicodeError("binary")
        except Exception:
            text_out = None
            b64_out = b64.b64encode(content).decode("ascii")

        safe_resp_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower()
            not in {
                "set-cookie",
                "authorization",
                "proxy-authorization",
                "transfer-encoding",
            }
        }
        await st.audit(
            "tool_proxy.call",
            actor="agent",
            resource_type="tool_credential",
            resource_id=cred_name,
            detail={
                "method": method,
                "url_host": hostname,
                "status": resp.status_code,
                "bytes": len(content),
            },
        )
        return {
            "credential": cred_name,
            "status_code": resp.status_code,
            "headers": safe_resp_headers,
            "body": text_out,
            "body_base64": b64_out,
            "bytes": len(content),
            "truncated": truncated,
        }

    # ── Room workspace (path-addressed shared FS) ─────────────────────────

    @app.get("/v1/rooms/{room_id}/workspace")
    async def workspace_list_route(
        request: Request,
        room_id: str,
        prefix: str = Query("", description="Path prefix filter"),
    ) -> dict[str, Any]:
        from opengateway.workspace import list_workspace

        await _require_room(request, room_id)
        files = list_workspace(st, room_id, prefix=prefix)
        return {"room_id": room_id, "prefix": prefix or "", "files": files}

    @app.get("/v1/rooms/{room_id}/workspace/{path:path}")
    async def workspace_get_route(
        request: Request,
        room_id: str,
        path: str,
        format: str = Query(
            "auto",
            description="auto | json | binary — auto returns JSON for text, binary otherwise",
        ),
    ) -> Any:
        from fastapi.responses import Response
        from opengateway.workspace import read_workspace_bytes

        await _require_room(request, room_id)
        try:
            result = read_workspace_bytes(st, room_id, path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if result is None:
            raise HTTPException(status_code=404, detail="File not found")
        raw, entry = result
        ct = entry.get("content_type") or "application/octet-stream"
        fmt = (format or "auto").lower()
        is_text = ct.startswith("text/") or ct in {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/x-yaml",
            "application/yaml",
        }
        if fmt == "binary" or (fmt == "auto" and not is_text):
            return Response(
                content=raw,
                media_type=ct,
                headers={
                    "X-Workspace-Path": entry["path"],
                    "X-Workspace-Sha256": entry.get("sha256") or "",
                },
            )
        text: str | None = None
        b64_content: str | None = None
        if is_text or fmt == "json":
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                b64_content = base64.b64encode(raw).decode("ascii")
        else:
            b64_content = base64.b64encode(raw).decode("ascii")
        return {
            "path": entry["path"],
            "bytes": len(raw),
            "content_type": ct,
            "sha256": entry.get("sha256"),
            "updated_at": entry.get("updated_at"),
            "updated_by": entry.get("updated_by"),
            "content": text,
            "content_base64": b64_content,
            "storage": entry.get("storage"),
        }

    @app.put("/v1/rooms/{room_id}/workspace/{path:path}")
    async def workspace_put_route(
        request: Request,
        room_id: str,
        path: str,
        updated_by: str = Query("", description="Participant id or agent name"),
        content_type: str = Query("", description="MIME type override"),
    ) -> dict[str, Any]:
        from opengateway.workspace import put_workspace_file

        await _require_room(request, room_id)
        ctype_hdr = (request.headers.get("content-type") or "").split(";")[0].strip()
        raw: bytes
        if ctype_hdr == "application/json":
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="JSON body must be object")
            if body.get("content_base64") is not None:
                try:
                    raw = base64.b64decode(str(body["content_base64"]))
                except Exception as e:
                    raise HTTPException(status_code=400, detail="invalid content_base64") from e
            elif body.get("content") is not None:
                raw = str(body["content"]).encode("utf-8")
            else:
                raise HTTPException(
                    status_code=400, detail="content or content_base64 required"
                )
            ct = (
                content_type
                or str(body.get("content_type") or "")
                or "text/plain"
            )
            who = updated_by or str(body.get("updated_by") or "")
        else:
            raw = await request.body()
            ct = content_type or ctype_hdr or "application/octet-stream"
            who = updated_by
        try:
            meta = put_workspace_file(
                st,
                room_id=room_id,
                path=path,
                raw=raw,
                content_type=ct or None,
                updated_by=who,
                files_root=files_root,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        await st.audit(
            "workspace.put",
            actor=who or "agent",
            room_id=room_id,
            resource_type="workspace",
            resource_id=meta["path"],
            detail={"bytes": meta["bytes"], "storage": meta.get("storage")},
        )
        return meta

    @app.delete("/v1/rooms/{room_id}/workspace/{path:path}")
    async def workspace_delete_route(
        request: Request, room_id: str, path: str
    ) -> dict[str, Any]:
        from opengateway.workspace import delete_workspace_file, normalize_path

        await _require_room(request, room_id)
        try:
            norm = normalize_path(path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        ok = delete_workspace_file(st, room_id, norm)
        if not ok:
            raise HTTPException(status_code=404, detail="File not found")
        await st.audit(
            "workspace.delete",
            actor="agent",
            room_id=room_id,
            resource_type="workspace",
            resource_id=norm,
        )
        return {"ok": True, "path": norm}

    return app


def build_app() -> FastAPI:
    """Uvicorn factory entrypoint — creates Store after env (OPENGATEWAY_DB) is set."""
    return create_app()


# Eager app for ASGI importers that expect `app` (uses current env at import time)
app = create_app()
