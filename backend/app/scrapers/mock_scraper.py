from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseScraper
from ..models.schemas import RawOddsData, RawOutcomeOffer

# ── Realistic Euroleague mock data ─────────────────────────
# Each bookmaker has slightly different player name spellings and thresholds
# to simulate real-world discrepancies.

_GAMES = [
    {
        "home": "Olympiacos",
        "away": "Real Madrid",
        "start": (datetime.utcnow() + timedelta(hours=3)).isoformat(),
    },
    {
        "home": "Fenerbahce",
        "away": "FC Barcelona",
        "start": (datetime.utcnow() + timedelta(hours=5)).isoformat(),
    },
    {
        "home": "Partizan",
        "away": "Crvena Zvezda",
        "start": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
    },
    {
        "home": "Panathinaikos",
        "away": "Anadolu Efes",
        "start": (datetime.utcnow() + timedelta(hours=26)).isoformat(),
    },
    {
        "home": "Bayern Munich",
        "away": "Maccabi Tel Aviv",
        "start": (datetime.utcnow() + timedelta(hours=48)).isoformat(),
    },
]

# Player markets per game — each bookmaker has INTENTIONALLY different thresholds
# to create detectable discrepancies.
_PLAYER_MARKETS: dict[str, list[dict]] = {
    # ── Mozzart ────────────────────────────────────────────
    "mozzart": [
        # Game 0: Olympiacos vs Real Madrid
        {"game": 0, "player": "Sasha Vezenkov", "threshold": 18.5, "over": 1.85, "under": 1.95},
        {"game": 0, "player": "Facundo Campazzo", "threshold": 12.5, "over": 1.90, "under": 1.90},
        {"game": 0, "player": "Kostas Sloukas", "threshold": 14.5, "over": 1.80, "under": 2.00},
        {"game": 0, "player": "Walter Tavares", "threshold": 10.5, "over": 1.75, "under": 2.05},
        # Game 1: Fenerbahce vs Barcelona
        {"game": 1, "player": "Nigel Hayes-Davis", "threshold": 15.5, "over": 1.85, "under": 1.95},
        {"game": 1, "player": "Nick Calathes", "threshold": 11.5, "over": 1.90, "under": 1.90},
        {"game": 1, "player": "Nikola Mirotic", "threshold": 19.5, "over": 1.80, "under": 2.00},
        # Game 2: Partizan vs Crvena Zvezda (Belgrade derby!)
        {"game": 2, "player": "Iffe Lundberg", "threshold": 16.5, "over": 1.85, "under": 1.95},
        {"game": 2, "player": "Nikola Jovic", "threshold": 13.5, "over": 1.90, "under": 1.90},
        {"game": 2, "player": "Filip Petrusev", "threshold": 14.5, "over": 1.75, "under": 2.05},
        # Game 3: Panathinaikos vs Efes
        {"game": 3, "player": "Mathias Lessort", "threshold": 13.5, "over": 1.85, "under": 1.95},
        {"game": 3, "player": "Jaron Blossomgame", "threshold": 12.5, "over": 1.80, "under": 2.00},
        # Game 4: Bayern vs Maccabi
        {"game": 4, "player": "Vladimir Lucic", "threshold": 14.5, "over": 1.90, "under": 1.90},
        {"game": 4, "player": "Saben Lee", "threshold": 11.5, "over": 1.85, "under": 1.95},
    ],
    # ── Meridian — DIFFERENT thresholds (creates gaps!) ────
    "meridian": [
        {"game": 0, "player": "S. Vezenkov", "threshold": 20.5, "over": 1.90, "under": 1.90},
        {"game": 0, "player": "F. Campazzo", "threshold": 14.5, "over": 1.85, "under": 1.95},
        {"game": 0, "player": "K. Sloukas", "threshold": 14.5, "over": 1.75, "under": 2.05},
        {"game": 0, "player": "W. Tavares", "threshold": 12.5, "over": 1.80, "under": 2.00},
        {"game": 1, "player": "N. Hayes-Davis", "threshold": 17.5, "over": 1.90, "under": 1.90},
        {"game": 1, "player": "Nikolas Calathes", "threshold": 13.5, "over": 1.85, "under": 1.95},
        {"game": 1, "player": "N. Mirotic", "threshold": 19.5, "over": 1.90, "under": 1.90},
        {"game": 2, "player": "I. Lundberg", "threshold": 18.5, "over": 1.80, "under": 2.00},
        {"game": 2, "player": "N. Jovic", "threshold": 15.5, "over": 1.85, "under": 1.95},
        {"game": 2, "player": "F. Petrusev", "threshold": 16.5, "over": 1.80, "under": 2.00},
        {"game": 3, "player": "M. Lessort", "threshold": 15.5, "over": 1.80, "under": 2.00},
        {"game": 3, "player": "J. Blossomgame", "threshold": 12.5, "over": 1.85, "under": 1.95},
        {"game": 4, "player": "V. Lucic", "threshold": 16.5, "over": 1.85, "under": 1.95},
        {"game": 4, "player": "S. Lee", "threshold": 13.5, "over": 1.80, "under": 2.00},
    ],
    # ── MaxBet — another set of thresholds ─────────────────
    "maxbet": [
        {"game": 0, "player": "Vezenkov S.", "threshold": 19.5, "over": 1.88, "under": 1.92},
        {"game": 0, "player": "Campazzo F.", "threshold": 13.5, "over": 1.87, "under": 1.93},
        {"game": 0, "player": "Sloukas K.", "threshold": 15.5, "over": 1.82, "under": 1.98},
        {"game": 0, "player": "Tavares W.", "threshold": 11.5, "over": 1.78, "under": 2.02},
        {"game": 1, "player": "Hayes-Davis N.", "threshold": 16.5, "over": 1.88, "under": 1.92},
        {"game": 1, "player": "Calathes N.", "threshold": 12.5, "over": 1.87, "under": 1.93},
        {"game": 1, "player": "Mirotic N.", "threshold": 20.5, "over": 1.82, "under": 1.98},
        {"game": 2, "player": "Lundberg I.", "threshold": 17.5, "over": 1.83, "under": 1.97},
        {"game": 2, "player": "Jovic N.", "threshold": 14.5, "over": 1.88, "under": 1.92},
        {"game": 2, "player": "Petrusev F.", "threshold": 15.5, "over": 1.78, "under": 2.02},
        {"game": 3, "player": "Lessort M.", "threshold": 14.5, "over": 1.83, "under": 1.97},
        {"game": 3, "player": "Blossomgame J.", "threshold": 13.5, "over": 1.82, "under": 1.98},
        {"game": 4, "player": "Lucic V.", "threshold": 15.5, "over": 1.87, "under": 1.93},
        {"game": 4, "player": "Lee S.", "threshold": 12.5, "over": 1.83, "under": 1.97},
    ],
}

