import os
import time

from app.utils.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "local").strip().lower()
        self.model = os.getenv("HF_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
        self.max_retries = 3
        self._client = None
        self._tokenizer = None
        self._model = None

    def call(self, prompt, system=None):
        if self.provider in {"local", "huggingface", "hf"}:
            return self._call_local(prompt, system)
        if self.provider == "anthropic":
            return self._call_anthropic(prompt, system)
        raise ValueError(f"Unsupported LLM_PROVIDER: {self.provider}")

    def _call_anthropic(self, prompt, system=None):
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=api_key)

        messages = [{"role": "user", "content": prompt}]
        kwargs = {"model": os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8"), "max_tokens": 2048, "messages": messages}
        if system:
            kwargs["system"] = system

        for attempt in range(self.max_retries):
            try:
                response = self._client.messages.create(**kwargs)
                return response.content[0].text
            except anthropic.RateLimitError:
                wait = 2 ** attempt
                logger.warning(f"Rate limit hit, retrying in {wait}s")
                time.sleep(wait)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                raise

        raise RuntimeError("LLM call failed after max retries")

    def _call_local(self, prompt, system=None):
        if self._model is None:
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except ImportError as exc:
                raise RuntimeError(
                    "Local Hugging Face inference requires torch and transformers. "
                    "Install backend/requirements.txt before using Mobyus."
                ) from exc

            device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if device == "cuda" else torch.float32
            logger.info(f"Loading local model {self.model} on {device}")
            self._tokenizer = AutoTokenizer.from_pretrained(self.model)
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model,
                torch_dtype=dtype,
            ).to(device)
            self._model.eval()
            self._device = device

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        input_ids = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(self._device)

        import torch

        with torch.inference_mode():
            output_ids = self._model.generate(
                input_ids,
                max_new_tokens=2048,
                do_sample=False,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generated_ids = output_ids[0][input_ids.shape[-1]:]
        return self._tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
