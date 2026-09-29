"""Normalize + filter Instagram creator leads for partnership outreach.

Vendored/adapted from creator-finder/filter_creators.py — no runtime dependency
on that folder.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

# Default keyword lists: creators already selling digital products
DEFAULT_EXCLUDE_BIO_KEYWORDS: list[str] = [
    "course",
    "ebook",
    "e-book",
    "digistore",
    "stan.store",
    "gumroad",
    "shop my",
    "shopmy",
    "my course",
    "join my",
    "paid community",
    "whop.com",
    "whop",
    "teachable",
    "kajabi",
    "payhip",
    "digital product",
    "buy my",
]

DEFAULT_EXCLUDE_URL_KEYWORDS: list[str] = [
    "gumroad.com",
    "stan.store",
    "teachable.com",
    "kajabi.com",
    "digistore24",
    "digistore",
    "payhip.com",
    "whop.com",
    "shopmy.us",
    "shopmy",
]

FIELD_MAP = {
    "username": ["username", "userName", "ownerUsername", "handle", "instagramUsername", "screenName", "twitterUsername"],
    "full_name": ["full_name", "fullName", "name", "displayName"],
    "biography": ["biography", "bio", "biographyText", "description"],
    "followers": ["followersCount", "followers", "followerCount", "followers_count"],
    "following": ["followingCount", "following", "followsCount"],
    "posts": ["postsCount", "posts", "mediaCount", "posts_count", "statusesCount", "tweetsCount"],
    "email": [
        "email",
        "businessEmail",
        "business_email",
        "publicEmail",
        "public_email",
        "contactEmail",
        "primaryEmail",
    ],
    "external_url": ["externalUrl", "external_url", "website", "bioLink", "websiteUrl"],
    "engagement_rate": ["engagementRate", "engagement_rate", "er", "avgEngagementRate"],
    "is_verified": ["isVerified", "verified", "is_verified"],
    "is_private": ["private", "isPrivate", "is_private"],
    "category": ["businessCategoryName", "categoryName", "category"],
    "profile_url": ["profileUrl", "profile_url", "inputUrl"],
}


def first(row: dict[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    lower = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] not in (None, ""):
            return lower[k.lower()]
    return None


def to_float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return default


def to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def extract_email(explicit: Any, bio: str, external: str) -> str:
    if explicit and EMAIL_RE.fullmatch(str(explicit).strip()):
        return str(explicit).strip()
    blob = f"{bio or ''} {external or ''}"
    m = EMAIL_RE.search(blob)
    return m.group(0) if m else ""


def _row_looks_twitter(row: dict[str, Any], platform: str | None = None) -> bool:
    plat = (platform or "").strip().lower()
    if plat in ("twitter", "x"):
        return True
    if plat == "instagram":
        return False
    rt = str(row.get("recordType") or "").casefold()
    if rt == "leadprofile":
        return True
    profile = str(
        first(row, FIELD_MAP["profile_url"]) or row.get("profileUrl") or row.get("url") or ""
    ).casefold()
    return "x.com/" in profile or "twitter.com/" in profile


def normalize_row(row: dict[str, Any], platform: str | None = None) -> dict[str, Any]:
    username = first(row, FIELD_MAP["username"]) or ""
    username = str(username).lstrip("@").strip()
    bio = str(first(row, FIELD_MAP["biography"]) or "")
    external = str(first(row, FIELD_MAP["external_url"]) or "")
    email = extract_email(first(row, FIELD_MAP["email"]), bio, external)
    followers = int(to_float(first(row, FIELD_MAP["followers"])))
    profile = first(row, FIELD_MAP["profile_url"])
    is_twitter = _row_looks_twitter(row, platform)
    if not profile and username:
        if is_twitter:
            profile = f"https://x.com/{username}"
        else:
            profile = f"https://www.instagram.com/{username}/"
    # Avoid treating profile URL as external website when actor reuses "url"
    if external and username:
        ext_l = external.lower().replace("www.", "")
        handle = username.lower()
        if "instagram.com/" + handle in ext_l or "x.com/" + handle in ext_l or "twitter.com/" + handle in ext_l:
            external = ""
    followers_raw = first(row, FIELD_MAP["followers"])
    posts = row.get("latestPosts") if isinstance(row.get("latestPosts"), list) else []
    er = to_float(first(row, FIELD_MAP["engagement_rate"]))
    if not er and posts and followers:
        er = engagement_from_posts(posts, followers)
    last_post = latest_post_time(posts)
    error = row.get("error") or row.get("errorCode")
    return {
        "username": username,
        "full_name": str(first(row, FIELD_MAP["full_name"]) or ""),
        "biography": bio,
        "followers": followers,
        "following": int(to_float(first(row, FIELD_MAP["following"]))),
        "posts": int(to_float(first(row, FIELD_MAP["posts"]))),
        "email": email,
        "external_url": external,
        "engagement_rate": er,
        "is_verified": to_bool(first(row, FIELD_MAP["is_verified"])),
        "profile_url": str(profile or ""),
        # Extra signals (mainly from apify/instagram-profile-scraper)
        "scrape_error": str(error) if error and followers_raw in (None, "", 0) else "",
        "is_private": to_bool(first(row, FIELD_MAP["is_private"])),
        "category": str(first(row, FIELD_MAP["category"]) or ""),
        "last_post_at": last_post.isoformat() if last_post else "",
        "days_since_post": (
            (datetime.now(timezone.utc) - last_post).days if last_post else None
        ),
        "post_locations": sorted(
            {str(p.get("locationName")) for p in posts if p.get("locationName")}
        ),
        "related_usernames": [
            str(r.get("username"))
            for r in (row.get("relatedProfiles") or [])
            if isinstance(r, dict) and r.get("username")
        ],
        "external_urls": _all_external_urls(row, external),
        "link_emails": [],
        "link_selling": [],
        "location_match": "",
        "score": 0,
    }


def _all_external_urls(row: dict[str, Any], primary: str) -> list[str]:
    urls: list[str] = [primary] if primary else []
    for item in row.get("externalUrls") or []:
        u = item.get("url") if isinstance(item, dict) else item
        if u and str(u) not in urls:
            urls.append(str(u))
    return urls


def _parse_ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return None


def latest_post_time(posts: list[dict[str, Any]]) -> datetime | None:
    times = [t for t in (_parse_ts(p.get("timestamp")) for p in posts) if t]
    return max(times) if times else None


def engagement_from_posts(posts: list[dict[str, Any]], followers: int) -> float:
    """Average (likes + comments) per recent post as % of followers.

    Posts with hidden likes (Instagram reports -1) are skipped.
    """
    per_post = []
    for p in posts[:12]:
        likes = to_float(p.get("likesCount"), -1)
        comments = max(0.0, to_float(p.get("commentsCount"), 0))
        if likes < 0:
            continue
        per_post.append(likes + comments)
    if not per_post or followers <= 0:
        return 0.0
    return round(sum(per_post) / len(per_post) / followers * 100, 2)


# Extra words that count as a location match for common countries.
_LOCATION_ALIASES: dict[str, list[str]] = {
    "united states": [
        "usa", "u.s.", "america", "🇺🇸", "alabama", "alaska", "arizona", "arkansas",
        "california", "colorado", "connecticut", "delaware", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
        "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
        "new jersey", "new mexico", "new york", "north carolina", "north dakota",
        "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
        "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
        "virginia", "washington", "west virginia", "wisconsin", "wyoming", "nyc",
        "los angeles", "chicago", "houston", "dallas", "austin", "atlanta",
        "miami", "seattle", "boston", "denver", "phoenix", "nashville", "san diego",
    ],
    "united kingdom": [
        "uk", "u.k.", "england", "scotland", "wales", "britain", "🇬🇧", "london",
        "manchester", "birmingham", "leeds", "liverpool", "bristol", "glasgow",
        "edinburgh",
    ],
    "canada": ["🇨🇦", "toronto", "vancouver", "montreal", "calgary", "ottawa",
               "ontario", "alberta", "quebec", "british columbia"],
    "australia": ["🇦🇺", "aussie", "sydney", "melbourne", "brisbane", "perth",
                  "adelaide", "queensland", "nsw", "victoria"],
    "pakistan": ["🇵🇰", "karachi", "lahore", "islamabad", "rawalpindi", "faisalabad"],
    "india": ["🇮🇳", "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad",
              "chennai", "kolkata", "pune"],
    "united arab emirates": ["uae", "🇦🇪", "dubai", "abu dhabi", "sharjah"],
}
_LOCATION_SYNONYMS = {
    "usa": "united states", "us": "united states", "united states of america": "united states",
    "america": "united states", "uk": "united kingdom", "england": "united kingdom",
    "great britain": "united kingdom", "uae": "united arab emirates", "dubai": "united arab emirates",
}


def location_terms(location: str) -> list[str]:
    """Words that indicate a creator is in ``location`` (comma-separated input)."""
    terms: list[str] = []
    for part in (location or "").split(","):
        p = part.strip().casefold()
        if not p:
            continue
        terms.append(p)
        canon = _LOCATION_SYNONYMS.get(p, p)
        if canon != p:
            terms.append(canon)
        terms.extend(_LOCATION_ALIASES.get(canon, []))
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def match_location(lead: dict[str, Any], terms: list[str]) -> str:
    """Return the first location term found in bio / name / post locations."""
    if not terms:
        return ""
    blob = " ".join(
        [lead.get("biography") or "", lead.get("full_name") or ""]
        + list(lead.get("post_locations") or [])
    ).casefold()
    for t in terms:
        if len(t) <= 3 and t.isalpha():
            # short codes like "uk"/"usa"/"nyc" must be whole words
            if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", blob):
                return t
        elif t in blob:
            return t
    return ""


def score_lead(lead: dict[str, Any]) -> int:
    """0–100 outreach priority. Higher = contact first."""
    s = 0
    if lead.get("email"):
        s += 30
    er = lead.get("engagement_rate") or 0
    s += 25 if er >= 3 else 18 if er >= 1.5 else 10 if er >= 0.8 else 0
    days = lead.get("days_since_post")
    if days is not None:
        s += 15 if days <= 14 else 8 if days <= 45 else 0
    if lead.get("location_match"):
        s += 10
    hits = int(lead.get("source_hits") or 0)
    s += min(15, 5 * hits)
    if lead.get("external_url"):
        s += 5
    return min(100, s)


def contains_any(text: str, keywords: list[str]) -> str | None:
    t = (text or "").lower()
    for kw in keywords or []:
        if kw and kw.lower() in t:
            return kw
    return None


def contains_word(text: str, keywords: list[str]) -> str | None:
    """Like contains_any but matches whole words, so 'course' doesn't hit
    'of course' and 'whop' doesn't hit 'whopping'."""
    t = (text or "").lower().replace("of course", " ")
    for kw in keywords or []:
        k = (kw or "").lower().strip()
        if k and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", t):
            return kw
    return None


