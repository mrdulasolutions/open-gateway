"""Optional Redis event bus for multi-process / multi-worker scale-out.

When ``OPENGATEWAY_REDIS_URL`` (or ``REDIS_URL``) is set and the ``redis``
package is installed, gateway events are published to a Redis channel and
each worker subscribes so SSE / wait loops see activity from sibling processes.

Pair codes can also live in Redis so any worker can redeem them.

SQLite remains the system of record for rooms/messages — run a **single
writer** or shared volume carefully; Redis is for realtime fan-out + pair
coordination, not a full multi-master database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Optional

log = logging.getLogger("opengateway.redis")

CHANNEL = os.environ.get("OPENGATEWAY_REDIS_CHANNEL", "opengateway:events")
PAIR_PREFIX = os.environ.get("OPENGATEWAY_REDIS_PAIR_PREFIX", "opengateway:pair:")


def redis_url() -> Optional[str]:
    return (
        os.environ.get("OPENGATEWAY_REDIS_URL", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
        or None
    )


class RedisBus:
    """Thin async pub/sub + key/value for pair codes."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._redis: Any = None
        self._pubsub: Any = None
        self._listener_task: Optional[asyncio.Task[None]] = None
        self._on_event: Optional[Callable[[dict[str, Any]], Any]] = None
        self._node_id = os.environ.get("OPENGATEWAY_NODE_ID") or os.urandom(4).hex()

    @property
    def node_id(self) -> str:
        return self._node_id

    async def connect(self) -> None:
        try:
            import redis.asyncio as redis  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError(
                "REDIS_URL set but redis package missing — "
                "pip/uv install 'opengateway[redis]' or 'redis>=5'"
            ) from e
        self._redis = redis.from_url(self.url, decode_responses=True)
        await self._redis.ping()
        log.info("Redis connected (%s)", self.url.split("@")[-1])

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        if self._pubsub:
            await self._pubsub.unsubscribe(CHANNEL)
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def publish_event(self, event: dict[str, Any]) -> None:
        if not self._redis:
            return
        payload = dict(event)
        payload["_node"] = self._node_id
        await self._redis.publish(CHANNEL, json.dumps(payload, default=str))

    def start_listener(self, on_event: Callable[[dict[str, Any]], Any]) -> None:
        self._on_event = on_event
        if self._listener_task is None:
            self._listener_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        assert self._redis is not None
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(CHANNEL)
        try:
            async for message in self._pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except Exception:
                    continue
                # Skip echo of our own publishes
                if event.get("_node") == self._node_id:
                    continue
                if self._on_event:
                    try:
                        result = self._on_event(event)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        log.exception("redis event handler failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("redis listener stopped")

    async def pair_set(self, code: str, rec: dict[str, Any], ttl_seconds: int) -> None:
        if not self._redis:
            return
        key = f"{PAIR_PREFIX}{code.upper()}"
        await self._redis.set(key, json.dumps(rec, default=str), ex=max(60, ttl_seconds))

    async def pair_get(self, code: str) -> Optional[dict[str, Any]]:
        if not self._redis:
            return None
        key = f"{PAIR_PREFIX}{code.upper()}"
        raw = await self._redis.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def pair_incr_uses(self, code: str) -> Optional[dict[str, Any]]:
        """Atomically bump uses; delete when max_uses exceeded."""
        rec = await self.pair_get(code)
        if not rec:
            return None
        rec["uses"] = int(rec.get("uses") or 0) + 1
        max_uses = int(rec.get("max_uses") or 5)
        ttl = int(rec.get("ttl_seconds") or 900)
        if rec["uses"] >= max_uses:
            await self._redis.delete(f"{PAIR_PREFIX}{code.upper()}")
        else:
            await self.pair_set(code, rec, ttl)
        return rec


async def try_create_bus() -> Optional[RedisBus]:
    url = redis_url()
    if not url:
        return None
    bus = RedisBus(url)
    await bus.connect()
    return bus
