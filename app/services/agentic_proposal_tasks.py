"""Celery entry point for bounded, non-destructive proposal expiration."""

import os

from app import celery, create_app
from app.utils.logger import get_logger


logger = get_logger(__name__)


@celery.task(name="agentic.expire_action_proposals")
def expire_action_proposals_task(batch_size=100):
    app = create_app(os.getenv("FLASK_ENV", "development"))
    with app.app_context():
        from app.agentic.proposal_service import proposal_service

        expired_count = proposal_service.expire_due(batch_size=batch_size)
        logger.info("Expired %s pending agentic action proposals", expired_count)
        return expired_count
