from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    AV_KEY: str = ""
    TWELVEDATA_KEY: str = ""
    FINNHUB_KEY: str = ""
    REDIS_URL: str = ""
    API_KEYS: str = "free123"
    RATE_LIMIT: int = 100

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
