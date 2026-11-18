from app.utils.logger import get_logger
from app.security.crypto import credential_cipher, encrypt_credential
from app.security.urls import is_allowed_media_url

logger = get_logger(__name__)


class MeetingConnector:
    def handle_teams_webhook(self, payload, org_id=None):
        event_type = payload.get("type")
        logger.info(f"Teams webhook received: {event_type}")
        if event_type == "meeting.ended":
            recording_url = payload.get("recording_url")
            if recording_url:
                self._schedule_transcription(recording_url, source="teams", org_id=org_id)

    def handle_zoom_webhook(self, payload, org_id=None):
        event = payload.get("event")
        logger.info(f"Zoom webhook received: {event}")
        if event == "recording.completed":
            files = payload.get("payload", {}).get("object", {}).get("recording_files", [{}])
            download_url = files[0].get("download_url") if files else None
            if download_url:
                self._schedule_transcription(download_url, source="zoom", org_id=org_id)

    def _schedule_transcription(self, audio_url, source, org_id):
        if not isinstance(org_id, int) or org_id <= 0:
            logger.warning("Rejected a recording event without a resolved organization")
            return
        if not is_allowed_media_url(audio_url, source):
            logger.warning("Rejected a meeting recording URL outside the provider allowlist")
            return
        if credential_cipher() is None:
            logger.warning("Meeting transcription was not queued because URL encryption is not configured")
            return
        logger.info(f"Scheduling transcription for {source}")
        try:
            from app.services.queue import transcribe_meeting_task
            transcribe_meeting_task.delay(encrypt_credential(audio_url.encode("utf-8")), source, org_id)
        except Exception as error:
            logger.error("Failed to schedule meeting transcription (%s)", type(error).__name__)
