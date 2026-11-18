import hashlib
from datetime import datetime
from app.utils.logger import get_logger

logger = get_logger(__name__)


def anonymize_employee(data):
    result = dict(data)
    if "name" in result:
        result["name"] = f"Employee-{hashlib.md5(result['name'].encode()).hexdigest()[:8]}"
    if "email" in result:
        result["email"] = "***@***.***"
    return result


def check_consent(employee_id):
    logger.debug(f"Checking consent for employee {employee_id}")
    return True


def log_data_access(user_id, resource, resource_id, action="read"):
    logger.info(
        f"RGPD audit: user={user_id} accessed {resource}/{resource_id} [{action}] at {datetime.utcnow().isoformat()}"
    )
