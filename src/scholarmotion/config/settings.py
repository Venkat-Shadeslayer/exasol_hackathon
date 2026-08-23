from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env.local is intentionally ignored by Git. It lets a local deployment
    # override checked-in defaults without placing credentials in source control.
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"), extra="ignore", case_sensitive=False
    )

    environment: str = Field("development", validation_alias="SCHOLARMOTION_ENV")
    database_url: str = "sqlite+aiosqlite:///./scholarmotion.db"
    redis_url: str = "redis://localhost:6379/0"
    object_storage_backend: str = "local"
    object_storage_root: Path = Path("projects")
    s3_endpoint_url: str | None = None
    s3_bucket: str = "scholarmotion"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    main_llm_provider: str = "mock"
    main_llm_api_key: str | None = None
    main_llm_model: str = "deterministic-v1"
    main_llm_base_url: str | None = None
    main_llm_temperature: float = 0.2
    visual_llm_provider: str | None = None
    visual_llm_api_key: str | None = None
    visual_llm_model: str | None = None
    tts_provider: str = "mock"
    tts_api_key: str | None = None
    tts_model: str = "deterministic-v1"
    tts_base_url: str | None = None
    tts_voice: str = "alloy"
    # Exasol backs the source corpus and its analytics. It is independent of
    # database_url: project/scene state stays transactional, while corpus
    # retrieval and coverage aggregation run on the analytical platform.
    exasol_enabled: bool = False
    exasol_dsn: str = "localhost:8563"
    exasol_user: str = "sys"
    exasol_password: str = "exasol"
    exasol_schema: str = "SCHOLARMOTION"
    exasol_use_udf: bool = True
    # docker-db and on-prem Personal instances serve self-signed certificates.
    # Enable only where TLS terminates with a certificate the client trusts.
    exasol_verify_tls: bool = False

    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None

    max_concurrent_scenes: int = 4
    max_render_workers: int = 2
    max_llm_requests: int = 4
    render_timeout_seconds: int = 180
    ffmpeg_binary: str = "ffmpeg"
    manim_binary: str = "manim"
    celery_task_always_eager: bool = False

    @property
    def is_mock(self) -> bool:
        return self.main_llm_provider.lower() == "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
