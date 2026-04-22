from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.main import _close_http_clients, _create_real_scrapers, _shutdown_resources, lifespan
from app.scrapers.http_client import HttpClient
from app.scrapers.registry import registry


class StubScraper:
    def __init__(self, bookmaker_id: str, bookmaker_name: str) -> None:
        self._bookmaker_id = bookmaker_id
        self._bookmaker_name = bookmaker_name

    def get_bookmaker_id(self) -> str:
        return self._bookmaker_id

    def get_bookmaker_name(self) -> str:
        return self._bookmaker_name


@pytest.mark.asyncio
async def test_create_real_scrapers_uses_distinct_http_clients():
    scrapers, clients = _create_real_scrapers(
        ["mozzart", "maxbet"],
        rate_limit_per_second=7,
        meridian_rate_limit_per_second=2,
        proxies=["http://proxy.example:8080"],
    )

    try:
        assert [scraper.get_bookmaker_id() for scraper in scrapers] == ["mozzart", "maxbet"]
        assert len(clients) == 2

        first_http = scrapers[0]._http
        second_http = scrapers[1]._http

        assert isinstance(first_http, HttpClient)
        assert isinstance(second_http, HttpClient)
        assert first_http is clients[0]
        assert second_http is clients[1]
        assert first_http is not second_http
        assert first_http.rate_limit_per_second == pytest.approx(7)
        assert second_http.rate_limit_per_second == pytest.approx(7)

        await first_http._get_client()
        await second_http._get_client()
        assert first_http._client is not None
        assert second_http._client is not None
    finally:
        await _close_http_clients(clients)

    assert all(client._client is None for client in clients)


@pytest.mark.asyncio
async def test_create_real_scrapers_applies_meridian_rate_limit_override():
    scrapers, clients = _create_real_scrapers(
        ["meridian", "maxbet"],
        rate_limit_per_second=1,
        meridian_rate_limit_per_second=4,
        proxies=None,
    )

    try:
        assert [scraper.get_bookmaker_id() for scraper in scrapers] == ["meridian", "maxbet"]
        assert scrapers[0]._http.rate_limit_per_second == pytest.approx(4)
        assert scrapers[1]._http.rate_limit_per_second == pytest.approx(1)
    finally:
        await _close_http_clients(clients)


@pytest.mark.asyncio
async def test_close_http_clients_attempts_all_and_reraises_failure():
    close_order: list[str] = []

    class StubHttpClient:
        def __init__(self, name: str, exc: Exception | None = None) -> None:
            self.name = name
            self.exc = exc

        async def close(self) -> None:
            close_order.append(self.name)
            if self.exc is not None:
                raise self.exc

    with pytest.raises(RuntimeError, match="close failed") as exc_info:
        await _close_http_clients(
            [
                StubHttpClient("broken", RuntimeError("close failed")),
                StubHttpClient("healthy"),
            ]
        )

    assert close_order == ["broken", "healthy"]
    assert str(exc_info.value) == "close failed"


@pytest.mark.asyncio
async def test_shutdown_resources_closes_db_even_when_http_cleanup_fails():
    shutdown_order: list[str] = []

    async def close_http_clients(_: list[HttpClient]) -> None:
        shutdown_order.append("http")
        raise RuntimeError("http close failed")

    async def close_db_func() -> None:
        shutdown_order.append("db")

    with pytest.raises(RuntimeError, match="http close failed"):
        await _shutdown_resources(
            [],
            close_http_clients_func=close_http_clients,
            close_db_func=close_db_func,
        )

    assert shutdown_order == ["http", "db"]


@pytest.mark.asyncio
async def test_lifespan_starts_scheduler_without_blocking_on_run_cycle(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def fake_init_db(_: str) -> None:
        calls.append("init_db")

    async def fake_scheduler_start() -> None:
        calls.append("scheduler.start")

    async def fake_scheduler_stop() -> None:
        calls.append("scheduler.stop")

    async def fake_shutdown_resources(_: list[HttpClient]) -> None:
        calls.append("shutdown")

    async def unexpected_run_cycle() -> None:
        raise AssertionError("run_cycle should not be awaited during startup")

    monkeypatch.setattr("app.main.init_db", fake_init_db)
    monkeypatch.setattr("app.main._shutdown_resources", fake_shutdown_resources)
    monkeypatch.setattr("app.main.scheduler.start", fake_scheduler_start)
    monkeypatch.setattr("app.main.scheduler.stop", fake_scheduler_stop)
    monkeypatch.setattr("app.main.scheduler.run_cycle", unexpected_run_cycle)

    registry._scrapers.clear()
    async with lifespan(FastAPI()):
        assert "scheduler.start" in calls

    assert "scheduler.stop" in calls


@pytest.mark.asyncio
async def test_lifespan_upserts_configured_bookmakers_before_starting_scheduler(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []
    scrapers = [StubScraper("alpha", "Alpha"), StubScraper("beta", "Beta")]

    async def fake_init_db(_: str) -> None:
        calls.append("init_db")

    def fake_create_real_scrapers(
        bookmaker_ids: list[str],
        *,
        rate_limit_per_second: float,
        meridian_rate_limit_per_second: float,
        proxies: list[str] | None,
    ) -> tuple[list[StubScraper], list[HttpClient]]:
        calls.append(f"create:{','.join(bookmaker_ids)}")
        return scrapers, []

    async def fake_upsert_bookmaker(
        id: str,
        name: str,
        website_url: str | None = None,
    ) -> None:
        del website_url
        calls.append(f"upsert:{id}:{name}")

    async def fake_scheduler_start() -> None:
        calls.append("scheduler.start")

    async def fake_scheduler_stop() -> None:
        calls.append("scheduler.stop")

    async def fake_shutdown_resources(_: list[HttpClient]) -> None:
        calls.append("shutdown")

    monkeypatch.setattr("app.main.init_db", fake_init_db)
    monkeypatch.setattr("app.main._create_real_scrapers", fake_create_real_scrapers)
    monkeypatch.setattr("app.main.odds_store.upsert_bookmaker", fake_upsert_bookmaker)
    monkeypatch.setattr("app.main._shutdown_resources", fake_shutdown_resources)
    monkeypatch.setattr("app.main.scheduler.start", fake_scheduler_start)
    monkeypatch.setattr("app.main.scheduler.stop", fake_scheduler_stop)
    monkeypatch.setattr("app.main.settings.scraper_mode", "real", raising=False)
    monkeypatch.setattr("app.main.settings.bookmakers", "alpha,beta", raising=False)

    registry._scrapers.clear()
    async with lifespan(FastAPI()):
        assert registry.get_ids() == ["alpha", "beta"]

    assert calls == [
        "init_db",
        "create:alpha,beta",
        "upsert:alpha:Alpha",
        "upsert:beta:Beta",
        "scheduler.start",
        "scheduler.stop",
        "shutdown",
    ]
