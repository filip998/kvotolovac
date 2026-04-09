from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kvotolovac.db"
    scrape_interval_minutes: int = 10
    log_level: str = "INFO"
    cors_origins: str = "*"
    bookmakers: str = "mozzart,meridian,maxbet"
    notification_gap_threshold: float = 1.5

    @property
    def bookmaker_list(self) -> list[str]:
        return [b.strip() for b in self.bookmakers.split(",") if b.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> str:
        return self.database_url.replace("sqlite:///", "")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
