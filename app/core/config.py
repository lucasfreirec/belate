from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration read from environment variables, or from `.env` in development."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    prim_api_key: SecretStr = SecretStr("")
    prim_base_url: str = "https://prim.iledefrance-mobilites.fr/marketplace"
    # Station polled by the unitary request. Default: Cité Universitaire.
    prim_station_ref: str = "STIF:StopArea:SP:473843:"

    database_url: str = "postgresql+psycopg://belate:belate@localhost:5432/belate"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