def evaluate(lead: dict[str, Any], filters: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason). reason is 'ok' when accepted."""
    f = filters
    if lead.get("scrape_error"):
        return False, f"profile_unavailable:{lead['scrape_error']}"[:60]
    if lead.get("is_private"):
        return False, "private"
    followers = lead["followers"]
    min_f = int(f.get("min_followers", 0))
    max_f = int(f.get("max_followers", 10**12))
    if followers < min_f:
        return False, f"followers<{min_f}"
    if followers > max_f:
        return False, f"followers>{max_f}"
    active_days = int(f.get("active_within_days") or 0)
    if active_days > 0 and lead.get("days_since_post") is not None:
        if lead["days_since_post"] > active_days:
            return False, f"inactive>{active_days}d"
    if f.get("require_email") and not lead["email"]:
        return False, "no_email"
    if f.get("require_external_url") and not lead["external_url"]:
        return False, "no_external_url"
    if f.get("exclude_verified") and lead["is_verified"]:
        return False, "verified"
    min_er = float(f.get("min_engagement_rate") or 0)
    if min_er > 0 and lead["engagement_rate"] > 0 and lead["engagement_rate"] < min_er:
        return False, f"engagement<{min_er}"
    # Also reject if ER is required but missing/zero when min_er > 0 and we want strict?
    # Spec: 0 = off; if set and lead has ER below threshold, reject. Missing ER passes.
    exclude_selling = f.get("exclude_selling_digital", True)
    bio_kws = f.get("exclude_bio_keywords")
    url_kws = f.get("exclude_url_keywords")
    if bio_kws is None and exclude_selling:
        bio_kws = DEFAULT_EXCLUDE_BIO_KEYWORDS
    if url_kws is None and exclude_selling:
        url_kws = DEFAULT_EXCLUDE_URL_KEYWORDS
    if not exclude_selling:
        bio_kws = bio_kws or []
        url_kws = url_kws or []
    hit = contains_word(lead["biography"], bio_kws or [])
    if hit:
        return False, f"bio_monetized:{hit}"
    for url in lead.get("external_urls") or [lead["external_url"]]:
        hit = contains_any(url, url_kws or [])
        if hit:
            return False, f"url_monetized:{hit}"
    if exclude_selling and lead.get("link_selling"):
        return False, f"link_monetized:{lead['link_selling'][0]}"
    if f.get("location_strict") and f.get("location_terms") and not lead.get("location_match"):
        return False, "location_unconfirmed"
    include = f.get("include_bio_keywords") or []
    if include and not contains_word(lead["biography"], include):
        return False, "bio_off_niche"
    if not lead["username"]:
        return False, "no_username"
    return True, "ok"


def filter_leads(
    raw_rows: list[dict[str, Any]],
    filters: dict[str, Any],
    niche: str = "",
    platform: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Normalize, de-dupe, evaluate. Returns (qualified, rejected, reason_counts)."""
    leads = [normalize_row(r, platform=platform) for r in raw_rows]
    return evaluate_leads(dedupe(leads), filters, niche=niche)


