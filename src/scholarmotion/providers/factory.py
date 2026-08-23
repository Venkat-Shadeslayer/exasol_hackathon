from __future__ import annotations

from scholarmotion.config import Settings
from scholarmotion.providers.embeddings.local import LocalEmbeddingProvider
from scholarmotion.providers.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider
from scholarmotion.providers.llm.anthropic_provider import AnthropicProvider
from scholarmotion.providers.llm.gemini_provider import GeminiProvider
from scholarmotion.providers.llm.mock import MockLLMProvider
from scholarmotion.providers.llm.openai_compatible import OpenAICompatibleProvider
from scholarmotion.providers.tts.elevenlabs import ElevenLabsTTSProvider
from scholarmotion.providers.tts.kokoro_http import KokoroHTTPTTSProvider
from scholarmotion.providers.tts.mock import MockTTSProvider
from scholarmotion.providers.tts.openai_compatible import OpenAICompatibleTTSProvider


def create_llm_provider(settings: Settings):
    name = settings.main_llm_provider.lower()
    if name == "mock":
        return MockLLMProvider(settings.main_llm_model)
    if not settings.main_llm_api_key:
        raise ValueError(f"MAIN_LLM_API_KEY is required for {name}")
    if name == "anthropic":
        return AnthropicProvider(api_key=settings.main_llm_api_key, model=settings.main_llm_model)
    if name == "gemini":
        return GeminiProvider(api_key=settings.main_llm_api_key, model=settings.main_llm_model)
    if name in {"openai", "openai_compatible"}:
        return OpenAICompatibleProvider(
            api_key=settings.main_llm_api_key,
            model=settings.main_llm_model,
            base_url=settings.main_llm_base_url,
            supports_images=name == "openai",
        )
    raise ValueError(f"unknown LLM provider: {name}")


def create_tts_provider(settings: Settings):
    if settings.tts_provider.lower() == "mock":
        return MockTTSProvider(settings.tts_model)
    if settings.tts_provider.lower() == "elevenlabs":
        if not settings.tts_api_key:
            raise ValueError("TTS_API_KEY is required for elevenlabs")
        return ElevenLabsTTSProvider(
            api_key=settings.tts_api_key, model=settings.tts_model, voice_id=settings.tts_voice
        )
    if settings.tts_provider.lower() == "kokoro_http":
        if not settings.tts_base_url:
            raise ValueError("TTS_BASE_URL is required for kokoro_http")
        return KokoroHTTPTTSProvider(base_url=settings.tts_base_url, voice=settings.tts_voice)
    if settings.tts_provider.lower() in {"openai", "openai_compatible"}:
        if not settings.tts_api_key:
            raise ValueError("TTS_API_KEY is required")
        return OpenAICompatibleTTSProvider(
            api_key=settings.tts_api_key,
            model=settings.tts_model,
            base_url=settings.tts_base_url or "https://api.openai.com/v1",
            voice=settings.tts_voice,
        )
    raise RuntimeError(
        f"TTS provider {settings.tts_provider!r} is not bundled; register an adapter implementing TTSProvider"
    )


def create_embedding_provider(settings: Settings):
    if settings.embedding_provider.lower() in {"openai", "openai_compatible"}:
        if not settings.embedding_api_key:
            raise ValueError("EMBEDDING_API_KEY is required")
        return OpenAICompatibleEmbeddingProvider(
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url or "https://api.openai.com/v1",
            dimensions=settings.embedding_dimensions,
        )
    return LocalEmbeddingProvider(settings.embedding_model)
