"""Fixed, organization-scoped read handlers for the A2 capability set."""

from app.agentic.context import ExecutionContext
from app.agentic.capabilities.employee import EmployeeListInput, EmployeeGetInput
from app.services.employee_queries import find_employee, list_employee_records
from app.services.onboarding_queries import ONBOARDING_PLAN_LIMIT, list_onboarding_plans


def list_employees_handler(context: ExecutionContext, arguments: EmployeeListInput) -> dict:
    pagination = list_employee_records(context.org_id, arguments.page, arguments.per_page)
    employees = [
        {
            "id": employee.id,
            "name": employee.name,
            "role": employee.role,
            "team": employee.team,
            "status": employee.status,
        }
        for employee in pagination.items
    ]
    returned = len(employees)
    return {
        "employees": employees,
        "total": pagination.total,
        "page": pagination.page,
        "page_size": arguments.per_page,
        "returned": returned,
        "has_next_page": pagination.has_next,
        "coverage_complete": returned == pagination.total,
    }


def get_employee_handler(context: ExecutionContext, arguments: EmployeeGetInput) -> dict:
    employee = find_employee(context.org_id, arguments.employee_id)
    if employee is None:
        from app.agentic.errors import CapabilityResourceNotFoundError
        raise CapabilityResourceNotFoundError("Employee not found")
    return {
        "id": employee.id,
        "name": employee.name,
        "role": employee.role,
        "team": employee.team,
        "status": employee.status,
        "external_id": employee.external_id,
        "manager_id": employee.manager_id,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "data_contract_version": employee.data_contract_version,
    }


def list_onboarding_plans_handler(context: ExecutionContext, _arguments) -> dict:
    plans = list_onboarding_plans(context.org_id)
    visible_plans = []
    omitted_invalid_parent = False
    for plan in plans:
        # The plan query is organization-scoped. Employee.org_id is not constrained
        # by a composite FK, so avoid exposing a cross-tenant employee association.
        if plan.employee is None or plan.employee.org_id != context.org_id:
            omitted_invalid_parent = True
            continue
        tasks = []
        for task in plan.tasks:
            if task.plan_id != plan.id:
                omitted_invalid_parent = True
                continue
            tasks.append({"id": task.id, "title": task.title, "status": task.status})
        total_tasks = len(plan.tasks)
        completed_tasks = sum(task.status == "completed" for task in plan.tasks)
        visible_plans.append({
            "id": plan.id,
            "employee_id": plan.employee_id,
            "start_date": plan.start_date.isoformat(),
            "status": plan.status,
            "progress": round(completed_tasks / total_tasks * 100) if total_tasks else 0,
            "completed_tasks": completed_tasks,
            "total_tasks": total_tasks,
            "tasks": tasks,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
            "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
        })

    if len(plans) == ONBOARDING_PLAN_LIMIT:
        completeness = "unknown"
    elif omitted_invalid_parent:
        completeness = "partial"
    else:
        completeness = "complete"
    return {
        "plans": visible_plans,
        "coverage_limit": ONBOARDING_PLAN_LIMIT,
        "returned": len(visible_plans),
        "completeness": completeness,
    }
