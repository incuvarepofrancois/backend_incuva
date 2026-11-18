from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy import desc

from app.modules.meeting_intelligence.analyzer import MeetingAnalyzer
from app.governance.access import current_user_access
from app.governance.budgets import consume_budget
from app.governance.controls import consume_approval, evaluate_control
from app.middleware.rate_limiter import rate_limit
from app.models.meeting import Meeting

meetings_bp = Blueprint("meetings", __name__)
_analyzer = MeetingAnalyzer()


def _org_access(action):
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return None, (jsonify({"error": "organization access required"}), 403)
    _user, error = current_user_access(org_id, "meetings", action)
    return (org_id, None) if error is None else (None, error)


@meetings_bp.route("/meeting-analyze", methods=["POST"])
@jwt_required()
@rate_limit(max_requests=10, window_seconds=60)
def analyze_meeting():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    transcript = data.get("transcript", "")
    if not isinstance(transcript, str) or not transcript.strip():
        return jsonify({"error": "transcript is required"}), 400
    if len(transcript) > 200_000:
        return jsonify({"error": "transcript is too long"}), 413
    org_id, error = _org_access("manage")
    if error:
        return error
    user_id = int(get_jwt_identity())
    approval_id = data.get("approval_id")
    action_check = evaluate_control(
        org_id, "action_allowlist", {"action": "meeting.analyze"}, actor_user_id=user_id,
    )
    if not action_check["allowed"]:
        return jsonify({"error": "this action is not allowed by the organization policy", "reason": action_check.get("reason")}), 403
    approval_check = evaluate_control(
        org_id, "require_approval", {"action": "meeting.analyze", "approval_id": approval_id},
        actor_user_id=user_id,
    )
    if not approval_check["allowed"]:
        return jsonify({"error": "human approval is required before sending this transcript for analysis", "reason": approval_check["reason"]}), 403
    if approval_check.get("requires_approval") and approval_check.get("mode") == "block" and not consume_approval(org_id, approval_id, "meeting.analyze", actor_user_id=user_id):
        return jsonify({"error": "approval is no longer valid"}), 403
    budget_result = consume_budget(org_id, 1, "REQUESTS", "llm_requests", user_id=user_id, source="meeting_analysis")
    if not budget_result["allowed"]:
        return jsonify({"error": "organization AI analysis budget exceeded", "reason": budget_result["reason"]}), 429
    try:
        result = _analyzer.analyze(transcript.strip())
    except Exception:
        return jsonify({"error": "meeting analysis failed"}), 502

    title = data.get("title") or "Réunion analysée"
    if not isinstance(title, str):
        return jsonify({"error": "title must be a string"}), 400
    meeting = Meeting(
        org_id=org_id,
        source="transcript",
        title=title.strip()[:255] or "Réunion analysée",
        participants=result["participants"],
        transcript=transcript.strip(),
        decisions=result["decisions"],
        action_items=result["action_items"],
        risk_signals=[],
    )
    try:
        from app import db
        db.session.add(meeting)
        db.session.commit()
    except Exception:
        from app import db
        db.session.rollback()
        return jsonify({"error": "meeting could not be saved"}), 500
    return jsonify(meeting.to_dict()), 201


@meetings_bp.route("/meetings", methods=["GET"])
@jwt_required()
def list_meetings():
    org_id, error = _org_access("view")
    if error:
        return error
    meetings = Meeting.query.filter_by(org_id=org_id).order_by(desc(Meeting.created_at)).limit(100).all()
    return jsonify({"meetings": [meeting.to_dict() for meeting in meetings]}), 200


@meetings_bp.route("/meetings/<int:meeting_id>", methods=["GET"])
@jwt_required()
def get_meeting(meeting_id):
    org_id, error = _org_access("view")
    if error:
        return error
    meeting = Meeting.query.filter_by(id=meeting_id, org_id=org_id).first_or_404()
    return jsonify(meeting.to_dict()), 200


@meetings_bp.route("/meetings/<int:meeting_id>/review", methods=["PATCH"])
@jwt_required()
def review_meeting(meeting_id):
    org_id, error = _org_access("manage")
    if error:
        return error
    meeting = Meeting.query.filter_by(id=meeting_id, org_id=org_id).first_or_404()
    reviewed = (request.get_json() or {}).get("reviewed")
    if not isinstance(reviewed, bool):
        return jsonify({"error": "reviewed must be a boolean"}), 400
    if reviewed:
        meeting.reviewed_at = datetime.now(timezone.utc)
        meeting.reviewed_by = int(get_jwt_identity())
    else:
        meeting.reviewed_at = None
        meeting.reviewed_by = None
    from app import db
    db.session.commit()
    return jsonify(meeting.to_dict()), 200
