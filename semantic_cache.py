import time

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from cache_models import CacheEntry
from config import settings, require_value


redis_client = Redis.from_url(
    require_value("REDIS_URL", settings.redis_url),
    decode_responses=True,
    protocol=2,
    socket_connect_timeout=2,
    socket_timeout=3,
)

CACHE_TTL_SECONDS = 3600
SEMANTIC_THRESHOLD = 0.985
SEMANTIC_CANDIDATE_COUNT = 10

def get_cache_entry(
    scope: str,
    entry_id: str,
) -> CacheEntry | None:
    """读取指定资料范围中的问答缓存。"""

    key = f"research-library:qa-cache:{scope}:{entry_id}"

    try:
        raw = redis_client.get(key)
    except RedisError:
        return None

    if raw is None:
        return None

    try:
        entry = CacheEntry.model_validate_json(raw)
    except ValidationError:
        return None

    if entry.expires_at <= time.time():
        return None

    return entry

def save_cache_entry(
    scope: str,
    entry: CacheEntry,
) -> bool:
    """保存问答缓存，并设置自动过期时间。"""

    remaining_seconds = int(
        entry.expires_at - time.time()
    )

    if remaining_seconds <= 0:
        return False

    key = f"research-library:qa-cache:{scope}:{entry.id}"

    try:
        return redis_client.set(
            key,
            entry.model_dump_json(),
            ex=remaining_seconds,
        )
    except RedisError:
        return False

def save_cache_vector(
    scope: str,
    entry_id: str,
    vector: list[float],
) -> bool:
    """将问题向量保存到当前资料范围的向量集合。"""

    key = f"research-library:qa-vectors:{scope}"

    try:
        with redis_client.pipeline(transaction=True) as pipe:
            pipe.execute_command(
                "VADD",
                key,
                "VALUES",
                len(vector),
                *vector,
                entry_id,
                "NOQUANT",
            )

            pipe.expire(key, CACHE_TTL_SECONDS)

            pipe.execute()

        return True

    except RedisError:
        return False

def find_semantic_entries(
    scope: str,
    vector: list[float],
) -> list[tuple[CacheEntry, float]]:
    """检索相似问题，并读取仍然有效的问答记录。"""

    key = f"research-library:qa-vectors:{scope}"

    try:
        rows = redis_client.execute_command(
            "VSIM",
            key,
            "VALUES",
            len(vector),
            *vector,
            "WITHSCORES",
            "COUNT",
            SEMANTIC_CANDIDATE_COUNT,
        )
    except RedisError:
        return []

    matches = []

    for entry_id, raw_score in zip(
        rows[::2],
        rows[1::2],
    ):
        score = float(raw_score)
        if score < SEMANTIC_THRESHOLD:
            continue

        entry = get_cache_entry(scope, entry_id)
        if entry is None:
            continue


        matches.append((entry, score))

    return matches

if __name__ == "__main__":
    connected = redis_client.ping()
    print("Redis连接成功：", connected)
