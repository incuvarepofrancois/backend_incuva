import os

from app import celery, create_app
from app.services.frappehr_sync import run_frappehr_sync


@celery.task(name="integrations.sync_frappehr_employees")
def sync_frappehr_employees_task(org_id, sync_id):
    app = create_app(os.getenv("FLASK_ENV", "development"))
    with app.app_context():
        return run_frappehr_sync(org_id, sync_id)
