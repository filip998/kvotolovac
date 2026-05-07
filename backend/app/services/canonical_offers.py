from __future__ import annotations

import json
import unicodedata

from ..models.schemas import (
    CanonicalMarket,
    CanonicalOffer,
    NormalizedOdds,
    NormalizedOutcomeOffer,
)


_CANONICAL_MARKET_TYPES = {
    "football_result": "result",
    "football_double_chance": "double_chance",
    "player_points_milestones": "player_points",
    "tennis_match_winner": "match_winner",
}


def canonical_market_type(market_type: str) -> str:
    normalized = " ".join(market_type.strip().split())
    return _CANONICAL_MARKET_TYPES.get(normalized, normalized)


def outcome_code_for_event_orientation(
    *,
    market_type: str,
    outcome_code: str,
    orientation: str | None,
) -> str:
    if orientation != "reversed":
        return outcome_code

    canonical_type = canonical_market_type(market_type)
    if canonical_type in {"result", "match_winner"}:
        return {
            "home": "away",
            "away": "home",
        }.get(outcome_code, outcome_code)
    if canonical_type == "double_chance":
        return {
            "home_or_draw": "draw_or_away",
            "draw_or_away": "home_or_draw",
            "home_or_away": "home_or_away",
        }.get(outcome_code, outcome_code)
    return outcome_code