_BOOKMAKER_META = {
    "mozzart": ("Mozzart", "https://www.mozzartbet.com"),
    "meridian": ("Meridian", "https://www.meridianbet.rs"),
    "maxbet": ("MaxBet", "https://www.maxbet.rs"),
    "balkanbet": ("BalkanBet", "https://www.balkanbet.rs"),
    "oktagonbet": ("OktagonBet", "https://www.oktagonbet.com"),
    "merkurxtip": ("MERKUR X TIP", "https://www.merkurxtip.rs"),
    "soccerbet": ("SoccerBet", "https://www.soccerbet.rs"),
    "superbet": ("Superbet", "https://superbet.rs"),
    "betole": ("BetOle", "https://www.betole.com"),
    "365": ("365", "https://www.365.rs/"),
    "admiralbet": ("AdmiralBet", "https://admiralbet.rs"),
    "pinnbet": ("PinnBet", "https://www.pinnbet.rs"),
    "volcanobet": ("VolcanoBet", "https://www.volcanobet.rs/sport-v2/prematch/events"),
}

_PLAYER_MARKETS["soccerbet"] = [
    {
        **market,
        "threshold": market["threshold"] + (0.5 if idx % 3 == 0 else 0.0),
        "over": round(max(1.01, market["over"] + (0.02 if idx % 2 == 0 else -0.01)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.02 if idx % 2 == 0 else 0.01)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]

_PLAYER_MARKETS["merkurxtip"] = [
    {
        **market,
        "threshold": market["threshold"] + (-0.5 if idx % 4 == 0 else 0.5),
        "over": round(max(1.01, market["over"] + (0.01 if idx % 2 == 0 else -0.02)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.01 if idx % 2 == 0 else 0.02)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]

_PLAYER_MARKETS["oktagonbet"] = [
    {
        **market,
        "threshold": market["threshold"] + (0.5 if idx % 4 in (0, 2) else 0.0),
        "over": round(max(1.01, market["over"] + (0.02 if idx % 2 == 0 else -0.02)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.02 if idx % 2 == 0 else 0.02)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]

_PLAYER_MARKETS["superbet"] = [
    {
        **market,
        "threshold": market["threshold"] + (-0.5 if idx % 4 == 0 else 0.5),
        "over": round(max(1.01, market["over"] + (0.03 if idx % 2 == 0 else -0.02)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.03 if idx % 2 == 0 else 0.02)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]

_PLAYER_MARKETS["betole"] = [
    {
        **market,
        "threshold": market["threshold"] + (0.5 if idx % 4 == 0 else 0.0),
        "over": round(max(1.01, market["over"] + (0.01 if idx % 2 == 0 else -0.02)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.01 if idx % 2 == 0 else 0.02)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]

_PLAYER_MARKETS["365"] = [
    {
        **market,
        "threshold": market["threshold"] + (0.5 if idx % 5 in (1, 4) else 0.0),
        "over": round(max(1.01, market["over"] + (0.02 if idx % 2 == 0 else -0.01)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.01 if idx % 2 == 0 else 0.02)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]

_PLAYER_MARKETS["volcanobet"] = [
    {
        **market,
        "threshold": market["threshold"] + (0.5 if idx % 4 == 0 else 0.0),
        "over": round(max(1.01, market["over"] + (0.01 if idx % 2 == 0 else -0.03)), 2),
        "under": round(
            max(1.01, market["under"] + (-0.01 if idx % 2 == 0 else 0.03)),
            2,
        ),
    }
    for idx, market in enumerate(_PLAYER_MARKETS["maxbet"])
]


# ── Asian handicap (with OT) — mock data ───────────────────
# Storage convention: ``threshold`` is the home team's expected margin —
# *positive* means home is favoured by ``threshold`` points; *negative* means
# home is the underdog by ``|threshold|``. ``over`` pays when home covers the
# spread (home_margin > threshold); ``under`` pays when the away team covers.
# We deliberately stagger lines and odds across bookmakers so the analyzer
# surfaces a same-line same-margin discrepancy on game 0 and a threshold-gap
# middle on game 1, exercising both code paths in mock mode.
_HANDICAP_MARKETS: dict[str, list[dict]] = {
    "mozzart": [
        # Game 0: Olympiacos (home) favoured by 4.5 → over (home covers) priced
        # higher than under because covering -4.5 is the harder outcome.
        {"game": 0, "threshold": 4.5, "over": 1.95, "under": 1.85},
        # Game 1: Fenerbahce favoured by 5.5 (lower line)
        {"game": 1, "threshold": 5.5, "over": 1.85, "under": 1.95},
        # Game 2: Partizan slight underdog (home gets +1.5)
        {"game": 2, "threshold": -1.5, "over": 1.90, "under": 1.90},
    ],
    "meridian": [
        # Game 0: same line as mozzart, different odds → same-line cross-book
        # arb candidate (1/1.85 + 1/1.95 < 1).
        {"game": 0, "threshold": 4.5, "over": 1.85, "under": 1.95},
        # Game 1: wider line 7.5 → 5.5↔7.5 gap with mozzart (middle window
        # when home wins by 6 or 7).
        {"game": 1, "threshold": 7.5, "over": 1.85, "under": 1.95},
        # Game 2: same direction as mozzart
        {"game": 2, "threshold": -1.5, "over": 1.92, "under": 1.88},
    ],
    "maxbet": [
        # Game 0: tighter line, middle-friendly odds
        {"game": 0, "threshold": 3.5, "over": 1.92, "under": 1.88},
        # Game 1: line between mozzart and meridian
        {"game": 1, "threshold": 6.5, "over": 1.88, "under": 1.92},
        # Game 2: same line as the other two
        {"game": 2, "threshold": -1.5, "over": 1.88, "under": 1.92},
    ],
}

_FOOTBALL_GAMES = [
    {
        "league": "uae_2",
        "home": "Hatta SC",
        "away": "Al Urooba UAE",
        "start": (datetime.utcnow() + timedelta(hours=3)).isoformat(),
    },
    {
        "league": "egypt_1_relegation_group",
        "home": "El Gouna FC",
        "away": "Haras El Hodood",
        "start": (datetime.utcnow() + timedelta(hours=4)).isoformat(),
    },
]

_FOOTBALL_OUTCOME_MARKETS: dict[str, list[dict]] = {
    "mozzart": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.48, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.18, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.62, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.41, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.29, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.47, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 1.80, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.90, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_result", "outcome": "home", "odds": 1.98, "label": "1"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.76, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.18, "line": 2.5, "label": "3+"},
    ],
    "volcanobet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.46, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.16, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.64, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.42, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.28, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.46, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 1.88, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.92, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_result", "outcome": "home", "odds": 2.02, "label": "1"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.82, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.12, "line": 2.5, "label": "3+"},
    ],
    "maxbet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.50, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.15, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.55, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.42, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.42, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.12, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.73, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_result", "outcome": "home", "odds": 2.30, "label": "1"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 2.15, "line": 2.5, "label": "0-2"},
    ],
    "balkanbet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.40, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.20, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.55, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.37, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.24, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.42, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 1.78, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.82, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.53, "label": "X2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.85, "line": 2.5, "label": "3+"},
    ],
    "soccerbet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.45, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.10, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.70, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.40, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.28, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.48, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.05, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.85, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.74, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.20, "line": 2.5, "label": "3+"},
    ],
    "merkurxtip": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.32, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.25, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.68, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.36, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.27, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.50, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.00, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.88, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.80, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.10, "line": 2.5, "label": "3+"},
    ],
    "oktagonbet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.36, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.18, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.62, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.38, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.25, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.47, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.03, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.84, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.76, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.18, "line": 2.5, "label": "3+"},
    ],
    "betole": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.42, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.22, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.58, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.39, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.26, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.45, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.06, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.83, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.51, "label": "X2"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.78, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.16, "line": 2.5, "label": "3+"},
    ],
    "365": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.38, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.20, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.60, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.40, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.27, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.46, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.04, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.85, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.49, "label": "X2"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.74, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.20, "line": 2.5, "label": "3+"},
    ],
    "superbet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.40, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.25, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.55, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.39, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.26, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.45, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.05, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.84, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_result", "outcome": "home", "odds": 1.92, "label": "1"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.78, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.16, "line": 2.5, "label": "3+"},
    ],
    "admiralbet": [
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.42, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.20, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.60, "label": "2"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_draw", "odds": 1.40, "label": "1X"},
        {"game": 0, "market": "football_double_chance", "outcome": "home_or_away", "odds": 1.27, "label": "12"},
        {"game": 0, "market": "football_double_chance", "outcome": "draw_or_away", "odds": 1.46, "label": "X2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.10, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.80, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_result", "outcome": "home", "odds": 1.94, "label": "1"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.80, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.12, "line": 2.5, "label": "3+"},
    ],
    "pinnbet": [
        # Default partial-mode shape: result + 2.5 totals on game 0 only, no double chance.
        {"game": 0, "market": "football_result", "outcome": "home", "odds": 2.45, "label": "1"},
        {"game": 0, "market": "football_result", "outcome": "draw", "odds": 3.18, "label": "X"},
        {"game": 0, "market": "football_result", "outcome": "away", "odds": 2.62, "label": "2"},
        {"game": 0, "market": "football_total_goals", "outcome": "under", "odds": 2.08, "line": 2.5, "label": "0-2"},
        {"game": 0, "market": "football_total_goals", "outcome": "over", "odds": 1.82, "line": 2.5, "label": "3+"},
        {"game": 1, "market": "football_result", "outcome": "home", "odds": 1.96, "label": "1"},
        {"game": 1, "market": "football_total_goals", "outcome": "under", "odds": 1.82, "line": 2.5, "label": "0-2"},
        {"game": 1, "market": "football_total_goals", "outcome": "over", "odds": 2.14, "line": 2.5, "label": "3+"},
    ],
}


