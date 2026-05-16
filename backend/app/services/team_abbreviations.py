"""Period-abbreviation expansion for team names.

Many bookmakers shorten common club-naming prefixes with periods or compact
forms: ``Hap.Haifa``, ``Cl.America``, ``Atl.Mineiro``, ``Dep.Saprissa``,
``B.Leverkusen``, ``R.Cartagena``. The fuzzy matcher's tokenizer
(``normalize_identity_text``) strips ``.`` to whitespace, leaving 1–4 letter
fragments that don't share any token with the full canonical name
(``Hap`` vs ``Hapoel``, ``Atl`` vs ``Atletico``). The fuzzy scorer's only
recourse was ``partial_ratio``, which also rescued real substring poison
(``Aris`` matching inside ``Paris``).

This module provides ``expand_team_abbreviations`` which detects period and
compact-period abbreviation patterns and expands the well-known prefix set.
Applied to both sides before tokenization, the resulting tokens overlap
naturally (``Hap.Haifa`` → ``Hapoel Haifa``), making the abbreviation case
work via plain ``token_set_ratio`` instead of relying on the brittle
``partial_ratio``.

Conservative scope
==================
The expansion map only includes prefixes whose long form is **unambiguous**
within football. Genuinely ambiguous abbreviations (``M.`` could be Maccabi,
Moscow, Madrid; ``A.`` could be Athletic, Atletico, Audax) are intentionally
omitted — the existing matcher already handles them via the remaining tokens.

The map is applied **only at the start of a token** (the abbreviation must be
the prefix). ``Dep.`` matches ``Dep.Saprissa`` but not ``Atl.Dep.Cordoba``.

Format detection
================
The function handles three real-world bookmaker patterns observed in
KvotoLovac scrapes:

1. ``Hap.`` — period as a token suffix (whitespace before next token).
2. ``Hap.Haifa`` — compact period join (no whitespace).
3. ``Cl.America`` — same compact form.

For each, the prefix before the first ``.`` is matched against the map; on
hit, the dotted form is replaced with the long form plus a space, so
downstream tokenizers see ``Hapoel Haifa`` etc.
"""

from __future__ import annotations

import re

# Conservative prefix → expansion map. Add new entries only when the
# expansion is unambiguous within football. Keys must be lowercase.
_ABBREVIATION_PREFIX_MAP: dict[str, str] = {
    "hap": "Hapoel",
    "mac": "Maccabi",
    "dep": "Deportivo",
    "atl": "Atletico",
    "cl": "Club",
    "sp": "Sporting",
    "un": "Union",
    "olym": "Olympique",
    "din": "Dinamo",
    "dyn": "Dynamo",
    "univ": "Universidad",
    "internac": "Internacional",
    "fern": "Fernando",
    "centr": "Central",
    # Single-letter prefixes are deliberately omitted as they are usually
    # ambiguous (``M.``, ``A.``, ``S.``). The longer prefixes above are the
    # high-precision wins observed across the manual labeling pass.
}

_DOTTED_PREFIX_PATTERN = re.compile(
    r"(?:^|(?<=\s))([A-Za-z]{2,6})\.",
)


def expand_team_abbreviations(text: str | None) -> str:
    """Expand known dotted abbreviation prefixes in ``text``.

    Examples
    --------
    >>> expand_team_abbreviations("Hap.Haifa")
    'Hapoel Haifa'
    >>> expand_team_abbreviations("Atl.Mineiro")
    'Atletico Mineiro'
    >>> expand_team_abbreviations("Cl.America")
    'Club America'
    >>> expand_team_abbreviations("Dep.Saprissa")
    'Deportivo Saprissa'
    >>> expand_team_abbreviations("Hap. Haifa")
    'Hapoel  Haifa'
    >>> expand_team_abbreviations("Atletico Mineiro")
    'Atletico Mineiro'
    >>> expand_team_abbreviations("X.Y")  # unknown prefix passes through
    'X.Y'
    >>> expand_team_abbreviations("M.Netanya")  # single-letter prefix passes through
    'M.Netanya'

    Returns the input unchanged if there are no expansions to apply, so
    callers may safely chain it with other normalizers.
    """
    if not text:
        return text or ""

    def _replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        expansion = _ABBREVIATION_PREFIX_MAP.get(prefix.lower())
        if expansion is None:
            return match.group(0)
        return f"{expansion} "

    return _DOTTED_PREFIX_PATTERN.sub(_replace, text)
