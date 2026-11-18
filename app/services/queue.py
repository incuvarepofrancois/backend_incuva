from app.utils.logger import get_logger

logger = get_logger(__name__)


def _run_transcription(audio_url, source, org_id):
    logger.info(f"Processing meeting transcription: {source}")
    from app.security.crypto import decrypt_credential
    from app.modules.meeting_intelligence.transcriber import Transcriber
    from app.modules.meeting_intelligence.analyzer import MeetingAnalyzer

    audio_url = decrypt_credential(audio_url).decode("utf-8")
    transcriber = Transcriber()
    analyzer = MeetingAnalyzer()
    result = transcriber.transcribe(audio_url)
    analysis = analyzer.analyze(result.get("text", ""))
    logger.info("Meeting analyzed for organization %s (%s)", org_id, source)
    return analysis


try:
    from app import celery

    @celery.task(name="transcribe_meeting")
    def transcribe_meeting_task(audio_url, source, org_id):
        return _run_transcription(audio_url, source, org_id)

except Exception as e:
    logger.warning(f"Celery not available: {e}")

    class transcribe_meeting_task:
        @staticmethod
        def delay(*args, **kwargs):
            logger.warning("Celery not available — task dropped")