class MockScraper(BaseScraper):
    """Mock scraper returning realistic Euroleague basketball data."""

    def __init__(self, bookmaker_id: str) -> None:
        if bookmaker_id not in _BOOKMAKER_META:
            raise ValueError(f"Unknown mock bookmaker: {bookmaker_id}")
        self._bookmaker_id = bookmaker_id

    def get_bookmaker_id(self) -> str:
        return self._bookmaker_id

    def get_bookmaker_name(self) -> str:
        return _BOOKMAKER_META[self._bookmaker_id][0]

    def get_supported_leagues(self) -> list[str]:
        return ["euroleague"]

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football"] if self._bookmaker_id in _FOOTBALL_OUTCOME_MARKETS else []

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "euroleague":
            return []

        markets = _PLAYER_MARKETS.get(self._bookmaker_id, [])
        results: list[RawOddsData] = []
        for m in markets:
            game = _GAMES[m["game"]]
            results.append(
                RawOddsData(
                    bookmaker_id=self._bookmaker_id,
                    league_id=league_id,
                    sport="basketball",
                    home_team=game["home"],
                    away_team=game["away"],
                    market_type="player_points",
                    player_name=m["player"],
                    threshold=m["threshold"],
                    over_odds=m["over"],
                    under_odds=m["under"],
                    start_time=game["start"],
                )
            )
        for h in _HANDICAP_MARKETS.get(self._bookmaker_id, []):
            game = _GAMES[h["game"]]
            results.append(
                RawOddsData(
                    bookmaker_id=self._bookmaker_id,
                    league_id=league_id,
                    sport="basketball",
                    home_team=game["home"],
                    away_team=game["away"],
                    market_type="home_handicap_ot",
                    player_name=None,
                    threshold=h["threshold"],
                    over_odds=h["over"],
                    under_odds=h["under"],
                    start_time=game["start"],
                )
            )
        return results

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        if sport != "football":
            return []

        markets = _FOOTBALL_OUTCOME_MARKETS.get(self._bookmaker_id, [])
        results: list[RawOutcomeOffer] = []
        for market in markets:
            game = _FOOTBALL_GAMES[market["game"]]
            results.append(
                RawOutcomeOffer(
                    bookmaker_id=self._bookmaker_id,
                    league_id=game["league"],
                    sport="football",
                    home_team=game["home"],
                    away_team=game["away"],
                    market_type=market["market"],
                    outcome_code=market["outcome"],
                    odds=market["odds"],
                    line=market.get("line"),
                    raw_label=market["label"],
                    start_time=game["start"],
                )
            )
        return results
