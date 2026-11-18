import re


def validate_email(email):
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_employee_data(data):
    errors = []
    if not data.get("name"):
        errors.append("name is required")
    return errors


def validate_pagination(page, per_page, max_per_page=100):
    page = max(1, int(page))
    per_page = min(max(1, int(per_page)), max_per_page)
    return page, per_page
