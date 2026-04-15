from __future__ import annotations

from app.services.text_normalizer import (
    compact_identity_text,
    normalize_identity_text,
    tokenize_identity_text,
)


def test_normalize_identity_text_strips_diacritics_and_punctuation():
    assert normalize_identity_text("Budućnost VOLI") == "buducnost voli"
    assert normalize_identity_text("Inst.de Cordoba") == "inst de cordoba"


def test_tokenize_identity_text_can_preserve_hyphens():
    assert tokenize_identity_text("Codi Miller-McIntyre", keep_hyphens=True) == [
        "codi",
        "miller-mcintyre",
    ]
    assert tokenize_identity_text("Codi Miller-McIntyre") == [
        "codi",
        "miller",
        "mcintyre",
    ]


def test_compact_identity_text_collapses_spacing_punctuation_and_diacritics():
    assert compact_identity_text("S. Miljenović") == "smiljenovic"
    assert compact_identity_text("Codi Miller McIntyre") == "codimillermcintyre"
