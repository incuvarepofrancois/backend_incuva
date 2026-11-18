import requests
from app.utils.logger import get_logger

logger = get_logger(__name__)


class NotificationService:
    def send_alert(self, recipient, subject, body, channel="email"):
        logger.info(f"Sending {channel} alert to {recipient}: {subject}")
        return {"status": "queued", "channel": channel}

    def send_slack(self, webhook_url, message):
        try:
            resp = requests.post(webhook_url, json={"text": message}, timeout=5)
            return {"status": "sent" if resp.ok else "failed"}
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")
            return {"status": "error", "error": str(e)}
