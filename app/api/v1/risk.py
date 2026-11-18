from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt, jwt_required

from app import db
from app.governance.access import current_user_access
from app.modules.risk_detection.scorer import RiskScorer
from app.models.signal import Signal
from app.models.employee import Employee
from app.models.risk_score import RiskScore

risk_bp = Blueprint("risk", __name__)
_scorer = RiskScorer()


@risk_bp.route("/predict-turnover", methods=["POST"])
@jwt_required()
def predict_turnover():
    data = request.get_json() or {}
    employee_id = data.get("employee_id")
    if not employee_id:
        return jsonify({"error": "employee_id required"}), 400
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return jsonify({"error": "organization access required"}), 403
    _user, error = current_user_access(org_id, "risk", "manage")
    if error:
        return error
    employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first_or_404()
    result = _scorer.calculate_score(employee.id)
    score = RiskScore(
        employee_id=employee.id,
        score=result["risk_score"],
        level=result["risk_level"] if result["data_status"] == "ready" else "insufficient_data",
        confidence=result["confidence"],
        drivers=result["drivers"],
        canonical_context=result["canonical_context"],
        recommendation=result["recommendation"] if result["data_status"] == "ready" else None,
    )
    db.session.add(score)
    db.session.commit()
    return jsonify(result), 200


@risk_bp.route("/risk-scores", methods=["GET"])
@jwt_required()
def list_risk_scores():
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return jsonify({"error": "organization access required"}), 403
    _user, error = current_user_access(org_id, "risk", "view")
    if error:
        return error

    rows = (
        RiskScore.query.join(Employee, RiskScore.employee_id == Employee.id)
        .filter(Employee.org_id == org_id)
        .order_by(RiskScore.created_at.desc(), RiskScore.id.desc())
        .limit(1000)
        .all()
    )
    latest_by_employee = {}
    for row in rows:
        if row.employee_id not in latest_by_employee:
            latest_by_employee[row.employee_id] = {
                "employee_id": row.employee_id,
                "risk_score": row.score if row.level != "insufficient_data" else None,
                "risk_level": row.level,
                "data_status": "insufficient_data" if row.level == "insufficient_data" else "ready",
                "signals_count": len(row.drivers or []),
                "confidence": row.confidence,
                "drivers": row.drivers or [],
                "canonical_context": row.canonical_context,
                "recommendation": row.recommendation,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
    return jsonify({"risk_scores": list(latest_by_employee.values())}), 200


@risk_bp.route("/employees/<int:employee_id>/signals", methods=["GET"])
@jwt_required()
def get_signals(employee_id):
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return jsonify({"error": "organization access required"}), 403
    _user, error = current_user_access(org_id, "risk", "view")
    if error:
        return error
    employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first_or_404()
    signals = Signal.query.filter_by(employee_id=employee.id).order_by(Signal.captured_at.desc()).all()
    return jsonify({"signals": [s.to_dict() for s in signals]}), 200