def dedupe(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for lead in leads:
        u = lead["username"].lower()
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(lead)
    return unique


def evaluate_leads(
    leads: list[dict[str, Any]],
    filters: dict[str, Any],
    niche: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Evaluate already-normalized leads; qualified come back best-first."""
    terms = filters.get("location_terms") or []
    qualified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for lead in leads:
        if terms and not lead.get("location_match"):
            lead["location_match"] = match_location(lead, terms)
        ok, reason = evaluate(lead, filters)
        row = {**lead, "niche": niche, "filter_reason": reason}
        row["score"] = score_lead(row)
        (qualified if ok else rejected).append(row)
    qualified.sort(key=lambda r: (-r["score"], -r["followers"]))
    # Group all scraper errors under one bucket in the summary
    reason_counts = dict(
        Counter(
            "profile_unavailable" if r["filter_reason"].startswith("profile_unavailable")
            else r["filter_reason"]
            for r in rejected
        )
    )
    return qualified, rejected, reason_counts


def to_manyreach(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        first_name = (r.get("full_name") or "").split()[0] if r.get("full_name") else r["username"]
        out.append(
            {
                "email": r["email"],
                "first_name": first_name,
                "instagram_username": r["username"],
                "instagram_url": r["profile_url"],
                "followers": r["followers"],
                "bio": (r.get("biography") or "")[:500],
                "website": r.get("external_url") or "",
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(
                {k: " | ".join(map(str, v)) if isinstance(v, list) else v for k, v in r.items()}
            )


def write_exports(
    job_dir: Path,
    qualified: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> dict[str, str]:
    """Write qualified / manyreach / rejected CSVs + raw JSON lists. Returns relative names."""
    job_dir.mkdir(parents=True, exist_ok=True)
    q_fields = [
        "score",
        "username",
        "full_name",
        "email",
        "followers",
        "engagement_rate",
        "days_since_post",
        "location_match",
        "category",
        "biography",
        "external_url",
        "profile_url",
        "found_via",
        "link_selling",
        "niche",
        "filter_reason",
    ]
    write_csv(job_dir / "qualified.csv", qualified, q_fields)
    write_csv(job_dir / "rejected.csv", rejected, q_fields)
    write_csv(
        job_dir / "manyreach.csv",
        to_manyreach(qualified),
        [
            "email",
            "first_name",
            "instagram_username",
            "instagram_url",
            "followers",
            "bio",
            "website",
        ],
    )
    (job_dir / "qualified.json").write_text(
        json.dumps(qualified, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (job_dir / "rejected.json").write_text(
        json.dumps(rejected, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "qualified_csv": "qualified.csv",
        "rejected_csv": "rejected.csv",
        "manyreach_csv": "manyreach.csv",
    }