def build_market_key(
    *,
    match_id: str,
    event_id: str | None,
    sport: str,
    market_type: str,
    subject_type: str,
    subject_key: str | None,
    subject_name: str | None,
    line: float | None,
    period: str | None,
    scope: str | None,
) -> str:
    event_identity = _clean_part(event_id) if event_id else f"match:{match_id}"
    subject_identity = _clean_part(subject_key) or _subject_key(subject_type, subject_name)
    payload = {
        "event": event_identity,
        "line": _normalize_line(line),
        "market_type": market_type,
        "period": _clean_part(period),
        "scope": _clean_part(scope),
        "sport": sport,
        "subject": subject_identity,
        "subject_type": subject_type,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def canonical_offers_from_normalized_odds(
    odds: NormalizedOdds,
    *,
    event_id: str | None = None,
    bookmaker_match_id: str | None = None,
    subject_key_override: str | None = None,
    subject_name_override: str | None = None,
    scraped_at: str | None = None,
    period: str | None = None,
    scope: str | None = None,
) -> list[CanonicalOffer]:
    effective_scraped_at = scraped_at if scraped_at is not None else odds.scraped_at
    market = canonical_market_from_normalized_odds(
        odds,
        event_id=event_id,
        bookmaker_match_id=bookmaker_match_id,
        subject_key_override=subject_key_override,
        subject_name_override=subject_name_override,
        period=period,
        scope=scope,
    )
    offers: list[CanonicalOffer] = []
    if odds.over_odds is not None:
        offers.append(
            _offer(
                market=market,
                bookmaker_id=odds.bookmaker_id,
                outcome_code="over",
                odds=odds.over_odds,
                source_url=odds.source_url,
                raw_label=None,
                scraped_at=effective_scraped_at,
            )
        )
    if odds.under_odds is not None:
        offers.append(
            _offer(
                market=market,
                bookmaker_id=odds.bookmaker_id,
                outcome_code="under",
                odds=odds.under_odds,
                source_url=odds.source_url,
                raw_label=None,
                scraped_at=effective_scraped_at,
            )
        )
    return offers


def canonical_market_from_normalized_odds(
    odds: NormalizedOdds,
    *,
    event_id: str | None = None,
    bookmaker_match_id: str | None = None,
    subject_key_override: str | None = None,
    subject_name_override: str | None = None,
    period: str | None = None,
    scope: str | None = None,
) -> CanonicalMarket:
    subject_type, subject_key, subject_name = _subject_from_normalized_odds(odds)
    if subject_key_override is not None:
        subject_key = subject_key_override
    if subject_name_override is not None:
        subject_name = subject_name_override
    market_type = canonical_market_type(odds.market_type)
    line = _normalize_line(odds.threshold)
    market_key = build_market_key(
        match_id=odds.match_id,
        event_id=event_id,
        sport=odds.sport,
        market_type=market_type,
        subject_type=subject_type,
        subject_key=subject_key,
        subject_name=subject_name,
        line=line,
        period=period,
        scope=scope,
    )
    return CanonicalMarket(
        market_key=market_key,
        match_id=odds.match_id,
        event_id=event_id,
        bookmaker_match_id=bookmaker_match_id,
        sport=odds.sport,
        market_type=market_type,
        source_market_type=odds.market_type,
        subject_type=subject_type,
        subject_key=subject_key,
        subject_name=subject_name,
        line=line,
        period=period,
        scope=scope,
    )


def canonical_offer_from_normalized_outcome_offer(
    offer: NormalizedOutcomeOffer,
    *,
    event_id: str | None = None,
    event_orientation: str | None = None,
    bookmaker_match_id: str | None = None,
    scraped_at: str | None = None,
    period: str | None = None,
    scope: str | None = None,
) -> CanonicalOffer:
    effective_scraped_at = (
        scraped_at if scraped_at is not None else offer.scraped_at
    )
    market = canonical_market_from_normalized_outcome_offer(
        offer,
        event_id=event_id,
        bookmaker_match_id=bookmaker_match_id,
        period=period,
        scope=scope,
    )
    return _offer(
        market=market,
        bookmaker_id=offer.bookmaker_id,
        outcome_code=outcome_code_for_event_orientation(
            market_type=offer.market_type,
            outcome_code=offer.outcome_code,
            orientation=event_orientation,
        ),
        odds=offer.odds,
        source_url=offer.source_url,
        raw_label=offer.raw_label,
        scraped_at=effective_scraped_at,
    )


def canonical_market_from_normalized_outcome_offer(
    offer: NormalizedOutcomeOffer,
    *,
    event_id: str | None = None,
    bookmaker_match_id: str | None = None,
    period: str | None = None,
    scope: str | None = None,
) -> CanonicalMarket:
    subject_type, subject_key, subject_name = _subject_from_outcome_offer(offer)
    market_type = canonical_market_type(offer.market_type)
    line = _normalize_line(offer.line)
    market_key = build_market_key(
        match_id=offer.match_id,
        event_id=event_id,
        sport=offer.sport,
        market_type=market_type,
        subject_type=subject_type,
        subject_key=subject_key,
        subject_name=subject_name,
        line=line,
        period=period,
        scope=scope,
    )
    return CanonicalMarket(
        market_key=market_key,
        match_id=offer.match_id,
        event_id=event_id,
        bookmaker_match_id=bookmaker_match_id,
        sport=offer.sport,
        market_type=market_type,
        source_market_type=offer.market_type,
        subject_type=subject_type,
        subject_key=subject_key,
        subject_name=subject_name,
        line=line,
        period=period,
        scope=scope,
    )


def _subject_from_normalized_odds(
    odds: NormalizedOdds,
) -> tuple[str, str | None, str | None]:
    if odds.player_name:
        subject_name = odds.player_name.strip()
        return "player", _subject_key("player", subject_name), subject_name
    return "event", None, None


def _subject_from_outcome_offer(
    offer: NormalizedOutcomeOffer,
) -> tuple[str, str | None, str | None]:
    return "event", None, None


def _offer(
    *,
    market: CanonicalMarket,
    bookmaker_id: str,
    outcome_code: str,
    odds: float,
    source_url: str | None,
    raw_label: str | None,
    scraped_at: str | None,
) -> CanonicalOffer:
    return CanonicalOffer(
        market_key=market.market_key,
        market=market,
        bookmaker_id=bookmaker_id,
        outcome_code=outcome_code,
        odds=odds,
        source_url=source_url,
        raw_label=raw_label,
        scraped_at=scraped_at,
    )


def _subject_key(subject_type: str, subject_name: str | None) -> str | None:
    normalized_name = _clean_part(subject_name)
    if not normalized_name:
        return None
    return f"{subject_type}:{normalized_name}"


def _clean_part(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = " ".join(normalized.split())
    return normalized or None


def _normalize_line(line: float | None) -> float | None:
    if line is None:
        return None
    normalized = float(line)
    if normalized == 0:
        return 0.0
    return normalized
