"""Unit tests for Apify actor input construction."""
from __future__ import annotations

from app.apify_client import DEFAULT_ACTOR, build_actor_input


def test_mapped_country_uses_preferred_country_without_term_suffix():
    payload = build_actor_input(
        ["home workout mom creator"],
        [],
        "United States",
        50,
        DEFAULT_ACTOR,
        min_followers=10_000,
        max_followers=100_000,
    )

    assert payload["preferredCountry"] == "US"
    assert payload["searchTerms"] == ["home workout mom creator"]
    assert "United States" not in " ".join(payload["searchTerms"])
    assert payload["requireBusinessOrCreator"] is False
    assert payload["minFollowers"] == 10_000
    assert payload["maxFollowers"] == 100_000


def test_seed_only_sets_usernames_and_seed_discovery():
    payload = build_actor_input(
        [],
        [],
        "United States",
        50,
        DEFAULT_ACTOR,
        min_followers=5_000,
        max_followers=250_000,
        seed_usernames=["@FitMomUsername", "https://www.instagram.com/homefitnesscoach/"],
        seed_discovery=True,
    )

    assert payload["usernames"] == ["FitMomUsername", "homefitnesscoach"]
    assert payload["seedDiscovery"] is True
    assert "searchTerms" not in payload
    assert "hashtags" not in payload
    assert payload["preferredCountry"] == "US"
    assert payload["minFollowers"] == 5_000
    assert payload["maxFollowers"] == 250_000
    assert payload["maxLeads"] == 50


def test_seed_without_discovery_omits_seed_discovery_flag():
    payload = build_actor_input(
        ["home workout"],
        [],
        "",
        30,
        seed_usernames=["microcreator"],
        seed_discovery=False,
    )
    assert payload["usernames"] == ["microcreator"]
    assert "seedDiscovery" not in payload
    assert payload["searchTerms"] == ["home workout"]


def test_normalize_instagram_username():
    from app.apify_client import normalize_instagram_username

    assert normalize_instagram_username("@Handle") == "Handle"
    assert normalize_instagram_username("https://instagram.com/foo/") == "foo"
    assert normalize_instagram_username("  ") is None


def test_twitter_keyword_only_payload():
    from app.apify_client import build_actor_input, DEFAULT_TWITTER_ACTOR

    payload = build_actor_input(
        ["home fitness mom", "workout", "mom life"],
        [],
        "USA",
        50,
        DEFAULT_TWITTER_ACTOR,
        min_followers=5000,
        max_followers=250000,
        platform="twitter",
        require_email=False,
        exclude_selling=True,
    )

    assert payload["resultType"] == "leadProfiles"
    assert payload["profileSearch"]["findProfilesBy"] == "keywordQuery"
    assert payload["profileSearch"]["keywordQuery"] == "home fitness mom"
    assert payload["profileSearch"]["maxCandidates"] == 50
    assert payload["bioIncludeTerms"] == ["workout", "mom life"]
    assert payload["locationContains"] == "USA"
    assert payload["minFollowers"] == 5000
    assert payload["maxFollowers"] == 250000
    assert payload["requireEmail"] is False
    assert "gumroad" in payload["bioExcludeTerms"]
    assert "accountReferences" not in payload["profileSearch"]


def test_twitter_seed_only_payload():
    from app.apify_client import build_actor_input

    payload = build_actor_input(
        [],
        [],
        "",
        50,
        platform="twitter",
        min_followers=5000,
        max_followers=250000,
        seed_usernames=["@handle1", "https://x.com/handle2", "handle3"],
        require_email=False,
    )

    assert payload["profileSearch"]["findProfilesBy"] == "accountReferences"
    refs = payload["profileSearch"]["accountReferences"]
    assert refs == ["@handle1", "@handle2", "@handle3"]
    assert payload["profileSearch"]["maxCandidates"] == 50
    assert "keywordQuery" not in payload["profileSearch"]


def test_twitter_keywords_take_precedence_over_seeds():
    from app.apify_client import build_actor_input

    payload = build_actor_input(
        ["home fitness mom"],
        [],
        "",
        40,
        platform="twitter",
        seed_usernames=["@seed1"],
    )
    assert payload["profileSearch"]["findProfilesBy"] == "keywordQuery"
    assert payload["profileSearch"]["keywordQuery"] == "home fitness mom"
    assert "accountReferences" not in payload["profileSearch"]


def test_normalize_twitter_username():
    from app.apify_client import normalize_twitter_username

    assert normalize_twitter_username("@Handle") == "Handle"
    assert normalize_twitter_username("https://x.com/foo/") == "foo"
    assert normalize_twitter_username("https://twitter.com/bar") == "bar"
    assert normalize_twitter_username("  ") is None
