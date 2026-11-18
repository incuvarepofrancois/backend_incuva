import hashlib
import time
from functools import wraps

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity


def _caller_key():
    try:
        claims = get_jwt() or {}
        identity = get_jwt_identity()
        if identity is not None:
            return f"org:{claims.get('org_id', 'unknown')}:user:{identity}"
    except Exception:
        pass
    return f"ip:{request.remote_addr or 'unknown'}"


def _consume_local(key, now, window_seconds):
    buckets = current_app.extensions["rate_limit_memory"]
    bucket_start = int(now // window_seconds)
    storage_key = f"{key}:{bucket_start}"
    count = buckets.get(storage_key, 0) + 1
    buckets[storage_key] = count
    if len(buckets) > 10000:
        current_window = bucket_start
        for stale_key in [item for item in buckets if not item.endswith(f":{current_window}")]:
            buckets.pop(stale_key, None)
    return count


def rate_limit(max_requests=60, window_seconds=60):
    """Apply a per-user or per-IP limit, shared through Redis outside tests."""
    if isinstance(max_requests, bool) or max_requests < 1 or window_seconds < 1:
        raise ValueError("rate limit values must be positive integers")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            now = time.time()
            caller = _caller_key()
            endpoint = request.endpoint or request.path
            digest = hashlib.sha256(f"{caller}:{endpoint}".encode("utf-8")).hexdigest()
            redis_client = current_app.extensions.get("redis_client")
            try:
                if redis_client is None:
                    if not current_app.testing and current_app.config.get("ENVIRONMENT") == "production":
                        return jsonify({"error": "Rate limiting is unavailable"}), 503
                    count = _consume_local(digest, now, window_seconds)
                else:
                    pipeline = redis_client.pipeline(transaction=True)
                    key = f"incuva:rate:{digest}"
                    pipeline.incr(key)
                    pipeline.expire(key, window_seconds)
                    count = pipeline.execute()[0]
            except Exception:
                if current_app.testing or current_app.config.get("ENVIRONMENT") != "production":
                    count = _consume_local(digest, now, window_seconds)
                else:
                    current_app.logger.exception("Distributed rate limiter unavailable")
                    return jsonify({"error": "Rate limiting is unavailable"}), 503

            if count > max_requests:
                response = jsonify({"error": "Rate limit exceeded", "retry_after_seconds": window_seconds})
                response.status_code = 429
                response.headers["Retry-After"] = str(window_seconds)
                return response
            return view(*args, **kwargs)

        return wrapped

    return decorator


def mark_once(namespace, value, ttl_seconds):
    """Reject a repeated signed webhook within its replay window."""
    digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
    key = f"incuva:replay:{digest}"
    redis_client = current_app.extensions.get("redis_client")
    if redis_client is not None:
        try:
            return bool(redis_client.set(key, "1", nx=True, ex=ttl_seconds))
        except Exception:
            if current_app.config.get("ENVIRONMENT") == "production":
                current_app.logger.exception("Webhook replay store unavailable")
                raise RuntimeError("Webhook replay protection is unavailable") from None
    elif current_app.config.get("ENVIRONMENT") == "production":
        raise RuntimeError("Webhook replay protection is unavailable")

    now = time.time()
    cache = current_app.extensions.setdefault("webhook_replay_memory", {})
    for entry_key, expiry in list(cache.items()):
        if expiry <= now:
            cache.pop(entry_key, None)
    if key in cache:
        return False
    cache[key] = now + ttl_seconds
    return True
