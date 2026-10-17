from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    artifact_root: Path = Path("/data/thumbnails")
    media_indexer_api_url: str = "http://media-indexer:8000"
    ninerouter_base_url: str = "http://9router:20128"
    ninerouter_api_key: str = ""
    thumbnail_llm_model: str = "neovids-standard"
    thumbnail_llm_timeout_seconds: float = 90
    generation_timeout_seconds: float = 300


settings = Settings()
