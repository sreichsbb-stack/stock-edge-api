import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


class Cache:
    def __init__(self):
        self._client: Optional[aioredis.Redis] = None

    def _get_client(self) -> Optional[aioredis.Redis]:
        if self._client:
            return self._client
        url = settings.REDIS_URL
        if not url or not url.startswith(("redis://", "rediss://")):
            return None
        try:
            self._client = aioredis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as e:
            logger.warning(f"Redis init failed: {e}")
        return self._client

    async def get(self, key: str) -> Optional[Any]:
        client = self._get_client()
        if not client:
            return None
        try:
            val = await client.get(key)
            return json.loads(val) if val else None
        except Exception as e:
            logger.warning(f"Cache GET error [{key}]: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> None:
        client = self._get_client()
        if not client:
            return
        try:
            await client.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.warning(f"Cache SET error [{key}]: {e}")

    async def incr_with_expire(self, key: str, ttl: int = 60) -> int:
        """Atomic increment + set TTL on first write. Used for rate limiting."""
        client = self._get_client()
        if not client:
            return 0
        try:
            pipe = client.pipeline()
            await pipe.incr(key)
            await pipe.expire(key, ttl)
            results = await pipe.execute()
            return int(results[0])
        except Exception as e:
            logger.warning(f"Cache INCR error [{key}]: {e}")
            return 0


cache = Cache()
