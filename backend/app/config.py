from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kvotolovac.db"
    scrape_interval_minutes: int = 10
    log_level: str = "INFO"
    cors_origins: str = "*"
    bookmakers: str = "mozzart,meridian,maxbet,oktagonbet,admiralbet,balkanbet,merkurxtip,pinnbet"
    notification_gap_threshold: float = 1.5
    scraper_mode: str = "mock"  # "mock" or "real"
    proxy_list: str = ""
    rate_limit_per_second: float = 1.0
    meridian_rate_limit_per_second: float = 2.0
    league_registry_path: str = str(
        Path(__file__).resolve().parent / "data" / "league_registry.json"
    )

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
