"""Unit tests for normalize + evaluate filters (offline, no Apify)."""
from __future__ import annotations

from app.filters import (
    DEFAULT_EXCLUDE_BIO_KEYWORDS,
    DEFAULT_EXCLUDE_URL_KEYWORDS,
    evaluate,
    filter_leads,
    normalize_row,
    to_manyreach,
)


SAMPLE = [
    {
        "username": "mom_sleep_tips",
        "fullName": "Maya Chen",
        "biography": "Newborn sleep tips | Free guides in bio 💌 hello@maysleep.com",
        "followersCount": 42000,
        "email": "hello@maysleep.com",
        "externalUrl": "https://linktr.ee/maysleep",
        "engagementRate": 3.2,
        "isVerified": False,
        "profileUrl": "https://instagram.com/mom_sleep_tips",
    },
    {
        "username": "fit_coach_alex",
        "fullName": "Alex Rivera",
        "biography": "Online coaching | Join my course 💪",
        "followersCount": 55000,
        "email": "",
        "externalUrl": "https://stan.store/alexfit",
        "engagementRate": 2.1,
        "isVerified": False,
    },
    {
        "username": "mega_brand",
        "fullName": "Big Brand",
        "biography": "Official account",
        "followersCount": 2500000,
        "email": "press@bigbrand.com",
        "externalUrl": "https://bigbrand.com",
        "engagementRate": 0.4,
        "isVerified": True,
    },
    {
        "username": "dad_first_year",
        "fullName": "Jordan Lee",
        "biography": "First-time dad | No product yet | jordan.lee.creator@gmail.com",
        "followersCount": 18500,
        "email": "jordan.lee.creator@gmail.com",
        "externalUrl": "",
        "engagementRate": 4.5,
        "isVerified": False,
    },
    {
        "username": "tiny_account",
        "fullName": "Sam",
        "biography": "Just posting",
        "followersCount": 2500,
        "email": "",
        "externalUrl": "",
        "engagementRate": 1.0,
        "isVerified": False,
    },
    {
        "username": "mealprep_mia",
        "fullName": "Mia Ortiz",
        "biography": "Budget family meals | recipes daily",
        "followersCount": 67000,
        "email": "",
        "externalUrl": "https://linktr.ee/miameals",
        "engagementRate": 2.8,
        "isVerified": False,
    },
    {
        "username": "newborn_nurse_kate",
        "fullName": "Kate Brooks",
        "biography": "RN | Newborn care tips | kate@newbornnotes.co",
        "followersCount": 92000,
        "email": "kate@newbornnotes.co",
        "externalUrl": "https://newbornnotes.co",
        "engagementRate": 3.8,
        "isVerified": False,
    },
]


DEFAULT_FILTERS = {
    "min_followers": 10000,
    "max_followers": 100000,
    "require_email": True,
    "exclude_selling_digital": True,
    "min_engagement_rate": 0,
}


def test_normalize_extracts_email_from_bio():
    row = normalize_row(
        {
            "ownerUsername": "@demo",
            "biography": "Hit me at contact@example.com",
            "followersCount": "12,000",
        }
    )
    assert row["username"] == "demo"
    assert row["email"] == "contact@example.com"
    assert row["followers"] == 12000
    assert "instagram.com/demo" in row["profile_url"]


def test_exclude_selling_via_bio_and_url():
    filters = {**DEFAULT_FILTERS}
    row = {**SAMPLE[1], "email": "alex@fit.com"}
    alex = normalize_row(row)
    ok, reason = evaluate(alex, filters)
    assert not ok
    assert "monetized" in reason


def test_followers_bounds():
    filters = {**DEFAULT_FILTERS}
    tiny = normalize_row(SAMPLE[4])
    ok, reason = evaluate(tiny, filters)
    assert not ok
    assert reason.startswith("followers<")

    mega = normalize_row(SAMPLE[2])
    ok, reason = evaluate(mega, filters)
    assert not ok
    assert reason.startswith("followers>")


def test_require_email():
    filters = {**DEFAULT_FILTERS}
    mia = normalize_row(SAMPLE[5])
    ok, reason = evaluate(mia, filters)
    assert not ok
    assert reason == "no_email"


def test_qualified_passers():
    filters = {**DEFAULT_FILTERS}
    qualified, rejected, summary = filter_leads(SAMPLE, filters, niche="parenting")
    usernames = {q["username"] for q in qualified}
    # mom_sleep_tips, dad_first_year, newborn_nurse_kate should pass
    assert "mom_sleep_tips" in usernames
    assert "dad_first_year" in usernames
    assert "newborn_nurse_kate" in usernames
    assert "fit_coach_alex" not in usernames
    assert "tiny_account" not in usernames
    assert "mega_brand" not in usernames
    assert "mealprep_mia" not in usernames
    assert len(qualified) == 3
    assert sum(summary.values()) == len(rejected)


def test_exclude_selling_off_keeps_stan_store_if_email():
    # Without exclude_selling, stan.store coach still fails require_email
    # Give them an email via bio normalize path
    row = {
        **SAMPLE[1],
        "email": "alex@fit.com",
        "biography": "Online coaching | Join my course 💪 alex@fit.com",
    }
    filters = {**DEFAULT_FILTERS, "exclude_selling_digital": False}
    ok, reason = evaluate(normalize_row(row), filters)
    assert ok
    assert reason == "ok"


def test_manyreach_shape():
    q, _, _ = filter_leads(SAMPLE, DEFAULT_FILTERS, niche="parenting")
    rows = to_manyreach(q)
    assert rows
    assert set(rows[0].keys()) == {
        "email",
        "first_name",
        "instagram_username",
        "instagram_url",
        "followers",
        "bio",
        "website",
    }


def test_default_keyword_lists_seeded():
    assert "gumroad" in DEFAULT_EXCLUDE_BIO_KEYWORDS
    assert "stan.store" in DEFAULT_EXCLUDE_URL_KEYWORDS
    assert "teachable.com" in DEFAULT_EXCLUDE_URL_KEYWORDS



def test_normalize_twitter_primary_email_and_x_url():
    row = normalize_row(
        {
            "recordType": "leadProfile",
            "username": "fitmom",
            "displayName": "Fit Mom",
            "bio": "Home workouts daily",
            "followersCount": 22000,
            "primaryEmail": "hi@fitmom.example",
            "websiteUrl": "https://fitmom.example",
        },
        platform="twitter",
    )
    assert row["email"] == "hi@fitmom.example"
    assert row["full_name"] == "Fit Mom"
    assert row["external_url"] == "https://fitmom.example"
    assert row["profile_url"] == "https://x.com/fitmom"


def test_normalize_twitter_shaped_row_without_platform_arg():
    row = normalize_row(
        {
            "recordType": "leadProfile",
            "handle": "coachx",
            "bio": "Training tips",
            "followersCount": 15000,
            "profileUrl": "https://x.com/coachx",
        }
    )
    assert row["username"] == "coachx"
    assert "x.com/coachx" in row["profile_url"]
