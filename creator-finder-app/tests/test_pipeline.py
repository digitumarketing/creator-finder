"""Offline tests for the official-scraper pipeline (Apify + network mocked)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import apify_client, db, linkcheck, pipeline
from app.apify_client import ApifyAuthError, ApifyKeyUnavailableError
from app.filters import evaluate, location_terms, match_location, normalize_row
from app.jobs import create_job
from app.models import ApiKey, Job


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    db.init_db()
    s = db.get_session()
    yield s
    s.close()
    db._engine.dispose()


def _recent(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _profile(username, followers, bio="", url="", related=(), likes=900, days=3):
    return {
        "username": username,
        "fullName": username.title(),
        "biography": bio,
        "externalUrl": url,
        "followersCount": followers,
        "private": False,
        "verified": False,
        "latestPosts": [
            {"likesCount": likes, "commentsCount": 30, "timestamp": _recent(days)}
        ],
        "relatedProfiles": [{"username": r} for r in related],
    }


PROFILES = {
    "toddlermealmom": _profile(
        "toddlermealmom", 30000, "Toddler meals 🥕 Austin, TX | mom@meals.co",
        related=["babyfoodbyamy"],
    ),
    "sellingcoach": _profile(
        "sellingcoach", 40000, "Grab my ebook 👇 coach@x.com", "https://stan.store/coach"
    ),
    "hugebrand": _profile("hugebrand", 900000, "Official"),
    "linkonly": _profile("linkonly", 25000, "Mom of 3", "https://linktr.ee/linkonly"),
    "sleepyhead": _profile("sleepyhead", 20000, "tired mama", days=200),
    "babyfoodbyamy": _profile("babyfoodbyamy", 15000, "BLW ideas | amy@blw.com"),
    "ghost": {"username": "ghost", "error": "not_found", "errorDescription": "Not found"},
}


def _fake_actor(calls):
    async def fake(token, actor, run_input, on_status=None):
        calls.append((token, actor, run_input))
        if actor == pipeline.HASHTAG_ACTOR:
            owners = ["toddlermealmom", "sellingcoach", "hugebrand", "toddlermealmom", "ghost"]
            return [
                {"ownerUsername": o, "hashtag": "toddlermeals", "locationName": None}
                for o in owners
            ], {}
        if actor == pipeline.SEARCH_ACTOR:
            return [{"username": "linkonly"}, {"username": "sleepyhead"}], {}
        if actor == pipeline.PROFILE_ACTOR:
            return [PROFILES[u] for u in run_input["usernames"] if u in PROFILES], {}
        raise AssertionError(actor)

    return fake


def _make_job(session, **kw):
    session.add(ApiKey(label="a", token="apify_api_aaaaaaaaaa"))
    session.commit()
    defaults = dict(
        niche="parenting",
        keywords=["toddler meals"],
        hashtags=["#toddlermeals"],
        location="United States",
        min_followers=10_000,
        max_followers=100_000,
        require_email=True,
        exclude_selling=True,
        min_engagement_rate=0,
        max_leads=50,
        actor_id="",
        engine="pipeline",
        options={"check_links": True},
    )
    defaults.update(kw)
    return create_job(session, **defaults)


def test_pipeline_end_to_end(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(apify_client, "run_actor", _fake_actor(calls))

    async def fake_links(leads):
        for lead in leads:
            if lead["username"] == "linkonly":
                lead["link_emails"] = ["hello@linkonly.com"]
                lead["email"] = "hello@linkonly.com"
                lead["email_source"] = "link_in_bio"
        return len(leads)

    monkeypatch.setattr(linkcheck, "check_links", fake_links)
    job = _make_job(session)
    asyncio.run(pipeline.Pipeline(session, job).run())
    session.refresh(job)

    assert job.status == "succeeded", job.error_message
    qualified = {r["username"]: r for r in json.loads(job.results_json)}
    assert set(qualified) == {"toddlermealmom", "linkonly", "babyfoodbyamy"}
    # related creator came from the seed's relatedProfiles
    assert qualified["babyfoodbyamy"]["found_via"] == ["related:@toddlermealmom"]
    assert qualified["toddlermealmom"]["location_match"] == "texas" or qualified[
        "toddlermealmom"
    ]["location_match"] == "austin"
    # best lead first
    first = json.loads(job.results_json)[0]["username"]
    assert first == "toddlermealmom"

    reasons = json.loads(job.reject_summary_json)
    assert reasons.get("followers>100000") == 1
    assert any(k.startswith("bio_monetized") or k.startswith("url_monetized") for k in reasons)
    assert "profile_unavailable" in reasons
    assert "inactive>60d" in reasons

    hashtag_call = next(c for c in calls if c[1] == pipeline.HASHTAG_ACTOR)
    assert hashtag_call[2] == {"hashtags": ["toddlermeals"], "resultsLimit": 150}
    assert (pipeline.JOBS_DIR / str(job.id) / "manyreach.csv").exists()


def test_skip_seen_avoids_paying_twice(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(apify_client, "run_actor", _fake_actor(calls))
    monkeypatch.setattr(linkcheck, "check_links", lambda leads: asyncio.sleep(0, 0))
    old = Job(niche="old", results_json=json.dumps([{"username": "toddlermealmom"}]))
    session.add(old)
    session.commit()
    job = _make_job(session, options={"skip_seen": True, "expand_related": False})
    asyncio.run(pipeline.Pipeline(session, job).run())
    enriched = [u for c in calls if c[1] == pipeline.PROFILE_ACTOR for u in c[2]["usernames"]]
    assert "toddlermealmom" not in enriched
    assert "linkonly" in enriched


def test_keypool_rotates_past_bad_and_exhausted_keys(session, monkeypatch):
    for label in ("bad", "broke", "good"):
        session.add(ApiKey(label=label, token=f"apify_api_{label}xxxxxx"))
    session.commit()

    async def fake(token, actor, run_input, on_status=None):
        if "bad" in token:
            raise ApifyAuthError("nope")
        if "broke" in token:
            raise ApifyKeyUnavailableError("out of credit")
        return [{"ok": 1}], {}

    monkeypatch.setattr(apify_client, "run_actor", fake)
    pool = pipeline.KeyPool(session)
    assert asyncio.run(pool.run("x", {})) == [{"ok": 1}]
    assert pool.used_labels == ["good"]
    bad = session.query(ApiKey).filter_by(label="bad").one()
    broke = session.query(ApiKey).filter_by(label="broke").one()
    assert bad.is_bad is True
    assert broke.is_bad is False  # out of credit is not "invalid"


def test_start_error_classification():
    def resp(code, etype):
        return httpx.Response(code, json={"error": {"type": etype, "message": "m"}})

    with pytest.raises(ApifyAuthError):
        apify_client._raise_for_start(resp(401, "token-not-valid"))
    with pytest.raises(ApifyKeyUnavailableError):
        apify_client._raise_for_start(resp(403, "not-enough-usage-to-run-paid-actor"))
    with pytest.raises(ApifyKeyUnavailableError):
        apify_client._raise_for_start(resp(402, "payment-required"))
    with pytest.raises(apify_client.ApifyError):
        apify_client._raise_for_start(resp(400, "invalid-input"))


def test_analyse_page_finds_email_and_store():
    page = """<html><a href="mailto:hi@creator.com">Email</a>
      <script>{"url":"https:\\/\\/stan.store\\/creator","title":"My Ebook"}</script>
      <img src="logo@2x.png"> support@linktr.ee</html>"""
    found = linkcheck.analyse_page(page)
    assert found["emails"] == ["hi@creator.com"]
    assert "stan.store" in found["selling"]
    assert "ebook" in found["selling"]


def test_of_course_is_not_a_course():
    lead = normalize_row({"username": "a", "biography": "Of course I love tacos a@b.co",
                          "followersCount": 20000})
    ok, reason = evaluate(lead, {"min_followers": 1, "max_followers": 10**9,
                                 "exclude_selling_digital": True})
    assert ok, reason


def test_engagement_from_latest_posts_skips_hidden_likes():
    lead = normalize_row({
        "username": "a", "followersCount": 10000,
        "latestPosts": [{"likesCount": 450, "commentsCount": 50},
                        {"likesCount": -1, "commentsCount": 5}],
    })
    assert lead["engagement_rate"] == 5.0


def test_location_terms_and_match():
    terms = location_terms("USA")
    assert "united states" in terms and "texas" in terms
    assert match_location({"biography": "Mom in Dallas 🤠"}, terms) == "dallas"
    # "us" must not match inside words
    assert match_location({"biography": "just us moms"}, location_terms("uk")) == ""
