from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
)
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError
from app import db, jwt
from app.models.auth_session import AuthSession
from app.models.organization import Organization
from app.models.user import User
from app.middleware.rate_limiter import rate_limit
from app.governance.audit import record_audit_event

auth_bp = Blueprint("auth", __name__)


def _session_response(user, status=200):
    session = AuthSession(id=str(uuid4()), user_id=user.id)
    db.session.add(session)
    db.session.commit()

    claims = {"org_id": user.org_id, "role": user.role, "sid": session.id}
    identity = str(user.id)
    return jsonify({
        "access_token": create_access_token(identity=identity, additional_claims=claims),
        "refresh_token": create_refresh_token(identity=identity, additional_claims=claims),
        "user": user.to_dict(),
    }), status

@auth_bp.route("/login", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid email or password"}), 401
    email = data.get("email")
    password = data.get("password")
    if (
        not isinstance(email, str)
        or not isinstance(password, str)
        or not email.strip()
        or len(email) > 255
        or not password
        or len(password) > 1024
    ):
        return jsonify({"error": "Invalid email or password"}), 401
    user = User.query.filter_by(email=email.strip().lower()).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password"}), 401

    return _session_response(user)


@auth_bp.route("/register", methods=["POST"])
@rate_limit(max_requests=5, window_seconds=3600)
def register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid registration details"}), 400

    organization_name = data.get("organization_name")
    email = data.get("email")
    password = data.get("password")
    normalized_email = email.strip().lower() if isinstance(email, str) else ""
    if not isinstance(organization_name, str) or not organization_name.strip() or len(organization_name.strip()) > 255:
        return jsonify({"error": "Organization name is required (maximum 255 characters)"}), 400
    if (
        not normalized_email
        or len(normalized_email) > 255
        or "@" not in normalized_email
        or any(char.isspace() for char in normalized_email)
    ):
        return jsonify({"error": "A valid email address is required"}), 400
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        return jsonify({"error": "Password must contain at least 12 characters"}), 400

    if User.query.filter_by(email=normalized_email).first():
        return jsonify({"error": "An account already exists for this email"}), 409

    try:
        organization = Organization(name=organization_name.strip())
        db.session.add(organization)
        db.session.flush()
        user = User(
            email=normalized_email,
            password_hash=generate_password_hash(password),
            org_id=organization.id,
            role="admin",
        )
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if User.query.filter_by(email=normalized_email).first():
            return jsonify({"error": "An account already exists for this email"}), 409
        return jsonify({"error": "Registration could not be completed"}), 500

    return _session_response(user, status=201)


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
@rate_limit(max_requests=30, window_seconds=60)
def refresh():
    identity = get_jwt_identity()
    user = db.session.get(User, int(identity))
    if user is None:
        return jsonify({"error": "Session expired"}), 401
    session_id = get_jwt().get("sid")
    session = db.session.get(AuthSession, session_id) if session_id else None
    if session is None or session.user_id != user.id or session.revoked_at is not None:
        return jsonify({"error": "Session expired"}), 401
    access_token = create_access_token(
        identity=identity,
        additional_claims={"org_id": user.org_id, "role": user.role, "sid": session.id},
    )
    return jsonify({"access_token": access_token}), 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def current_user():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"error": "Session expired"}), 401
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"error": "Session expired"}), 401
    return jsonify({"user": user.to_dict()}), 200


@auth_bp.route("/logout", methods=["POST"])
@jwt_required(verify_type=False)
def logout():
    session_id = get_jwt().get("sid")
    session = db.session.get(AuthSession, session_id) if session_id else None
    if session is not None and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
    return jsonify({"message": "Logged out"}), 200


@auth_bp.route("/sessions", methods=["GET"])
@jwt_required()
def list_sessions():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"error": "Session expired"}), 401
    current_session_id = get_jwt().get("sid")
    sessions = AuthSession.query.filter_by(user_id=user_id, revoked_at=None).order_by(AuthSession.created_at.desc()).all()
    return jsonify({"sessions": [{
        "id": item.id,
        "created_at": item.created_at.isoformat() + "Z",
        "current": item.id == current_session_id,
    } for item in sessions]}), 200


@auth_bp.route("/sessions/<session_id>", methods=["DELETE"])
@jwt_required()
@rate_limit(max_requests=20, window_seconds=60)
def revoke_session(session_id):
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"error": "Session expired"}), 401
    session = AuthSession.query.filter_by(id=session_id, user_id=user_id, revoked_at=None).first()
    if session is None:
        return jsonify({"error": "Active session not found"}), 404
    session.revoked_at = datetime.now(timezone.utc)
    user = db.session.get(User, user_id)
    if user:
        record_audit_event(user.org_id, "auth.session_revoked", "auth_session", session_id, user_id, details={"self_revoked": True})
    db.session.commit()
    return jsonify({"message": "Session revoked"}), 200


@auth_bp.route("/password", methods=["PATCH"])
@jwt_required()
@rate_limit(max_requests=5, window_seconds=3600)
def change_password():
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return jsonify({"error": "Session expired"}), 401
    payload = request.get_json(silent=True)
    current_password = payload.get("current_password") if isinstance(payload, dict) else None
    new_password = payload.get("new_password") if isinstance(payload, dict) else None
    if not isinstance(current_password, str) or not isinstance(new_password, str):
        return jsonify({"error": "Current and new passwords are required"}), 400
    if len(new_password) < 12 or len(new_password) > 1024:
        return jsonify({"error": "New password must contain between 12 and 1024 characters"}), 400
    user = db.session.get(User, user_id)
    if user is None or not check_password_hash(user.password_hash, current_password):
        return jsonify({"error": "Current password is incorrect"}), 403
    user.password_hash = generate_password_hash(new_password)
    AuthSession.query.filter_by(user_id=user_id, revoked_at=None).update(
        {"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False
    )
    record_audit_event(user.org_id, "auth.password_changed", "user", user_id, user_id)
    db.session.commit()
    return jsonify({"message": "Password changed. Please sign in again."}), 200


@jwt.token_in_blocklist_loader
def is_token_revoked(_jwt_header, jwt_payload):
    session_id = jwt_payload.get("sid")
    if not session_id:
        return False
    session = db.session.get(AuthSession, session_id)
    return session is None or session.revoked_at is not None
