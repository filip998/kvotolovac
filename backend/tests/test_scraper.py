from __future__ import annotations

import pytest

from app.scrapers.base import BaseScraper
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import ScraperRegistry
from app.models.schemas import RawOddsData, RawOutcomeOffer


@pytest.mark.asyncio
async def test_mock_scraper_returns_data():
    scraper = MockScraper("mozzart")
    data = await scraper.scrape_odds("euroleague")
    assert len(data) > 0
    assert all(isinstance(d, RawOddsData) for d in data)


@pytest.mark.asyncio
async def test_mock_scraper_bookmaker_id():
    scraper = MockScraper("meridian")
    assert scraper.get_bookmaker_id() == "meridian"
    assert scraper.get_bookmaker_name() == "Meridian"


@pytest.mark.asyncio
async def test_mock_scraper_supported_leagues():
    scraper = MockScraper("maxbet")
    leagues = scraper.get_supported_leagues()
    assert "euroleague" in leagues


@pytest.mark.asyncio
async def test_mock_scraper_supports_superbet():
    scraper = MockScraper("superbet")
    data = await scraper.scrape_odds("euroleague")

    assert scraper.get_bookmaker_name() == "Superbet"
    assert len(data) > 0
    assert all(item.bookmaker_id == "superbet" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_betole():
    scraper = MockScraper("betole")
    data = await scraper.scrape_odds("euroleague")

    assert scraper.get_bookmaker_name() == "BetOle"
    assert len(data) > 0
    assert all(item.bookmaker_id == "betole" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_365():
    scraper = MockScraper("365")
    data = await scraper.scrape_odds("euroleague")

    assert scraper.get_bookmaker_name() == "365"
    assert len(data) > 0
    assert all(item.bookmaker_id == "365" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_merkurxtip():
    scraper = MockScraper("merkurxtip")
    data = await scraper.scrape_odds("euroleague")

    assert scraper.get_bookmaker_name() == "MERKUR X TIP"
    assert len(data) > 0
    assert all(item.bookmaker_id == "merkurxtip" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_merkurxtip_football_outcomes():
    scraper = MockScraper("merkurxtip")
    data = await scraper.scrape_outcome_offers("football")

    assert scraper.get_supported_outcome_sports() == ["football"]
    assert len(data) > 0
    assert all(isinstance(item, RawOutcomeOffer) for item in data)
    assert all(item.bookmaker_id == "merkurxtip" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_oktagonbet():
    scraper = MockScraper("oktagonbet")
    data = await scraper.scrape_odds("euroleague")

    assert scraper.get_bookmaker_name() == "OktagonBet"
    assert len(data) > 0
    assert all(item.bookmaker_id == "oktagonbet" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_oktagonbet_football_outcomes():
    scraper = MockScraper("oktagonbet")
    data = await scraper.scrape_outcome_offers("football")

    assert scraper.get_supported_outcome_sports() == ["football"]
    assert len(data) > 0
    assert all(isinstance(item, RawOutcomeOffer) for item in data)
    assert all(item.bookmaker_id == "oktagonbet" for item in data)


@pytest.mark.asyncio
async def test_mock_scraper_supports_betole_football_outcomes():
    scraper = MockScraper("betole")
    data = await scraper.scrape_outcome_offers("football")

    assert scraper.get_supported_outcome_sports() == ["football"]
    assert len(data) > 0
    assert all(isinstance(item, RawOutcomeOffer) for item in data)
    assert all(item.bookmaker_id == "betole" for item in data)
    assert {item.market_type for item in data} == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }


@pytest.mark.asyncio
async def test_mock_scraper_supports_365_football_outcomes():
    scraper = MockScraper("365")
    data = await scraper.scrape_outcome_offers("football")

    assert scraper.get_supported_outcome_sports() == ["football"]
    assert len(data) > 0
    assert all(isinstance(item, RawOutcomeOffer) for item in data)
    assert all(item.bookmaker_id == "365" for item in data)
    assert {item.market_type for item in data} == {
        "football_result",
        "football_double_chance",
        "football_total_goals",
    }


@pytest.mark.asyncio
async def test_mock_scraper_unsupported_league():
    scraper = MockScraper("mozzart")
    data = await scraper.scrape_odds("nba")
    assert data == []


def test_mock_scraper_invalid_bookmaker():
    with pytest.raises(ValueError):
        MockScraper("unknown_bookmaker")


@pytest.mark.asyncio
async def test_mock_scraper_has_player_names():
    scraper = MockScraper("mozzart")
    data = await scraper.scrape_odds("euroleague")
    player_names = [d.player_name for d in data if d.player_name]
    assert len(player_names) > 0


@pytest.mark.asyncio
async def test_mock_scraper_different_thresholds_across_bookmakers():
    """Verify that different bookmakers have different thresholds (intentional discrepancies)."""
    mozzart = MockScraper("mozzart")
    meridian = MockScraper("meridian")

    m_data = await mozzart.scrape_odds("euroleague")
    r_data = await meridian.scrape_odds("euroleague")

    m_thresholds = {d.player_name: d.threshold for d in m_data}
    r_thresholds = {d.player_name: d.threshold for d in r_data}

    # At least some thresholds should differ (the mock data is designed this way)
    diffs = 0
    for player in m_thresholds:
        for rp in r_thresholds:
            # Fuzzy match — just check last names
            m_last = player.split()[-1].lower() if player else ""
            r_last = rp.split()[-1].lower() if rp else ""
            if m_last and r_last and m_last == r_last:
                if m_thresholds[player] != r_thresholds[rp]:
                    diffs += 1
    assert diffs > 0, "Mock data should have intentional threshold differences"


def test_registry_register_and_get():
    reg = ScraperRegistry()
    scraper = MockScraper("mozzart")
    reg.register(scraper)
    assert reg.get("mozzart") is scraper
    assert reg.get("unknown") is None


def test_registry_get_all():
    reg = ScraperRegistry()
    reg.register(MockScraper("mozzart"))
    reg.register(MockScraper("meridian"))
    assert len(reg.get_all()) == 2
    assert set(reg.get_ids()) == {"mozzart", "meridian"}


def test_base_scraper_is_abstract():
    with pytest.raises(TypeError):
        BaseScraper()  # type: ignore[abstract]
