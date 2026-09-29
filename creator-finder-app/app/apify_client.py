"""Minimal Apify REST client. Never logs API tokens."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("creator_finder.apify")

APIFY_BASE = "https://api.apify.com/v2"
DEFAULT_ACTOR = "coregent~instagram-creator-leads-scraper"
DEFAULT_TWITTER_ACTOR = "maximedupre~twitter-lead-scraper"
# Polling
POLL_INTERVAL_SEC = 5.0
POLL_TIMEOUT_SEC = 60 * 45  # 45 minutes max


class ApifyAuthError(Exception):
    """Token rejected (401/403)."""


class ApifyError(Exception):
    """Other Apify API errors."""


class ApifyKeyUnavailableError(ApifyError):
    """Token is valid but can't run this now (out of credit, usage limit, rate
    limited, actor not rented). Try the next key; don't mark this one bad."""


def _error_type(r: httpx.Response) -> str:
    try:
        err = r.json().get("error") or {}
        return str(err.get("type") or "").lower()
    except Exception:
        return ""


def _raise_for_start(r: httpx.Response) -> None:
    """Classify a failed run start so callers can rotate keys correctly."""
    if r.status_code < 400:
        return
    etype = _error_type(r)
    detail = r.text[:500]
    if r.status_code == 401 or "token" in etype or "user-or-token" in etype:
        raise ApifyAuthError("Invalid or unauthorized API token")
    if r.status_code in (402, 403, 429) or any(
        w in etype for w in ("usage", "limit", "credit", "rent", "memory")
    ):
        raise ApifyKeyUnavailableError(
            f"Key can't run this actor now: HTTP {r.status_code} — {detail}"
        )
    log.error("start_actor_run failed HTTP %s: %s", r.status_code, detail)
    raise ApifyError(f"Start run failed: HTTP {r.status_code} — {detail}")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def actor_to_api_id(actor: str) -> str:
    """Convert 'user/name' to 'user~name' for REST paths."""
    a = (actor or "").strip()
    if not a:
        return DEFAULT_ACTOR
    return a.replace("/", "~")


async def test_token(token: str) -> dict[str, Any]:
    """GET /users/me — returns username + plan info. Raises ApifyAuthError on bad token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{APIFY_BASE}/users/me", headers=_headers(token))
    if r.status_code in (401, 403):
        raise ApifyAuthError("Invalid or unauthorized API token")
    if r.status_code >= 400:
        raise ApifyError(f"Apify users/me failed: HTTP {r.status_code}")
    data = r.json().get("data") or r.json()
    username = data.get("username") or data.get("email") or "unknown"
    plan = ""
    plan_obj = data.get("plan") or data.get("subscription") or {}
    if isinstance(plan_obj, dict):
        plan = plan_obj.get("id") or plan_obj.get("name") or plan_obj.get("tier") or ""
    elif isinstance(plan_obj, str):
        plan = plan_obj
    return {"username": username, "plan": str(plan) or "—", "raw": data}


async def start_actor_run(
    token: str,
    actor_id: str,
    run_input: dict[str, Any],
) -> dict[str, Any]:
    """POST /acts/{actorId}/runs — returns run object with id, defaultDatasetId, status."""
    aid = actor_to_api_id(actor_id)
    url = f"{APIFY_BASE}/acts/{aid}/runs"
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=_headers(token), json=run_input)
    _raise_for_start(r)
    body = r.json()
    return body.get("data") or body


async def get_run(token: str, run_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}", headers=_headers(token))
    if r.status_code in (401, 403):
        raise ApifyAuthError("Invalid or unauthorized API token")
    if r.status_code >= 400:
        raise ApifyError(f"Get run failed: HTTP {r.status_code}")
    body = r.json()
    return body.get("data") or body


async def get_dataset_info(token: str, dataset_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}",
            headers=_headers(token),
        )
    if r.status_code in (401, 403):
        raise ApifyAuthError("Invalid or unauthorized API token")
    if r.status_code >= 400:
        raise ApifyError(f"Dataset info failed: HTTP {r.status_code}")
    body = r.json()
    return body.get("data") or body


async def fetch_dataset_items(token: str, dataset_id: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=_headers(token),
            params={"format": "json", "clean": "true"},
        )
    if r.status_code in (401, 403):
        raise ApifyAuthError("Invalid or unauthorized API token")
    if r.status_code >= 400:
        raise ApifyError(f"Dataset fetch failed: HTTP {r.status_code}")
    data = r.json()
    if isinstance(data, list):
        return data
    return []


TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}


async def run_actor(
    token: str,
    actor_id: str,
    run_input: dict[str, Any],
    on_status: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Start a run, wait for it to finish, return (items, final_run_data).

    A run that ends FAILED/ABORTED/TIMED-OUT still returns whatever it saved
    (partial results beat nothing); it raises only when nothing was saved.
    ``on_status(run_data)`` is awaited after each poll when given.
    """
    try:
        run = await start_actor_run(token, actor_id, run_input)
    except httpx.TransportError as e:
        raise ApifyError(f"Couldn't reach Apify ({e.__class__.__name__}: {e})") from e
    run_id = run.get("id")
    status = (run.get("status") or "RUNNING").upper()
    elapsed = 0.0
    net_failures = 0
    while status not in TERMINAL_STATUSES:
        if elapsed >= POLL_TIMEOUT_SEC:
            raise ApifyError(f"Apify run {run_id} timed out waiting for completion")
        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC
        try:
            run = await get_run(token, run_id)
        except httpx.TransportError as e:
            # The run keeps going on Apify's side; ride out short network blips.
            net_failures += 1
            if net_failures >= 10:
                raise ApifyError(f"Lost connection to Apify while waiting for run {run_id}") from e
            continue
        net_failures = 0
        status = (run.get("status") or "").upper()
        if on_status is not None:
            await on_status(run)
    dataset_id = run.get("defaultDatasetId")
    items: list[dict[str, Any]] = []
    for attempt in range(3):
        try:
            items = await fetch_dataset_items(token, dataset_id) if dataset_id else []
            break
        except httpx.TransportError as e:
            if attempt == 2:
                raise ApifyError(f"Couldn't download results of run {run_id}: {e}") from e
            await asyncio.sleep(POLL_INTERVAL_SEC)
    if status != "SUCCEEDED" and not items:
        msg = (run.get("statusMessage") or "")[:300]
        raise ApifyError(f"Apify run {run_id} ended with status {status}. {msg}".strip())
    return items, run


