from __future__ import annotations

import pytest

from app.main import _close_http_clients, _create_real_scrapers, _shutdown_resources
from app.scrapers.http_client import HttpClient


@pytest.mark.asyncio
async def test_create_real_scrapers_uses_distinct_http_clients():
    scrapers, clients = _create_real_scrapers(
        ["mozzart", "maxbet"],
        rate_limit_per_second=7,
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
