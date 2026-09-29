"""Normalize + filter Instagram creator leads for partnership outreach.

Vendored/adapted from creator-finder/filter_creators.py — no runtime dependency
on that folder.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
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
    "email": ["email", "businessEmail", "publicEmail", "contactEmail", "primaryEmail"],
    "external_url": ["externalUrl", "external_url", "website", "bioLink", "websiteUrl"],
    "engagement_rate": ["engagementRate", "engagement_rate", "er", "avgEngagementRate"],
    "is_verified": ["isVerified", "verified", "is_verified"],
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
    return {
        "username": username,
        "full_name": str(first(row, FIELD_MAP["full_name"]) or ""),
        "biography": bio,
        "followers": followers,
        "following": int(to_float(first(row, FIELD_MAP["following"]))),
        "posts": int(to_float(first(row, FIELD_MAP["posts"]))),
        "email": email,
        "external_url": external,
        "engagement_rate": to_float(first(row, FIELD_MAP["engagement_rate"])),
        "is_verified": to_bool(first(row, FIELD_MAP["is_verified"])),
        "profile_url": str(profile or ""),
    }


def contains_any(text: str, keywords: list[str]) -> str | None:
    t = (text or "").lower()
    for kw in keywords or []:
        if kw and kw.lower() in t:
            return kw
    return None


def evaluate(lead: dict[str, Any], filters: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason). reason is 'ok' when accepted."""
    f = filters
    followers = lead["followers"]
    min_f = int(f.get("min_followers", 0))
    max_f = int(f.get("max_followers", 10**12))
    if followers < min_f:
        return False, f"followers<{min_f}"
    if followers > max_f:
        return False, f"followers>{max_f}"
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
    hit = contains_any(lead["biography"], bio_kws or [])
    if hit:
        return False, f"bio_monetized:{hit}"
    hit = contains_any(lead["external_url"], url_kws or [])
    if hit:
        return False, f"url_monetized:{hit}"
    include = f.get("include_bio_keywords") or []
    if include and not contains_any(lead["biography"], include):
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
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for lead in leads:
        u = lead["username"].lower()
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(lead)

    qualified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for lead in unique:
        ok, reason = evaluate(lead, filters)
        row = {**lead, "niche": niche, "filter_reason": reason}
        (qualified if ok else rejected).append(row)

    reason_counts = dict(Counter(r["filter_reason"] for r in rejected))
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
            w.writerow(r)


def write_exports(
    job_dir: Path,
    qualified: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> dict[str, str]:
    """Write qualified / manyreach / rejected CSVs + raw JSON lists. Returns relative names."""
    job_dir.mkdir(parents=True, exist_ok=True)
    q_fields = [
        "username",
        "full_name",
        "email",
        "followers",
        "engagement_rate",
        "biography",
        "external_url",
        "profile_url",
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
