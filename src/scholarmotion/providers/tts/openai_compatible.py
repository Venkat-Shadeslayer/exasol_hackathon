from __future__ import annotations

from pathlib import Path

import httpx

from scholarmotion.media.audio import estimate_sentence_timings, wav_duration
from scholarmotion.schemas import TTSResult


class OpenAICompatibleTTSProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        voice: str = "alloy",
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.voice = voice

    async def synthesize(
        self, text: str, output_path: Path, *, language: str = "English"
    ) -> TTSResult:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "voice": self.voice,
                    "input": text,
                    "response_format": "wav",
                },
            )
            response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        duration = wav_duration(output_path)
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sentence_timings=estimate_sentence_timings(text, duration),
            word_timings=[],
        )
