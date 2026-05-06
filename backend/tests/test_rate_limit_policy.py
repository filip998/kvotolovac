from __future__ import annotations

import pytest

from app.services.rate_limit_policy import (
    RateLimitPolicy,
    RateLimitPolicyError,
    parse_bookmaker_rate_limits,
    parse_scrape_type_rate_limits,
)


def test_parse_bookmaker_rate_limits_accepts_comma_and_semicolon_entries():
    assert parse_bookmaker_rate_limits("365:1; BetOle:0.5, meridian:2") == {
        "365": 1.0,
        "betole": 0.5,
        "meridian": 2.0,
    }


def test_parse_bookmaker_rate_limits_rejects_duplicates():
    with pytest.raises(RateLimitPolicyError):
        parse_bookmaker_rate_limits("betole:1,BetOle:0.5")


def test_parse_scrape_type_rate_limits_accepts_lane_and_detail_entries():
    policies = parse_scrape_type_rate_limits(
        "betole:outcome_offer:full:0.5,365:threshold_odds:1"
    )

    assert [policy.key for policy in policies] == [
        "betole:outcome_offer:full",
        "365:threshold_odds:*",
    ]
    assert [policy.rate_limit_per_second for policy in policies] == [0.5, 1.0]


@pytest.mark.parametrize(
    "raw",
    [
        "betole",
        "betole:not_a_lane:1",
        "betole:outcome_offer:turbo:1",
        "betole:outcome_offer:full:not-a-number",
        "betole:outcome_offer:full:99",
    ],
)
def test_parse_scrape_type_rate_limits_rejects_invalid_entries(raw: str):
    with pytest.raises(RateLimitPolicyError):
        parse_scrape_type_rate_limits(raw)


def test_effective_rate_limit_preserves_meridian_default_without_policy():
    policy = RateLimitPolicy(bookmaker_rate_limits={}, scrape_type_rate_limits=())

    assert policy.effective_rate_limit(
        bookmaker_id="meridian",
        lane="threshold_odds",
        detail_mode=None,
        global_rate_limit_per_second=1.0,
        meridian_rate_limit_per_second=2.0,
    ) == 2.0


def test_effective_rate_limit_uses_specific_policy_before_bookmaker_policy():
    policy = RateLimitPolicy(
        bookmaker_rate_limits={"betole": 1.0},
        scrape_type_rate_limits=parse_scrape_type_rate_limits(
            "betole:outcome_offer:full:0.25"
        ),
    )

    assert policy.effective_rate_limit(
        bookmaker_id="betole",
        lane="outcome_offer",
        detail_mode="full",
        global_rate_limit_per_second=3.0,
        meridian_rate_limit_per_second=2.0,
    ) == 0.25
    assert policy.effective_rate_limit(
        bookmaker_id="betole",
        lane="outcome_offer",
        detail_mode="partial",
        global_rate_limit_per_second=3.0,
        meridian_rate_limit_per_second=2.0,
    ) == 1.0


def test_effective_rate_limit_policy_cannot_raise_existing_default():
    policy = RateLimitPolicy(
        bookmaker_rate_limits={"365": 5.0},
        scrape_type_rate_limits=parse_scrape_type_rate_limits(
            "betole:outcome_offer:full:4"
        ),
    )

    assert policy.effective_rate_limit(
        bookmaker_id="365",
        lane="outcome_offer",
        detail_mode=None,
        global_rate_limit_per_second=1.0,
        meridian_rate_limit_per_second=2.0,
    ) == 1.0
    assert policy.effective_rate_limit(
        bookmaker_id="betole",
        lane="outcome_offer",
        detail_mode="full",
        global_rate_limit_per_second=1.0,
        meridian_rate_limit_per_second=2.0,
    ) == 1.0
