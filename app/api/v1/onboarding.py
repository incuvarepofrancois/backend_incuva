from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app import db
from app.governance.access import current_user_access
from app.models.employee import Employee
from app.models.onboarding import OnboardingPlan, OnboardingTask
from app.modules.onboarding.planner import OnboardingPlanner
from app.services.onboarding_queries import list_onboarding_plans

onboarding_bp = Blueprint("onboarding", __name__)
_planner = OnboardingPlanner()
TASK_STATUSES = {"pending", "in_progress", "completed"}
TASK_OWNERS = {"hr", "manager", "employee"}


def _organization_id(action):
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return None, (jsonify({"error": "organization access required"}), 403)
    _user, error = current_user_access(org_id, "onboarding", action)
    if error:
        return None, error
    return org_id, None


def _parse_date(value, field_name):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from error


@onboarding_bp.route("/onboarding/plans", methods=["GET"])
@jwt_required()
def list_plans():
    org_id, error = _organization_id("view")
    if error:
        return error
    plans = list_onboarding_plans(org_id)
    return jsonify({"plans": [plan.to_dict() for plan in plans]}), 200


@onboarding_bp.route("/onboarding/plans", methods=["POST"])
@jwt_required()
def create_plan():
    org_id, error = _organization_id("manage")
    if error:
        return error
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    employee_id = data.get("employee_id")
    if isinstance(employee_id, bool) or not isinstance(employee_id, int) or employee_id <= 0:
        return jsonify({"error": "employee_id must be a positive integer"}), 400
    employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first_or_404()

    try:
        start_date = _parse_date(data["start_date"], "start_date") if data.get("start_date") else employee.hire_date or date.today()
    except ValueError as parse_error:
        return jsonify({"error": str(parse_error)}), 400

    existing = OnboardingPlan.query.filter_by(
        employee_id=employee.id,
        org_id=org_id,
        status="active",
    ).first()
    if existing:
        return jsonify({"error": "An active onboarding plan already exists for this employee", "plan": existing.to_dict()}), 409

    plan = OnboardingPlan(
        org_id=org_id,
        employee_id=employee.id,
        start_date=start_date,
        created_by=int(get_jwt_identity()),
    )
    for task in _planner.build_tasks(employee.role, start_date):
        plan.tasks.append(OnboardingTask(**task))
    db.session.add(plan)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "Onboarding plan could not be saved"}), 500
    return jsonify(plan.to_dict()), 201


@onboarding_bp.route("/onboarding/plans/<int:plan_id>", methods=["GET"])
@jwt_required()
def get_plan(plan_id):
    org_id, error = _organization_id("view")
    if error:
        return error
    plan = OnboardingPlan.query.filter_by(id=plan_id, org_id=org_id).first_or_404()
    return jsonify(plan.to_dict()), 200


@onboarding_bp.route("/onboarding/plans/<int:plan_id>/tasks", methods=["POST"])
@jwt_required()
def add_task(plan_id):
    org_id, error = _organization_id("manage")
    if error:
        return error
    plan = OnboardingPlan.query.filter_by(id=plan_id, org_id=org_id).first_or_404()
    if plan.status == "completed":
        return jsonify({"error": "Completed plans cannot receive new tasks"}), 409
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400

    title = data.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        return jsonify({"error": "title must contain between 1 and 160 characters"}), 400
    description = data.get("description", "")
    if not isinstance(description, str) or len(description) > 1000:
        return jsonify({"error": "description must be a string of at most 1000 characters"}), 400
    owner_type = data.get("owner_type", "manager")
    if not isinstance(owner_type, str) or owner_type not in TASK_OWNERS:
        return jsonify({"error": "owner_type must be hr, manager, or employee"}), 400
    try:
        due_date = _parse_date(data["due_date"], "due_date") if data.get("due_date") else plan.start_date
    except ValueError as parse_error:
        return jsonify({"error": str(parse_error)}), 400

    task = OnboardingTask(
        plan_id=plan.id,
        title=title.strip(),
        description=description.strip() or None,
        category="custom",
        owner_type=owner_type,
        due_date=due_date,
        sort_order=len(plan.tasks),
    )
    plan.status = "active"
    plan.completed_at = None
    db.session.add(task)
    db.session.commit()
    return jsonify(plan.to_dict()), 201


@onboarding_bp.route("/onboarding/tasks/<int:task_id>", methods=["PATCH"])
@jwt_required()
def update_task(task_id):
    org_id, error = _organization_id("manage")
    if error:
        return error
    task = (
        OnboardingTask.query.join(OnboardingPlan)
        .filter(OnboardingTask.id == task_id, OnboardingPlan.org_id == org_id)
        .first_or_404()
    )
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    status = data.get("status")
    if not isinstance(status, str) or status not in TASK_STATUSES:
        return jsonify({"error": "status must be pending, in_progress, or completed"}), 400

    task.status = status
    task.completed_at = datetime.utcnow() if status == "completed" else None
    plan = task.plan
    if plan.tasks and all(item.status == "completed" for item in plan.tasks):
        plan.status = "completed"
        plan.completed_at = datetime.utcnow()
    else:
        plan.status = "active"
        plan.completed_at = None
    plan.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(plan.to_dict()), 200
