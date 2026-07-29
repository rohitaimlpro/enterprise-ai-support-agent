"""
Redis-backed cache of recent conversation history.

Postgres (see Message in app/models.py) remains the source of truth for
full history -- this cache just avoids a DB round-trip to rebuild the
agent's short-term memory on every chat turn. Each conversation's cache is
a capped, TTL'd list of {"role", "content"} dicts.
"""

import json

import redis

from app.config import get_settings


def _client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)


def _key(conversation_id: str) -> str:
    return f"conversation:{conversation_id}:messages"


def get_cached_messages(conversation_id: str) -> list[dict] | None:
    """Returns cached messages, or None on a cache miss (caller should
    fall back to Postgres and call set_cached_messages to repopulate)."""
    raw = _client().lrange(_key(conversation_id), 0, -1)
    if not raw:
        return None
    return [json.loads(item) for item in raw]


def append_message(conversation_id: str, role: str, content: str) -> None:
    settings = get_settings()
    key = _key(conversation_id)
    client = _client()
    client.rpush(key, json.dumps({"role": role, "content": content}))
    client.ltrim(key, -settings.conversation_history_cache_size, -1)
    client.expire(key, 60 * 60 * 24)  # 24h; Postgres is the durable copy


def set_cached_messages(conversation_id: str, messages: list[dict]) -> None:
    """(Re)populates the cache from Postgres after a cache miss."""
    settings = get_settings()
    key = _key(conversation_id)
    client = _client()
    trimmed = messages[-settings.conversation_history_cache_size :]

    pipeline = client.pipeline()
    pipeline.delete(key)
    if trimmed:
        pipeline.rpush(key, *[json.dumps(m) for m in trimmed])
    pipeline.expire(key, 60 * 60 * 24)
    pipeline.execute()
