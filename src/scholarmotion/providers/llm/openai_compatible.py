from __future__ import annotations

import base64
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import LLMCapabilities

T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        supports_images: bool = False,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("install scholarmotion[llm] for this provider") from exc
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.capabilities = LLMCapabilities(images=supports_images)

    async def generate_structured(
        self, prompt: str, output_schema: type[T], *, temperature: float | None = None
    ) -> T:
        response = await self.client.responses.parse(
            model=self.model,
            input=prompt,
            text_format=output_schema,
            temperature=temperature,
        )
        return response.output_parsed

    async def generate_text(self, prompt: str, *, temperature: float | None = None) -> str:
        response = await self.client.responses.create(
            model=self.model, input=prompt, temperature=temperature
        )
        return response.output_text

    async def generate_code(self, prompt: str, *, temperature: float | None = None) -> str:
        text = await self.generate_text(prompt, temperature=temperature)
        if "```python" in text:
            return text.split("```python", 1)[1].split("```", 1)[0].strip()
        return text.strip()

    async def analyze_images(
        self, prompt: str, images: list[bytes], *, temperature: float | None = None
    ) -> dict[str, Any]:
        if not self.capabilities.images:
            return {"passed": True, "skipped": True, "issues": []}
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for raw in images:
            encoded = base64.b64encode(raw).decode()
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"})
        response = await self.client.responses.create(
            model=self.model,
            input=[{"role": "user", "content": content}],
            temperature=temperature,
        )
        return {"passed": True, "raw": response.output_text, "issues": []}
