from app.services.llm import LLMClient
from app.utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Extract from the meeting transcript: decisions made, action items with owners, "
    "and list of participants. Return as JSON with keys: decisions, action_items, participants."
)


class DecisionExtractor:
    def __init__(self):
        self.llm = LLMClient()

    def extract(self, transcript):
        if not transcript:
            return {"decisions": [], "action_items": [], "participants": []}

        prompt = f"Extract decisions and action items from:\n\n{transcript}"
        try:
            response = self.llm.call(prompt, system=SYSTEM_PROMPT)
            return {"decisions": [], "action_items": [], "participants": [], "raw": response}
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return {"decisions": [], "action_items": [], "participants": []}
