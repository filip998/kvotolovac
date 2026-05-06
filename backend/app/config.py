from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kvotolovac.db"
    scrape_interval_minutes: int = 10
    log_level: str = "INFO"
    cors_origins: str = "*"
    bookmakers: str = "mozzart,maxbet,oktagonbet,meridian,admiralbet,balkanbet,merkurxtip,pinnbet,soccerbet,superbet,betole,365,volcanobet"
    enabled_sports: str = "basketball,football"
    # player_props = skip outcome-offer lanes and persist/analyze player_* thresholds.
    scrape_market_scope: Literal["all", "player_props"] = "all"
    # Empty = use scrape_market_scope compatibility behavior; "all" = no filtering.
    analysis_markets: str = ""
    max_middle_opportunities_per_market: int = Field(default=10, ge=1)
    notification_gap_threshold: float = 1.5
    persist_inapp_notifications: bool = False
    notification_retention_days: int = 3
    odds_history_retention_days: int = 7
    team_review_retention_days: int = 90
    scraper_mode: str = "mock"  # "mock" or "real"
    proxy_list: str = ""
    rate_limit_per_second: float = 1.0
    meridian_rate_limit_per_second: float = 2.0
    # Comma/semicolon-separated '<bookmaker>:<rate>' caps.
    bookmaker_rate_limits: str = ""
    # Comma/semicolon-separated '<bookmaker>:<lane>:<rate>' or
    # '<bookmaker>:<lane>:<detail_mode>:<rate>' caps.
    scrape_type_rate_limits: str = ""
    # partial = preview feeds only; full = preview feeds plus match-by-code enrichment.
    soccerbet_detail_mode: Literal["partial", "full"] = "partial"
    # partial = list feeds only; full = list feeds plus match detail for alternate totals.
    merkurxtip_detail_mode: Literal["partial", "full"] = "partial"
    # partial = football list feed only (result + 2.5 totals); full = list feed
    # plus per-event detail fetch to also emit double chance.
    pinnbet_detail_mode: Literal["partial", "full"] = "partial"
    # partial = football list feed only (result + 2.5 totals); full = list feed
    # plus per-event detail fetch to also emit double chance.
    betole_detail_mode: Literal["partial", "full"] = "partial"
    scrape_lookahead_hours: int = Field(default=24, ge=0)
    benchmark_dir: str = str(
        Path(__file__).resolve().parent.parent / "benchmarks"
    )
    league_registry_path: str = str(
        Path(__file__).resolve().parent / "data" / "league_registry.json"
    )
    team_registry_path: str = str(
        Path(__file__).resolve().parent / "data" / "team_registry.json"
    )

    @property
    def bookmaker_list(self) -> list[str]:
        return [b.strip() for b in self.bookmakers.split(",") if b.strip()]

    @property
    def enabled_sport_list(self) -> list[str]:
        return [s.strip() for s in self.enabled_sports.split(",") if s.strip()]

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