_COUNTRY_CODES = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "UK",
    "uk": "UK",
    "great britain": "UK",
    "pakistan": "PK",
    "pk": "PK",
    "australia": "AU",
    "au": "AU",
    "canada": "CA",
    "ca": "CA",
}


def _preferred_country(location: str) -> str | None:
    return _COUNTRY_CODES.get((location or "").strip().casefold())


def normalize_instagram_username(raw: str) -> str | None:
    """Strip @, URLs, and trailing slashes to a bare Instagram handle."""
    s = (raw or "").strip()
    if not s:
        return None
    # URL forms: instagram.com/handle[/...]
    lower = s.casefold()
    for prefix in (
        "https://www.instagram.com/",
        "http://www.instagram.com/",
        "https://instagram.com/",
        "http://instagram.com/",
        "www.instagram.com/",
        "instagram.com/",
    ):
        if lower.startswith(prefix):
            s = s[len(prefix) :]
            break
    s = s.lstrip("@").strip().strip("/")
    # Drop query/fragment and any remaining path segment after handle
    for sep in ("?", "#", "/"):
        if sep in s:
            s = s.split(sep, 1)[0]
    s = s.strip()
    return s or None


def normalize_twitter_username(raw: str) -> str | None:
    """Strip @, x.com/twitter.com URLs to a bare X/Twitter handle."""
    s = (raw or "").strip()
    if not s:
        return None
    lower = s.casefold()
    for prefix in (
        "https://www.x.com/",
        "http://www.x.com/",
        "https://x.com/",
        "http://x.com/",
        "www.x.com/",
        "x.com/",
        "https://www.twitter.com/",
        "http://www.twitter.com/",
        "https://twitter.com/",
        "http://twitter.com/",
        "www.twitter.com/",
        "twitter.com/",
    ):
        if lower.startswith(prefix):
            s = s[len(prefix) :]
            break
    s = s.lstrip("@").strip().strip("/")
    for sep in ("?", "#", "/"):
        if sep in s:
            s = s.split(sep, 1)[0]
    s = s.strip()
    return s or None


