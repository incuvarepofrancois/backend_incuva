from app.modules.risk_detection.scorer import RiskScorer
from app.utils.logger import get_logger

logger = get_logger(__name__)


class JarvisOrchestrator:
    def __init__(self):
        self.scorer = RiskScorer()

    def process_employee(self, employee_id):
        logger.info(f"Jarvis processing employee {employee_id}")
        result = self.scorer.calculate_score(employee_id)

        if result["risk_level"] in ("high", "critical"):
            self._create_insight(employee_id, result)
            self._notify_if_critical(employee_id, result)

        return result

    def _create_insight(self, employee_id, result):
        try:
            from app import db
            from app.models.employee import Employee
            from app.models.insight import Insight
            employee = db.session.get(Employee, employee_id)
            if employee is None or employee.org_id is None:
                logger.warning("Skipping risk insight because the employee has no organization")
                return
            insight = Insight(
                org_id=employee.org_id,
                type="risk_alert",
                title=f"Risk alert for employee {employee_id}",
                description=f"Risk level: {result['risk_level']} (score: {result['risk_score']})",
                data=result,
                severity="critical" if result["risk_level"] == "critical" else "warning",
            )
            db.session.add(insight)
            db.session.commit()
        except Exception as e:
            from app import db
            db.session.rollback()
            logger.error(f"Failed to create insight: {e}")

    def _notify_if_critical(self, employee_id, result):
        if result["risk_level"] == "critical":
            logger.warning(f"CRITICAL risk for employee {employee_id}: {result}")
