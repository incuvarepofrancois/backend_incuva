from functools import wraps
from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
        except Exception as e:
            return jsonify({"error": "Unauthorized", "detail": str(e)}), 401
        return f(*args, **kwargs)
    return decorated


def require_plan(plan):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            # TODO: check plan from JWT claims or DB
            return f(*args, **kwargs)
        return decorated
    return decorator