def build_twitter_actor_input(
    keywords: list[str],
    location: str,
    max_leads: int,
    *,
    min_followers: int | None = None,
    max_followers: int | None = None,
    seed_usernames: list[str] | None = None,
    require_email: bool = False,
    exclude_selling: bool = False,
    bio_exclude_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Build input for maximedupre~twitter-lead-scraper (leadProfiles).

    Keyword lines: first non-empty = keywordQuery; remaining = bioIncludeTerms.
    Seeds enrich via accountReferences when no keywords (keyword takes precedence
    when both present — mixed mode is not used).
    """
    from app.filters import DEFAULT_EXCLUDE_BIO_KEYWORDS

    kw_lines = [k.strip() for k in (keywords or []) if k and k.strip()]
    seeds: list[str] = []
    seen: set[str] = set()
    for raw in seed_usernames or []:
        handle = normalize_twitter_username(raw)
        if not handle:
            continue
        key = handle.casefold()
        if key in seen:
            continue
        seen.add(key)
        # Actor accepts @handle or bare handle
        seeds.append(f"@{handle}")

    max_candidates = max(1, min(int(max_leads or 50), 500))
    loc = (location or "").strip()

    payload: dict[str, Any] = {
        "resultType": "leadProfiles",
        "requireEmail": bool(require_email),
        "requireWebsite": False,
        "bioIncludeTerms": [],
        "bioExcludeTerms": [],
        "locationContains": loc,
    }
    if min_followers is not None:
        payload["minFollowers"] = int(min_followers)
    if max_followers is not None:
        payload["maxFollowers"] = int(max_followers)

    if exclude_selling:
        excludes = list(bio_exclude_terms) if bio_exclude_terms is not None else list(
            DEFAULT_EXCLUDE_BIO_KEYWORDS
        )
        payload["bioExcludeTerms"] = excludes

    if kw_lines:
        # Keyword discovery takes precedence over seeds when both present
        keyword_query = kw_lines[0]
        bio_include = kw_lines[1:]
        payload["profileSearch"] = {
            "findProfilesBy": "keywordQuery",
            "keywordQuery": keyword_query,
            "maxCandidates": max_candidates,
        }
        payload["bioIncludeTerms"] = bio_include
    elif seeds:
        payload["profileSearch"] = {
            "findProfilesBy": "accountReferences",
            "accountReferences": seeds,
            "maxCandidates": max_candidates,
        }
    else:
        # Caller should validate; empty search still needs a shape
        payload["profileSearch"] = {
            "findProfilesBy": "keywordQuery",
            "keywordQuery": "",
            "maxCandidates": max_candidates,
        }

    return payload


def build_actor_input(
    keywords: list[str],
    hashtags: list[str],
    location: str,
    max_leads: int,
    actor_id: str | None = None,
    *,
    min_followers: int | None = None,
    max_followers: int | None = None,
    seed_usernames: list[str] | None = None,
    seed_discovery: bool = False,
    platform: str = "instagram",
    require_email: bool | None = None,
    exclude_selling: bool = False,
) -> dict[str, Any]:
    """Build input for Instagram or Twitter/X creator lead scrapers.

    When ``platform`` is ``twitter``, delegates to ``build_twitter_actor_input``.
    Instagram path unchanged: strict schema for the default creator-leads actor.
    Mapped country locations use ``preferredCountry``; other locations are
    appended to terms. Seeds become ``usernames``; ``seed_discovery`` expands
    to related creators.
    """
    plat = (platform or "instagram").strip().lower()
    if plat in ("twitter", "x"):
        return build_twitter_actor_input(
            keywords,
            location,
            max_leads,
            min_followers=min_followers,
            max_followers=max_followers,
            seed_usernames=seed_usernames,
            require_email=bool(require_email) if require_email is not None else False,
            exclude_selling=bool(exclude_selling),
        )

    loc = (location or "").strip()
    country = _preferred_country(loc)
    search_terms: list[str] = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        search_terms.append(f"{kw} {loc}".strip() if loc and not country else kw)

    tags: list[str] = []
    for h in hashtags:
        h = h.strip().lstrip("#")
        if h:
            tags.append(h)

    seeds: list[str] = []
    seen: set[str] = set()
    for raw in seed_usernames or []:
        handle = normalize_instagram_username(raw)
        if not handle:
            continue
        key = handle.casefold()
        if key in seen:
            continue
        seen.add(key)
        seeds.append(handle)

    max_leads = max(1, min(int(max_leads or 50), 500))
    per_term = max(10, min(60, max_leads))
    max_profiles_scanned = max(200, min(2000, max_leads * 8))

    aid = actor_to_api_id(actor_id or DEFAULT_ACTOR)

    # Strict schema for the default creator-leads actor. Do not add
    # resultsLimit/maxItems here: the actor rejects unknown properties.
    if aid == DEFAULT_ACTOR or "instagram-creator-leads-scraper" in aid:
        payload: dict[str, Any] = {
            "maxLeads": max_leads,
            "maxLeadsPerSearchTerm": per_term,
            "maxProfilesScanned": max_profiles_scanned,
            "requireBusinessOrCreator": False,
            "excludePrivateAccounts": True,
            "deduplicateProfiles": True,
            "analysisDepth": "profileOnly",
            "contactRequirement": "any",
        }
        if search_terms:
            payload["searchTerms"] = search_terms
        if tags:
            payload["hashtags"] = tags
        if seeds:
            payload["usernames"] = seeds
            if seed_discovery:
                payload["seedDiscovery"] = True
        if min_followers is not None:
            payload["minFollowers"] = int(min_followers)
        if max_followers is not None:
            payload["maxFollowers"] = int(max_followers)
        if country:
            payload["preferredCountry"] = country
        return payload

    # Fallback for other actors — keep payloads conservative
    payload = {"maxItems": max_leads}
    if search_terms:
        payload["search"] = search_terms[0] if len(search_terms) == 1 else search_terms
    if tags:
        payload["hashtags"] = tags
    if seeds:
        payload["usernames"] = seeds
    return payload
