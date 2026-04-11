import logging
from fastapi import HTTPException, Security, Depends
from fastapi.security import APIKeyHeader, APIKeyQuery

from app.core.config import settings
from app.core.cache import cache

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)


async def require_api_key(
    key_header: str = Security(api_key_header),
    key_query: str = Security(api_key_query),
) -> str:
    key = key_header or key_query
    if not key or key not in settings.api_key_list:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Rate limit: per key per 60s window
    rate_key = f"ratelimit:{key}"
    count = await cache.incr_with_expire(rate_key, ttl=60)
    if count > settings.RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({settings.RATE_LIMIT} req/min)",
        )

    return key
