import os

from app import celery, create_app
from app.services.bamboohr_sync import run_bamboohr_sync


@celery.task(name="integrations.sync_bamboohr_employees")
def sync_bamboohr_employees_task(org_id, sync_id):
    app = create_app(os.getenv("FLASK_ENV", "development"))
    with app.app_context():
        return run_bamboohr_sync(org_id, sync_id)
