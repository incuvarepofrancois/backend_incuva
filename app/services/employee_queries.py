"""Organization-scoped employee queries shared by REST and internal capabilities."""

from app.models.employee import Employee


def list_employee_records(org_id: int, page: int, per_page: int):
    """Return the existing REST pagination object for one organization only."""
    return Employee.query.filter_by(org_id=org_id).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )


def find_employee(org_id: int, employee_id: int) -> Employee | None:
    """Find an employee only inside the supplied, server-derived organization."""
    return Employee.query.filter_by(id=employee_id, org_id=org_id).first()
