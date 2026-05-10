"""Discovery / A-B testing tool for Telegram notification formatting.

This script is a developer tool, NOT part of the production notification path.
It loads real opportunities from the local kvotolovac.db, renders each fixture
under three candidate format variants, and sends every variant (plus the
current production format as a "before") to a target Telegram chat so a human
can pick the winning design on their phone.

Usage (from the backend/ directory):

    ./venv/bin/python scripts/telegram_format_demo.py --chat-id 5164864439

The DB path defaults to backend/kvotolovac.db. The Telegram bot token is read
from the same .env / config the FastAPI app uses.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Make ``app.*`` importable when running this script directly from backend/.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import aiosqlite  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.schemas import OpportunityLeg  # noqa: E402
from app.services.notifications import (  # noqa: E402
    TelegramBotClient,
    TelegramOpportunityDisplayContext,
    _TelegramDeliveryItem,
    format_telegram_opportunity_group,
)
from app.services.opportunity_analyzer import Opportunity  # noqa: E402

BELGRADE = ZoneInfo("Europe/Belgrade")
DEFAULT_CHAT_ID = "5164864439"  # FilipTanic
DEFAULT_DB_PATH = BACKEND_DIR / "kvotolovac.db"
SEND_INTERVAL_SECONDS = 1.5  # Telegram bot rate-limit guard (1 msg/sec per chat).


# ── Loader ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    opportunities: list[Opportunity]
    context: TelegramOpportunityDisplayContext


async def _fetch_opportunities_by_filter(
    db_path: Path,
    *,
    match_id: str,
    market_type: str,
    line: float | None,
    opportunity_type: str | None = None,
    subject_name: str | None = None,
    limit: int = 12,
) -> tuple[list[Opportunity], TelegramOpportunityDisplayContext]:
    """Load opportunities + a display context from the dev DB."""

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        clauses = ["match_id = ?", "market_type = ?"]
        params: list[object] = [match_id, market_type]
        if line is None:
            clauses.append("line IS NULL")
        else:
            clauses.append("line = ?")
            params.append(line)
        if opportunity_type:
            clauses.append("opportunity_type = ?")
            params.append(opportunity_type)
        if subject_name:
            clauses.append("subject_name = ?")
            params.append(subject_name)
        query = (
            "SELECT * FROM opportunities WHERE "
            + " AND ".join(clauses)
            + " ORDER BY COALESCE(profit_margin, middle_ev, -999) DESC LIMIT ?"
        )
        params.append(limit)
        async with db.execute(query, params) as cursor:
            opp_rows = await cursor.fetchall()

        # Resolved event (preferred) or match fallback for display context.
        ctx_row = None
        async with db.execute(
            "SELECT id, display_home_team, display_away_team, display_league_name, start_time"
            " FROM resolved_events WHERE primary_match_id = ? LIMIT 1",
            (match_id,),
        ) as cursor:
            ctx_row = await cursor.fetchone()
        if ctx_row is None:
            async with db.execute(
                "SELECT m.id AS id, m.home_team AS display_home_team, "
                "m.away_team AS display_away_team, l.name AS display_league_name, "
                "m.start_time AS start_time "
                "FROM matches m LEFT JOIN leagues l ON l.id = m.league_id "
                "WHERE m.id = ?",
                (match_id,),
            ) as cursor:
                ctx_row = await cursor.fetchone()

    opportunities = [_row_to_opportunity(row) for row in opp_rows]
    fallback_label = (ctx_row["id"] if ctx_row else match_id) or match_id
    context = TelegramOpportunityDisplayContext(
        home_team=ctx_row["display_home_team"] if ctx_row else None,
        away_team=ctx_row["display_away_team"] if ctx_row else None,
        league_name=ctx_row["display_league_name"] if ctx_row else None,
        start_time=ctx_row["start_time"] if ctx_row else None,
        fallback_label=fallback_label,
    )
    return opportunities, context


def _row_to_opportunity(row) -> Opportunity:
    legs_payload = json.loads(row["legs"]) if row["legs"] else []
    legs = [OpportunityLeg(**leg) for leg in legs_payload]
    market_keys_raw = row["market_keys"] if "market_keys" in row.keys() else "[]"
    market_keys = tuple(json.loads(market_keys_raw)) if market_keys_raw else ()
    return Opportunity(
        sport=row["sport"],
        match_id=row["match_id"],
        resolved_event_id=row["resolved_event_id"] if "resolved_event_id" in row.keys() else None,
        opportunity_type=row["opportunity_type"],
        market_type=row["market_type"],
        line=row["line"],
        profit_margin=row["profit_margin"],
        middle_profit_margin=row["middle_profit_margin"],
        legs=legs,
        subject_type=row["subject_type"],
        subject_key=row["subject_key"],
        subject_name=row["subject_name"],
        market_keys=market_keys,
        middle_hit_probability=row["middle_hit_probability"]
        if "middle_hit_probability" in row.keys()
        else None,
        middle_ev=row["middle_ev"] if "middle_ev" in row.keys() else None,
        middle_ev_rank=row["middle_ev_rank"] if "middle_ev_rank" in row.keys() else None,
    )


# ── Tier classifier (shared across variants) ──────────────────────────────


_TIER_LABELS = ["calm", "good", "hot", "premium"]
_ARB_THRESHOLDS = (0.02, 0.04, 0.07)
_MIDDLE_THRESHOLDS = (0.05, 0.10, 0.20)


def _tier_index(opp: Opportunity) -> int:
    if opp.opportunity_type == "middle":
        value = opp.middle_ev
        thresholds = _MIDDLE_THRESHOLDS
    else:
        value = opp.profit_margin
        thresholds = _ARB_THRESHOLDS
    if value is None:
        return 0
    for idx, t in enumerate(thresholds):
        if value < t:
            return idx
    return 3


def _tier_emoji(tier: int) -> str:
    return ["🟢", "🟡⚡", "🔥", "🚨🔥"][tier]


def _percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2f}%"


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _format_belgrade(start_time: str | None) -> str | None:
    if not start_time:
        return None
    s = start_time.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BELGRADE)
    local = dt.astimezone(BELGRADE)
    return local.strftime("%a %d %b %H:%M")


def _arb_stake_split(o1: float, o2: float) -> tuple[float, float] | None:
    if o1 <= 0 or o2 <= 0:
        return None
    p1 = 1.0 / o1
    p2 = 1.0 / o2
    total = p1 + p2
    if total <= 0:
        return None
    return p1 / total, p2 / total


def _format_signed_line(value: float) -> str:
    if value > 0:
        return f"+{_format_number(value)}"
    return _format_number(value)


def _outcome_label(code: str, line: float | None, *, market_type: str) -> str:
    code_lower = (code or "").lower()
    market_lower = (market_type or "").lower()
    is_handicap = "handicap" in market_lower
    if is_handicap:
        if code_lower in {"over", "home", "1"}:
            return (
                f"H1 {_format_signed_line(line)}"
                if line is not None
                else "H1"
            )
        if code_lower in {"under", "away", "2"}:
            flipped = -line if line is not None else None
            return f"H2 {_format_signed_line(flipped)}" if flipped is not None else "H2"
    if market_lower == "match_winner":
        if code_lower in {"home", "1"}:
            return "1"
        if code_lower in {"away", "2"}:
            return "2"
        if code_lower in {"draw", "x"}:
            return "X"
    if code_lower in {"over", "o"}:
        base = "O"
    elif code_lower in {"under", "u"}:
        base = "U"
    elif code_lower in {"home", "1"}:
        base = "1"
    elif code_lower in {"away", "2"}:
        base = "2"
    elif code_lower in {"draw", "x"}:
        base = "X"
    else:
        base = (code or "").replace("_", " ").title() or "?"
    if line is None:
        return base
    return f"{base}{_format_number(line)}"


def _market_label(market_type: str) -> str:
    base = market_type
    if base.startswith("player_"):
        base = base[len("player_") :]
    return base.replace("_", " ").title()


def _alert_subject(opp: Opportunity) -> str | None:
    """Return a subject label only when it adds info beyond the market label."""

    raw = (opp.subject_name or opp.subject_key or "").strip()
    if not raw:
        return None
    if raw.lower() == _market_label(opp.market_type).lower():
        return None
    return raw


def _legs_grouped_by_outcome(
    opportunities: list[Opportunity],
) -> tuple[
    tuple[str, OpportunityLeg, OpportunityLeg, tuple[float, float] | None],
    list[tuple[str, str, float]],
]:
    """For arb collapse: pick the strongest pair, list the remaining option.

    Returns (best_pair, secondary_options) where:
      best_pair = (header_text, leg_a, leg_b, stake_split_or_None)
      secondary_options = [(side_label, bookmaker, odds), ...] for the
                          weaker pairs that share one of the two sides.
    """

    if not opportunities:
        raise ValueError("expected at least one opportunity")
    primary = opportunities[0]
    leg_a, leg_b = primary.legs[:2]
    stake = (
        _arb_stake_split(leg_a.odds, leg_b.odds)
        if primary.opportunity_type != "middle"
        else None
    )
    primary_books = {leg_a.bookmaker_id, leg_b.bookmaker_id}
    primary_outcomes = {
        leg_a.outcome_code: leg_a.bookmaker_id,
        leg_b.outcome_code: leg_b.bookmaker_id,
    }
    secondaries: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = {(leg_a.outcome_code, leg_a.bookmaker_id), (leg_b.outcome_code, leg_b.bookmaker_id)}
    for opp in opportunities[1:]:
        for leg in opp.legs[:2]:
            key = (leg.outcome_code, leg.bookmaker_id)
            if key in seen:
                continue
            if leg.bookmaker_id in primary_books and leg.outcome_code in primary_outcomes:
                # Same book offering same outcome again: ignore duplicate.
                continue
            secondaries.append(
                (
                    _outcome_label(leg.outcome_code, leg.line, market_type=primary.market_type),
                    leg.bookmaker_id,
                    leg.odds,
                )
            )
            seen.add(key)
    header = ""  # variants supply their own header
    return (header, leg_a, leg_b, stake), secondaries


# ── BEFORE: current production format ──────────────────────────────────────


def render_before(opportunities: list[Opportunity], context: TelegramOpportunityDisplayContext) -> str:
    items = [
        _TelegramDeliveryItem(fingerprint="", opportunity=opp, context=context)
        for opp in opportunities
    ]
    return format_telegram_opportunity_group(items)


# ── VARIANT A: Compact two-liner ───────────────────────────────────────────


def render_variant_a(
    opportunities: list[Opportunity],
    context: TelegramOpportunityDisplayContext,
) -> str:
    primary = opportunities[0]
    tier = _tier_index(primary)
    emoji = _tier_emoji(tier)
    is_middle = primary.opportunity_type == "middle"
    metric_value = _percent(primary.middle_ev if is_middle else primary.profit_margin)
    metric_label = "EV" if is_middle else "ROI"
    kind = "MIDDLE" if is_middle else "ARB"
    if tier == 3:
        header = f"{emoji} <b>PREMIUM {kind} · {metric_label} {metric_value} {emoji}</b>"
    elif tier == 2:
        header = f"{emoji} <b>{kind} · {metric_label} {metric_value}</b>"
    elif tier == 1:
        header = f"{emoji} <b>{metric_label} {metric_value} · {kind.lower()}</b>"
    else:
        header = f"{emoji} <b>{metric_label} {metric_value}</b> · {kind.lower()}"

    matchup_parts: list[str] = []
    if context.home_team and context.away_team:
        teams = f"{html.escape(context.home_team)} vs {html.escape(context.away_team)}"
        if tier == 3:
            teams = teams.upper()
        matchup_parts.append(f"<b>{teams}</b>")
    if context.league_name:
        matchup_parts.append(f"🏆 {html.escape(context.league_name)}")
    when = _format_belgrade(context.start_time)
    if when:
        matchup_parts.append(f"🕑 {html.escape(when)}")

    market_label = _market_label(primary.market_type)
    subject = _alert_subject(primary)
    market_line_parts = [market_label]
    if subject:
        market_line_parts.insert(0, html.escape(subject))
    if primary.line is not None:
        market_line_parts.append(_format_number(primary.line))
    market_line = " · ".join(market_line_parts)

    (_, leg_a, leg_b, stake), secondaries = _legs_grouped_by_outcome(opportunities)
    a_label = _outcome_label(leg_a.outcome_code, leg_a.line, market_type=primary.market_type)
    b_label = _outcome_label(leg_b.outcome_code, leg_b.line, market_type=primary.market_type)
    if stake:
        a_part = f"<b>{html.escape(leg_a.bookmaker_id)}</b> {a_label} @ {_format_number(leg_a.odds)} ({stake[0] * 100:.1f}%)"
        b_part = f"<b>{html.escape(leg_b.bookmaker_id)}</b> {b_label} @ {_format_number(leg_b.odds)} ({stake[1] * 100:.1f}%)"
    else:
        a_part = f"<b>{html.escape(leg_a.bookmaker_id)}</b> {a_label} @ {_format_number(leg_a.odds)}"
        b_part = f"<b>{html.escape(leg_b.bookmaker_id)}</b> {b_label} @ {_format_number(leg_b.odds)}"
    pair_line = f"  {a_part}  ↔  {b_part}"

    lines = [header]
    if matchup_parts:
        lines.append(" · ".join(matchup_parts))
    lines.append(market_line)
    lines.append(pair_line)
    if secondaries:
        grouped: dict[str, list[tuple[str, float]]] = {}
        for side_label, book, odds in secondaries:
            grouped.setdefault(side_label, []).append((book, odds))
        for side_label, items in grouped.items():
            books = ", ".join(f"{html.escape(b)} {_format_number(o)}" for b, o in items)
            lines.append(f"  + {len(items)} more {side_label} books: {books}")
    return "\n".join(lines)


# ── VARIANT B: Blockquote card ─────────────────────────────────────────────


def render_variant_b(
    opportunities: list[Opportunity],
    context: TelegramOpportunityDisplayContext,
) -> str:
    primary = opportunities[0]
    tier = _tier_index(primary)
    emoji = _tier_emoji(tier)
    is_middle = primary.opportunity_type == "middle"
    metric_value = _percent(primary.middle_ev if is_middle else primary.profit_margin)
    metric_label = "EV" if is_middle else "ROI"
    kind = "middle" if is_middle else "arb"
    if tier == 3:
        header = f"{emoji} <b>{metric_label} {metric_value} · {kind.upper()}</b> {emoji}"
    elif tier == 2:
        header = f"{emoji} <b>{metric_label} {metric_value}</b> · {kind}"
    else:
        header = f"{emoji} <b>{metric_label} {metric_value}</b> · {kind}"

    body_lines: list[str] = []
    if context.home_team and context.away_team:
        teams = f"{html.escape(context.home_team)} vs {html.escape(context.away_team)}"
        body_lines.append(f"<b>{teams}</b>")
    meta: list[str] = []
    if context.league_name:
        meta.append(f"🏆 {html.escape(context.league_name)}")
    when = _format_belgrade(context.start_time)
    if when:
        meta.append(f"🕑 {html.escape(when)}")
    if meta:
        body_lines.append(" · ".join(meta))

    market_label = _market_label(primary.market_type)
    subject = _alert_subject(primary)
    market_line_parts = [market_label]
    if subject:
        market_line_parts.insert(0, html.escape(subject))
    if primary.line is not None:
        market_line_parts.append(_format_number(primary.line))
    body_lines.append("📊 " + " · ".join(market_line_parts))
    body_lines.append("")

    (_, leg_a, leg_b, stake), secondaries = _legs_grouped_by_outcome(opportunities)
    a_label = _outcome_label(leg_a.outcome_code, leg_a.line, market_type=primary.market_type)
    b_label = _outcome_label(leg_b.outcome_code, leg_b.line, market_type=primary.market_type)
    if stake:
        body_lines.append(
            f"<b>{html.escape(leg_a.bookmaker_id)}</b> {a_label} @ {_format_number(leg_a.odds)}   "
            f"stake <b>{stake[0] * 100:.1f}%</b>"
        )
        body_lines.append(
            f"<b>{html.escape(leg_b.bookmaker_id)}</b> {b_label} @ {_format_number(leg_b.odds)}   "
            f"stake <b>{stake[1] * 100:.1f}%</b>"
        )
    else:
        body_lines.append(
            f"<b>{html.escape(leg_a.bookmaker_id)}</b> {a_label} @ {_format_number(leg_a.odds)}"
        )
        body_lines.append(
            f"<b>{html.escape(leg_b.bookmaker_id)}</b> {b_label} @ {_format_number(leg_b.odds)}"
        )
    if secondaries:
        grouped: dict[str, list[tuple[str, float]]] = {}
        for side_label, book, odds in secondaries:
            grouped.setdefault(side_label, []).append((book, odds))
        for side_label, items in grouped.items():
            books = ", ".join(f"{html.escape(b)} {_format_number(o)}" for b, o in items)
            body_lines.append(f"+ {len(items)} more {side_label}: {books}")

    body = "\n".join(body_lines)
    return f"{header}\n<blockquote>{body}</blockquote>"


# ── VARIANT C: Monospace table ─────────────────────────────────────────────


def render_variant_c(
    opportunities: list[Opportunity],
    context: TelegramOpportunityDisplayContext,
) -> str:
    primary = opportunities[0]
    tier = _tier_index(primary)
    emoji = _tier_emoji(tier)
    is_middle = primary.opportunity_type == "middle"
    metric_value = _percent(primary.middle_ev if is_middle else primary.profit_margin)
    metric_label = "EV" if is_middle else "ROI"
    kind_word = "ARBITRAGE" if not is_middle else "MIDDLE"
    if tier == 3:
        header = f"{emoji} <b>PREMIUM {kind_word} · {metric_label} {metric_value}</b> {emoji}"
    elif tier == 2:
        header = f"{emoji} <b>{kind_word} · {metric_label} {metric_value}</b>"
    elif tier == 1:
        header = f"{emoji} <b>{kind_word.lower()} · {metric_label} {metric_value}</b>"
    else:
        header = f"{emoji} <b>{kind_word.lower()} · {metric_label} {metric_value}</b>"

    pre_lines: list[str] = []
    title_left = ""
    if context.home_team and context.away_team:
        title_left = f"{context.home_team} vs {context.away_team}"
    title_right = context.league_name or ""
    if title_left or title_right:
        # Two-column header: matchup left, league right (best-effort align).
        width = 38
        gap = max(width - len(title_left) - len(title_right), 2)
        pre_lines.append(title_left + " " * gap + title_right)
    when = _format_belgrade(context.start_time)
    market_label = _market_label(primary.market_type)
    subject = _alert_subject(primary)
    bits: list[str] = []
    if when:
        bits.append(when)
    if subject:
        bits.append(subject)
    if primary.line is not None:
        bits.append(f"{market_label} {_format_number(primary.line)}")
    else:
        bits.append(market_label)
    pre_lines.append(" · ".join(bits))
    pre_lines.append("")

    (_, leg_a, leg_b, stake), secondaries = _legs_grouped_by_outcome(opportunities)
    rows = []
    rows.append(("side", "bookmaker", "odds", "stake"))
    rows.append(("────", "──────────────", "──────", "──────"))
    a_label = _outcome_label(leg_a.outcome_code, leg_a.line, market_type=primary.market_type)
    b_label = _outcome_label(leg_b.outcome_code, leg_b.line, market_type=primary.market_type)
    rows.append(
        (
            a_label,
            leg_a.bookmaker_id,
            _format_number(leg_a.odds),
            f"{stake[0] * 100:.1f}%" if stake else "──",
        )
    )
    rows.append(
        (
            b_label,
            leg_b.bookmaker_id,
            _format_number(leg_b.odds),
            f"{stake[1] * 100:.1f}%" if stake else "──",
        )
    )
    for side_label, book, odds in secondaries:
        rows.append((side_label, book, _format_number(odds), "──"))

    col_widths = [max(len(str(r[i])) for r in rows) for i in range(4)]
    for row in rows:
        pre_lines.append(
            "  ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
        )

    pre = html.escape("\n".join(pre_lines))
    return f"{header}\n<pre>{pre}</pre>"


# ── Driver ─────────────────────────────────────────────────────────────────


async def _build_fixtures(db_path: Path) -> list[Fixture]:
    fixtures: list[Fixture] = []

    # Fixture 1: Real Premium tier — Flau'jae Johnson rebounds (8.88% ROI, 7 books).
    flaujae_opps, flaujae_ctx = await _fetch_opportunities_by_filter(
        db_path,
        match_id="4133eec2b93d",
        market_type="player_rebounds",
        line=4.5,
        opportunity_type="same_line_arbitrage",
        subject_name="Flau’jae Johnson",
        limit=8,
    )
    if flaujae_opps:
        fixtures.append(
            Fixture(
                name="player-rebounds-premium",
                description="Player rebounds arb · ~8.88% ROI Premium · 7 bookmakers",
                opportunities=flaujae_opps,
                context=flaujae_ctx,
            )
        )

    # Fixture 2: Player points — DeWanna Bonner (Hot tier 4.5%, 6 books).
    points_opps, points_ctx = await _fetch_opportunities_by_filter(
        db_path,
        match_id="7a83b697fcc9",
        market_type="player_points",
        line=14.5,
        opportunity_type="same_line_arbitrage",
        subject_name="DeWanna Bonner",
        limit=8,
    )
    if points_opps:
        fixtures.append(
            Fixture(
                name="player-points-hot",
                description="Player points arb · ~4.53% ROI Hot · 6 bookmakers",
                opportunities=points_opps,
                context=points_ctx,
            )
        )

    # Fixture 3: Handicap middle (demo H1/H2 outcome labels with sign flip).
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM opportunities WHERE match_id=? AND market_type=? "
            "AND opportunity_type='middle' AND line=? "
            "ORDER BY middle_ev DESC LIMIT 6",
            ("55630a01b461", "home_handicap_ot", -6.5),
        ) as cursor:
            handicap_rows = await cursor.fetchall()
        async with db.execute(
            "SELECT id, display_home_team, display_away_team, "
            "display_league_name, start_time FROM resolved_events "
            "WHERE primary_match_id = ?",
            ("55630a01b461",),
        ) as cursor:
            handicap_ctx_row = await cursor.fetchone()
    if handicap_rows:
        handicap_opps = [_row_to_opportunity(row) for row in handicap_rows]
        handicap_ctx = (
            TelegramOpportunityDisplayContext(
                home_team=handicap_ctx_row["display_home_team"],
                away_team=handicap_ctx_row["display_away_team"],
                league_name=handicap_ctx_row["display_league_name"],
                start_time=handicap_ctx_row["start_time"],
                fallback_label=handicap_ctx_row["id"] or "55630a01b461",
            )
            if handicap_ctx_row is not None
            else TelegramOpportunityDisplayContext(fallback_label="55630a01b461")
        )
        fixtures.append(
            Fixture(
                name="handicap-middle",
                description="Handicap middle · ~7.33% EV Hot · demonstrates H1/H2 outcome labels",
                opportunities=handicap_opps,
                context=handicap_ctx,
            )
        )

    # Fixture 4: Tennis match winner (1/2 outcome labels, Calm tier).
    tennis_opps, tennis_ctx = await _fetch_opportunities_by_filter(
        db_path,
        match_id="a60e4fc6d470",
        market_type="match_winner",
        line=None,
        opportunity_type="same_line_arbitrage",
        limit=6,
    )
    if tennis_opps:
        fixtures.append(
            Fixture(
                name="tennis-match-winner",
                description="Tennis match winner · ~1.93% ROI Calm · demonstrates 1/2 outcome labels",
                opportunities=tennis_opps,
                context=tennis_ctx,
            )
        )

    return fixtures


async def _send(client: TelegramBotClient, chat_id: str, text: str) -> None:
    await client.send_message(chat_id=chat_id, text=text)
    await asyncio.sleep(SEND_INTERVAL_SECONDS)


async def main(chat_id: str, db_path: Path, *, dry_run: bool = False) -> int:
    fixtures = await _build_fixtures(db_path)
    if not fixtures:
        print("No fixtures could be loaded — is the dev DB present?", file=sys.stderr)
        return 1

    client = TelegramBotClient(
        token=settings.telegram_bot_token,
        api_base_url=settings.telegram_api_base_url,
    )
    if not client.token_configured and not dry_run:
        print("TELEGRAM_BOT_TOKEN is not configured.", file=sys.stderr)
        return 2

    intro = (
        "🧪 <b>Telegram format A/B test</b>\n"
        f"Sent at {datetime.now(BELGRADE).strftime('%a %d %b %H:%M %Z')}\n"
        f"Fixtures: {', '.join(f.name for f in fixtures)}\n"
        "Each fixture below is followed by: <b>BEFORE</b> (current prod) → "
        "<b>VARIANT A</b> (compact) → <b>VARIANT B</b> (blockquote) → "
        "<b>VARIANT C</b> (monospace)."
    )
    if dry_run:
        print("=== INTRO ===")
        print(intro)
    else:
        await _send(client, chat_id, intro)

    for fixture in fixtures:
        header = (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Fixture:</b> <code>{html.escape(fixture.name)}</code>\n"
            f"{html.escape(fixture.description)}"
        )
        before = "🅱️ <b>BEFORE — current production</b>\n\n" + render_before(
            fixture.opportunities[:1], fixture.context
        )
        var_a = "🅰️ <b>VARIANT A — compact two-liner</b>\n\n" + render_variant_a(
            fixture.opportunities, fixture.context
        )
        var_b = "🅱️ <b>VARIANT B — blockquote card</b>\n\n" + render_variant_b(
            fixture.opportunities, fixture.context
        )
        var_c = "🅲 <b>VARIANT C — monospace table</b>\n\n" + render_variant_c(
            fixture.opportunities, fixture.context
        )

        if dry_run:
            for label, body in (
                (f"--- {fixture.name} HEADER ---", header),
                ("--- BEFORE ---", before),
                ("--- VARIANT A ---", var_a),
                ("--- VARIANT B ---", var_b),
                ("--- VARIANT C ---", var_c),
            ):
                print(f"\n{label}\n{body}")
        else:
            await _send(client, chat_id, header)
            await _send(client, chat_id, before)
            await _send(client, chat_id, var_a)
            await _send(client, chat_id, var_b)
            await _send(client, chat_id, var_c)

    if not dry_run:
        await _send(
            client,
            chat_id,
            "✅ End of A/B test. Reply with the variant you want (e.g. \"go with B but use C's stake column\").",
        )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID, help="Telegram chat ID")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="Path to kvotolovac.db"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rendered HTML to stdout instead of sending to Telegram",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    rc = asyncio.run(main(args.chat_id, Path(args.db), dry_run=args.dry_run))
    sys.exit(rc)
