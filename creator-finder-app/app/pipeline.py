"""Multi-stage Instagram discovery pipeline on Apify's official scrapers.

Why: the old single "creator leads" actor finds profiles through web search,
so a run with 10 keywords + 10 hashtags came back with 0–30 profiles. The
official scrapers read Instagram directly:

  1. Discover  hashtags → apify/instagram-hashtag-scraper  (post owners)
               keywords → apify/instagram-search-scraper   (account search)
               seeds    → go straight to step 2
  2. Enrich    usernames → apify/instagram-profile-scraper (followers, bio,
               bio links, last 12 posts, related profiles)
  3. Expand    (optional) related profiles of good matches → enrich again
  4. Link check  open bio links locally for emails + storefronts (free)
  5. Filter + score locally, write CSVs

Each Apify run picks the next enabled key; a key that is out of credit or
rate-limited is skipped for the rest of the job, an invalid key is marked bad.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app import apify_client, linkcheck
from app.apify_client import ApifyAuthError, ApifyError, ApifyKeyUnavailableError
from app.db import JOBS_DIR
from app.filters import (
    dedupe,
    evaluate,
    evaluate_leads,
    location_terms,
    normalize_row,
    write_exports,
)
from app.models import ApiKey, Job

log = logging.getLogger("creator_finder.pipeline")

HASHTAG_ACTOR = "apify~instagram-hashtag-scraper"
SEARCH_ACTOR = "apify~instagram-search-scraper"
PROFILE_ACTOR = "apify~instagram-profile-scraper"

PROFILE_BATCH = 100  # usernames per profile-scraper run

DEFAULT_OPTIONS: dict[str, Any] = {
    "posts_per_hashtag": 150,
    "accounts_per_keyword": 50,
    "max_profiles": 300,
    "expand_related": True,
    "check_links": True,
    "location_strict": False,
    "active_within_days": 60,
    "skip_seen": True,
}


def merged_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    opts = {**DEFAULT_OPTIONS, **(raw or {})}
    opts["posts_per_hashtag"] = max(10, min(int(opts["posts_per_hashtag"]), 2000))
    opts["accounts_per_keyword"] = max(5, min(int(opts["accounts_per_keyword"]), 250))
    opts["max_profiles"] = max(10, min(int(opts["max_profiles"]), 3000))
    opts["active_within_days"] = max(0, int(opts["active_within_days"] or 0))
    return opts


def clean_hashtag(tag: str) -> str:
    """Instagram hashtags are letters/digits/underscore only."""
    return re.sub(r"[^\w]", "", (tag or "").strip().lstrip("#"), flags=re.UNICODE).lower()


class KeyPool:
    """Round-robin over enabled keys with per-job failover."""

    def __init__(self, session):
        self.session = session
        self.unavailable: set[int] = set()
        self._i = 0
        self.used_labels: list[str] = []

    def _usable(self) -> list[ApiKey]:
        keys = (
            self.session.query(ApiKey)
            .filter(ApiKey.enabled.is_(True), ApiKey.is_bad.is_(False))
            .order_by(ApiKey.id)
            .all()
        )
        return [k for k in keys if k.id not in self.unavailable]

    async def run(self, actor: str, run_input: dict[str, Any], on_status=None):
        last_err = "No enabled Apify API keys (add one on the API keys page)"
        while True:
            keys = self._usable()
            if not keys:
                raise ApifyError(f"No usable Apify key left. Last error: {last_err}")
            key = keys[self._i % len(keys)]
            self._i += 1
            try:
                items, _run = await apify_client.run_actor(
                    key.token, actor, run_input, on_status
                )
                if key.label not in self.used_labels:
                    self.used_labels.append(key.label)
                return items
            except ApifyAuthError as e:
                log.warning("Key %s rejected by Apify; marking bad", key.label)
                key.is_bad = True
                self.session.commit()
                last_err = f"{key.label}: {e}"
            except ApifyKeyUnavailableError as e:
                log.warning("Key %s unavailable (%s); trying next", key.label, e)
                self.unavailable.add(key.id)
                last_err = f"{key.label}: {e}"


class Pipeline:
    def __init__(self, session, job: Job):
        self.session = session
        self.job = job
        self.opts = merged_options(json.loads(job.options_json or "{}"))
        self.pool = KeyPool(session)
        self.usage = {"hashtag_posts": 0, "search_results": 0, "profiles": 0, "runs": 0}
        self.warnings: list[str] = []
        # username(lower) -> {"hits": int, "via": set[str], "locations": set[str]}
        self.candidates: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"hits": 0, "via": set(), "locations": set(), "handle": ""}
        )

    # ── helpers ──────────────────────────────────────────────
    def progress(self, msg: str) -> None:
        log.info("Job %s: %s", self.job.id, msg)
        self.job.progress_message = msg[:500]
        self.session.commit()

    def add_candidate(self, username: str, via: str, location: str | None = None) -> None:
        u = (username or "").strip().lstrip("@")
        if not u:
            return
        c = self.candidates[u.lower()]
        c["handle"] = c["handle"] or u
        c["hits"] += 1
        c["via"].add(via)
        if location:
            c["locations"].add(location)

    async def _run(self, actor: str, run_input: dict[str, Any], label: str) -> list[dict]:
        async def on_status(run: dict[str, Any]) -> None:
            msg = run.get("statusMessage") or run.get("status") or ""
            self.progress(f"{label} — {msg}")

        self.usage["runs"] += 1
        self.progress(f"{label} — starting")
        return await self.pool.run(actor, run_input, on_status)

    def seen_before(self) -> set[str]:
        """Usernames already qualified in earlier jobs (so we don't pay twice)."""
        seen: set[str] = set()
        rows = (
            self.session.query(Job.results_json)
            .filter(Job.id != self.job.id, Job.results_json.isnot(None))
            .all()
        )
        for (rj,) in rows:
            try:
                for r in json.loads(rj or "[]"):
                    if r.get("username"):
                        seen.add(r["username"].lower())
            except ValueError:
                continue
        return seen

    # ── stages ───────────────────────────────────────────────
    async def discover(self, keywords: list[str], hashtags: list[str], seeds: list[str]):
        for s in seeds:
            self.add_candidate(s, "seed")

        tags = [t for t in (clean_hashtag(h) for h in hashtags) if t]
        if tags:
            try:
                posts = await self._run(
                    HASHTAG_ACTOR,
                    {"hashtags": tags, "resultsLimit": self.opts["posts_per_hashtag"]},
                    f"Scanning {len(tags)} hashtag(s)",
                )
                self.usage["hashtag_posts"] += len(posts)
                for p in posts:
                    owner = p.get("ownerUsername") or (p.get("owner") or {}).get("username")
                    tag = p.get("hashtag") or p.get("inputUrl") or "hashtag"
                    tag = str(tag).rstrip("/").rsplit("/", 1)[-1]
                    self.add_candidate(owner, f"#{tag}", p.get("locationName"))
            except ApifyError as e:
                self.warnings.append(f"Hashtag scan failed: {e}")

        for kw in keywords:
            try:
                results = await self._run(
                    SEARCH_ACTOR,
                    {
                        "search": kw,
                        "searchType": "user",
                        "searchLimit": self.opts["accounts_per_keyword"],
                    },
                    f"Searching accounts for “{kw}”",
                )
            except ApifyError as e:
                self.warnings.append(f"Keyword search “{kw}” failed: {e}")
                continue
            self.usage["search_results"] += len(results)
            for r in results:
                self.add_candidate(r.get("username"), f"search:{kw}")

    async def enrich(self, usernames: list[str], label: str) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for i in range(0, len(usernames), PROFILE_BATCH):
            batch = usernames[i : i + PROFILE_BATCH]
            try:
                items = await self._run(
                    PROFILE_ACTOR,
                    {"usernames": batch},
                    f"{label}: profiles {i + 1}–{i + len(batch)} of {len(usernames)}",
                )
            except ApifyError as e:
                self.warnings.append(f"Profile batch {i // PROFILE_BATCH + 1} failed: {e}")
                continue
            self.usage["profiles"] += len(items)
            profiles.extend(items)
            self.job.raw_count = len(profiles)
            self.session.commit()
        return profiles

    def to_lead(self, raw: dict[str, Any]) -> dict[str, Any]:
        lead = normalize_row(raw, platform="instagram")
        c = self.candidates.get(lead["username"].lower())
        if c:
            lead["source_hits"] = c["hits"]
            lead["found_via"] = sorted(c["via"])
            lead["post_locations"] = sorted(set(lead["post_locations"]) | c["locations"])
        else:
            lead["source_hits"] = 0
            lead["found_via"] = ["related"]
        return lead

    # ── main ─────────────────────────────────────────────────
    async def run(self) -> None:
        job = self.job
        keywords = json.loads(job.keywords_json or "[]")
        hashtags = json.loads(job.hashtags_json or "[]")
        seeds = json.loads(job.seed_usernames_json or "[]")
        filters: dict[str, Any] = {
            "min_followers": job.min_followers,
            "max_followers": job.max_followers,
            "require_email": job.require_email,
            "exclude_selling_digital": job.exclude_selling,
            "min_engagement_rate": float(job.min_engagement_rate_str or 0),
            "active_within_days": self.opts["active_within_days"],
            "location_terms": location_terms(job.location or ""),
            "location_strict": self.opts["location_strict"],
        }

        await self.discover(keywords, hashtags, seeds)
        if not self.candidates:
            raise ApifyError(
                "Discovery found no accounts. " + " ".join(self.warnings)
                if self.warnings
                else "Discovery found no accounts — try broader or more popular hashtags."
            )

        skip = self.seen_before() if self.opts["skip_seen"] else set()
        seed_set = {s.lower() for s in seeds}
        ranked = sorted(
            (u for u in self.candidates if u not in skip or u in seed_set),
            key=lambda u: (u not in seed_set, -self.candidates[u]["hits"]),
        )
        budget = self.opts["max_profiles"]
        to_enrich = [self.candidates[u]["handle"] for u in ranked[:budget]]
        self.progress(
            f"Found {len(self.candidates)} accounts"
            + (f" ({len(self.candidates) - len(ranked)} already in earlier jobs)" if skip else "")
            + f"; checking {len(to_enrich)} profiles"
        )
        raw_profiles = await self.enrich(to_enrich, "Profiles")

        leads = [self.to_lead(p) for p in raw_profiles]
        raw_all = list(raw_profiles)

        # Expand: related profiles of accounts that already fit the basic filters
        if self.opts["expand_related"] and len(to_enrich) < budget:
            pre = {**filters, "require_email": False, "exclude_selling_digital": False}
            have = {lead["username"].lower() for lead in leads}
            related: list[str] = []
            for lead in leads:
                if not evaluate(lead, pre)[0]:
                    continue
                for r in lead.get("related_usernames") or []:
                    rl = r.lower()
                    if rl in have or rl in skip:
                        continue
                    have.add(rl)
                    related.append(r)
                    self.add_candidate(r, f"related:@{lead['username']}")
            related = related[: budget - len(to_enrich)]
            if related:
                self.progress(f"Checking {len(related)} related creators")
                more = await self.enrich(related, "Related creators")
                raw_all += more
                leads += [self.to_lead(p) for p in more]

        leads = dedupe(leads)

        if self.opts["check_links"]:
            # Only open links for profiles that pass the cheap checks
            pre = {**filters, "require_email": False, "exclude_selling_digital": False}
            worth = [lead for lead in leads if evaluate(lead, pre)[0]]
            self.progress(f"Opening bio links for {len(worth)} creators (emails + storefronts)")
            await linkcheck.check_links(worth)

        qualified, rejected, reason_counts = evaluate_leads(leads, filters, niche=job.niche)

        job_dir = JOBS_DIR / str(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "raw.json").write_text(
            json.dumps(raw_all, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        write_exports(job_dir, qualified, rejected)

        job.raw_count = len(leads)
        job.qualified_count = len(qualified)
        job.rejected_count = len(rejected)
        job.reject_summary_json = json.dumps(reason_counts)
        job.results_json = json.dumps(qualified, default=str)
        job.usage_json = json.dumps({**self.usage, "warnings": self.warnings})
        job.api_key_label = ", ".join(self.pool.used_labels) or None
        job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        job.error_message = "\n".join(self.warnings) or None
        job.progress_message = None
        self.session.commit()
        log.info(
            "Job %s done: %s candidates, %s profiles, %s qualified",
            job.id, len(self.candidates), len(leads), len(qualified),
        )
