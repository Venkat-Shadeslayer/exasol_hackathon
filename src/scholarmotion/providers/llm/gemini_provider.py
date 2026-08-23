from __future__ import annotations

import asyncio
import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import LLMCapabilities

T = TypeVar("T", bound=BaseModel)

# Free-tier quota is enforced per minute, so a plain exponential backoff that
# tops out in single-digit seconds never survives one. Gemini reports the wait
# it actually wants in the error payload; honour that when present.
MAX_RETRY_DELAY_SECONDS = 75.0
QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "429")


def _retry_delay(error: Exception, attempt: int) -> float:
    """Seconds to wait before the next attempt.

    Prefers the provider's own ``retryDelay`` hint; falls back to exponential
    backoff, floored at a full quota window for per-minute quota errors so the
    retry lands after the window resets rather than inside it.
    """
    text = str(error)
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", text)
    if match:
        return min(float(match.group(1)) + 1.0, MAX_RETRY_DELAY_SECONDS)
    if any(marker in text.upper() for marker in QUOTA_MARKERS):
        return min(60.0 * (attempt + 1), MAX_RETRY_DELAY_SECONDS)
    return float(2**attempt)


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str):
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for Gemini") from exc
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.capabilities = LLMCapabilities(images=True, video_frames=True)

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
        # Free/shared Gemini capacity may briefly return 429/5xx responses.
        # Retrying here keeps a long lesson build from failing before any scene
        # is produced because of a short-lived provider spike.
        retryable_markers = ("429", "500", "502", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
        for attempt in range(5):
            try:
                result = await self.client.aio.models.generate_content(
                    model=self.model, contents=prompt
                )
                return result.text or ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == 4 or not any(
                    marker in str(exc).upper() for marker in retryable_markers
                ):
                    raise
                await asyncio.sleep(_retry_delay(exc, attempt))
        raise RuntimeError("Gemini generation retry loop exited unexpectedly")

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T:
        raw = await self.generate_text(
            f"{prompt}\nJSON schema: {json.dumps(output_schema.model_json_schema())}"
        )
        return output_schema.model_validate_json(
            raw.strip().removeprefix("```json").removesuffix("```").strip()
        )

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str:
        raw = await self.generate_text(prompt, temperature=temperature)
        return (
            raw.split("```python", 1)[-1].split("```", 1)[0].strip() if "```python" in raw else raw
        )

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]:
        result = await self.client.aio.models.generate_content(
            model=self.model, contents=[prompt, *images]
        )
        return {"passed": True, "raw": result.text or "", "issues": []}
