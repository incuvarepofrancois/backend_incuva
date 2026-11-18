import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app, celery

app = create_app(os.getenv("FLASK_ENV", "development"))
app.app_context().push()
