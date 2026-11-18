import json
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CacheService:
    def __init__(self, redis_client=None):
        self._redis = redis_client

    def get(self, key):
        if not self._redis:
            return None
        try:
            value = self._redis.get(key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.error(f"Cache get failed: {e}")
            return None

    def set(self, key, value, ttl=300):
        if not self._redis:
            return
        try:
            self._redis.setex(key, ttl, json.dumps(value))
        except Exception as e:
            logger.error(f"Cache set failed: {e}")

    def delete(self, key):
        if not self._redis:
            return
        try:
            self._redis.delete(key)
        except Exception as e:
            logger.error(f"Cache delete failed: {e}")
