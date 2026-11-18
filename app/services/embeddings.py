from app.utils.logger import get_logger

logger = get_logger(__name__)


class EmbeddingsService:
    def __init__(self):
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                logger.warning("sentence-transformers not installed")
        return self._model

    def embed(self, text):
        model = self._load()
        if model is None:
            return []
        return model.encode(text).tolist()

    def similarity(self, text_a, text_b):
        from numpy import dot
        from numpy.linalg import norm
        a = self.embed(text_a)
        b = self.embed(text_b)
        if not a or not b:
            return 0.0
        return float(dot(a, b) / (norm(a) * norm(b)))
