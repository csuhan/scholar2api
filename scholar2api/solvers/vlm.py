"""VLM captcha solver for OpenAI-compatible endpoints.

Providers: OpenRouter (default) and HuggingFace. OpenRouter's free tier is
shared and volatile: requests may route to a *reasoning* model (answer in the
`reasoning` field) and upstream 429s are common, hence the fallback model list.
"""
import base64
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import httpx

from .base import CaptchaSolver

log = logging.getLogger(__name__)

# free vision models, strongest first
FREE_VL = ["google/gemma-4-31b-it:free",
           "google/gemma-4-26b-a4b-it:free",
           "dots-studio/dots-3-note-preview:free"]

PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions",
                   "OPENROUTER_API_KEY", "openrouter/free"),
    "huggingface": ("https://router.huggingface.co/v1/chat/completions",
                    "HF_TOKEN", "Qwen/Qwen2.5-VL-7B-Instruct"),
}


class VlmQuotaExhausted(RuntimeError):
    """Free daily quota is gone; more calls today will fail."""


class VlmSolver(CaptchaSolver):
    name = "vlm"

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None,
                 timeout: float = 60.0, retries: int = 3):
        self.provider = (provider or os.getenv("SCHOLAR2API_VLM_PROVIDER")
                         or "openrouter").lower()
        self.url, key_env, default = PROVIDERS[self.provider]
        self.api_key = os.getenv(key_env, "")
        self.model = model or os.getenv("SCHOLAR2API_VLM_MODEL") or default
        self.timeout, self.retries = timeout, retries

    def available(self) -> bool:
        return bool(self.api_key)

    def ask_image(self, image_png: bytes, question: str) -> str:
        if not self.api_key:
            raise RuntimeError(f"{self.provider} API key is not configured")
        payload = {
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64,"
                           + base64.b64encode(image_png).decode()}}]}],
            "max_tokens": 400, "temperature": 0,
        }
        if self.provider == "openrouter" and self.model in ("openrouter/free", ""):
            payload |= {"models": FREE_VL, "route": "fallback"}
        else:
            payload["model"] = self.model

        error = None
        for _ in range(self.retries):
            try:
                r = httpx.post(self.url, json=payload, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {self.api_key}"})
                if r.status_code == 429:
                    if "free-models-per-day" in r.text:
                        raise VlmQuotaExhausted(
                            "OpenRouter free daily quota exhausted; add credits "
                            "or wait for the reset")
                    error = RuntimeError(f"429: {r.text[:100]}")
                    continue
                if r.status_code >= 500:
                    error = RuntimeError(f"{r.status_code}: {r.text[:100]}")
                    continue
                if r.status_code == 401:
                    raise RuntimeError("VLM API key rejected (401)")
                r.raise_for_status()
                message = r.json()["choices"][0]["message"]
                return (message.get("content") or message.get("reasoning") or "").strip()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                error = e
                log.warning("VLM transport error: %s", e)
        raise RuntimeError(f"VLM failed after {self.retries} tries: {error}")

    def select_tiles(self, tiles_png: List[bytes], target: str) -> List[int]:
        def ask(item):
            index, tile = item
            try:
                answer = self.ask_image(
                    tile, f"Does this image contain {target}? "
                          "Answer strictly yes or no.").lower()
                verdicts = re.findall(r"\b(yes|no)\b", answer)
                return index, bool(verdicts and verdicts[-1] == "yes")
            except VlmQuotaExhausted:
                raise  # abort the challenge, don't burn 15 more calls
            except Exception as e:
                log.warning("tile %d: %s", index, e)
                return index, False

        with ThreadPoolExecutor(max_workers=4) as pool:
            return [i for i, hit in pool.map(ask, enumerate(tiles_png)) if hit]
