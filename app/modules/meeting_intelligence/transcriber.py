import os
import requests
from app.security.urls import is_allowed_media_url

from app.utils.logger import get_logger

logger = get_logger(__name__)


class Transcriber:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")

    def transcribe(self, audio_url):
        logger.info("Transcribing meeting audio")
        if not is_allowed_media_url(audio_url, "teams") and not is_allowed_media_url(audio_url, "zoom"):
            raise ValueError("Recording URL is outside the provider allowlist")
        try:
            response = requests.get(audio_url, timeout=30, allow_redirects=False, stream=True)
            if response.is_redirect or not response.ok:
                raise ValueError("Recording provider returned a non-success response")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 25 * 1024 * 1024:
                raise ValueError("Recording exceeds the supported upload size")
            audio_chunks = []
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > 25 * 1024 * 1024:
                    raise ValueError("Recording exceeds the supported upload size")
                audio_chunks.append(chunk)
            audio_content = b"".join(audio_chunks)
            headers = {"Authorization": f"Bearer {self.api_key}"}
            files = {"file": ("audio.mp3", audio_content, "audio/mpeg")}
            data = {"model": "whisper-1", "response_format": "verbose_json"}
            result = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                files=files,
                data=data,
                timeout=120,
            )
            result.raise_for_status()
            return result.json()
        except Exception as error:
            logger.error("Transcription failed (%s)", type(error).__name__)
            return {"text": "", "segments": []}
