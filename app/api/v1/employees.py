from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt, jwt_required

from app import db
from app.governance.access import current_user_access
from app.models.employee import Employee
from app.models.employee_absence import EmployeeAbsence
from app.models.integration_sync import IntegrationSync

employees_bp = Blueprint("employees", __name__)


def _manager_validation_error(manager_id, org_id, employee_id=None):
    if manager_id is None:
        return None
    if isinstance(manager_id, bool) or not isinstance(manager_id, int) or manager_id <= 0:
        return jsonify({"error": "manager_id must be a positive integer or null"}), 400
    if manager_id == employee_id:
        return jsonify({"error": "an employee cannot be their own manager"}), 400
    if not Employee.query.filter_by(id=manager_id, org_id=org_id).first():
        return jsonify({"error": "Employee not found"}), 404
    return None


def _org_access(resource, action):
    org_id = get_jwt().get("org_id")
    if org_id is None:
        return None, (jsonify({"error": "organization access required"}), 403)
    _user, error = current_user_access(org_id, resource, action)
    return (org_id, None) if error is None else (None, error)


@employees_bp.route("/employees", methods=["GET"])
@jwt_required()
def list_employees():
    org_id, error = _org_access("employees", "view")
    if error: return error
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    pagination = Employee.query.filter_by(org_id=org_id).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "employees": [e.to_dict() for e in pagination.items],
        "total": pagination.total,
        "page": page,
    }), 200


@employees_bp.route("/employees/<int:employee_id>", methods=["GET"])
@jwt_required()
def get_employee(employee_id):
    org_id, error = _org_access("employees", "view")
    if error: return error
    employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first_or_404()
    return jsonify(employee.to_dict()), 200


@employees_bp.route("/employees/<int:employee_id>/absences", methods=["GET"])
@jwt_required()
def list_employee_absences(employee_id):
    org_id, error = _org_access("employees", "view")
    if error:
        return error
    employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first_or_404()
    sync = IntegrationSync.query.filter_by(org_id=org_id, integration_key="frappehr").first()
    coverage = (sync.data_coverage or {}).get("absence_history") if sync else None
    records = EmployeeAbsence.query.filter_by(org_id=org_id, employee_id=employee.id).order_by(
        EmployeeAbsence.start_date.desc(), EmployeeAbsence.id.desc(),
    ).limit(500).all()
    return jsonify({
        "contract_version": employee.data_contract_version,
        "coverage": coverage,
        "absences": [record.to_dict() for record in records],
    }), 200


@employees_bp.route("/employees", methods=["POST"])
@jwt_required()
def create_employee():
    org_id, error = _org_access("employees", "manage")
    if error: return error
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    allowed = {"external_id", "name", "role", "team", "manager_id", "hire_date", "status"}
    if "manager_id" in data:
        error = _manager_validation_error(data["manager_id"], org_id)
        if error:
            return error
    employee = Employee(**{k: v for k, v in data.items() if k in allowed}, org_id=org_id)
    db.session.add(employee)
    db.session.commit()
    return jsonify(employee.to_dict()), 201


@employees_bp.route("/employees/<int:employee_id>", methods=["PUT"])
@jwt_required()
def update_employee(employee_id):
    org_id, error = _org_access("employees", "manage")
    if error: return error
    employee = Employee.query.filter_by(id=employee_id, org_id=org_id).first_or_404()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    if "manager_id" in data:
        error = _manager_validation_error(data["manager_id"], org_id, employee_id=employee.id)
        if error:
            return error
    for key, value in data.items():
        if key in {"external_id", "name", "role", "team", "manager_id", "hire_date", "status"}:
            setattr(employee, key, value)
    db.session.commit()
    return jsonify(employee.to_dict()), 200
