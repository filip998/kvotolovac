from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kvotolovac.db"
    scrape_interval_minutes: int = 10
    log_level: str = "INFO"
    cors_origins: str = "*"
    bookmakers: str = "mozzart,meridian,maxbet"
    notification_gap_threshold: float = 1.5
    scraper_mode: str = "mock"  # "mock" or "real"
    proxy_list: str = ""
    rate_limit_per_second: float = 1.0

    @property
    def bookmaker_list(self) -> list[str]:
        return [b.strip() for b in self.bookmakers.split(",") if b.strip()]

    @property
    def proxy_url_list(self) -> list[str]:
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> str:
        return self.database_url.replace("sqlite:///", "")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
