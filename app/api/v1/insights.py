from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt, jwt_required

from app.governance.access import current_user_access
from app.models.insight import Insight

insights_bp = Blueprint("insights", __name__)


@insights_bp.route("/insights/latest", methods=["GET"])
@jwt_required()
def get_latest_insights():
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return jsonify({"error": "organization access required"}), 403
    _user, error = current_user_access(org_id, "risk", "view")
    if error:
        return error
    insights = Insight.query.filter_by(org_id=org_id).order_by(Insight.created_at.desc()).limit(10).all()
    return jsonify({"insights": [i.to_dict() for i in insights]}), 200
