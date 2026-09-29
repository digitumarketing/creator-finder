"""Open creators' link-in-bio pages (Linktree, Beacons, websites) locally.

Most micro-creators don't put an email or "course" in the 150-char bio; it
lives behind the bio link. Fetching that page costs no Apify credit and finds
(a) contact emails and (b) storefronts that mean they already sell digital
products.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

import httpx

log = logging.getLogger("creator_finder.linkcheck")

MAX_BYTES = 400_000
TIMEOUT = 10.0
CONCURRENCY = 8
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
# Things that look like emails in page source but aren't contacts
_JUNK_EMAIL = re.compile(
    r"(\.(png|jpe?g|gif|webp|svg|js|css)$|@(sentry|example|email|domain|wixpress|"
    r"sentry-next|2x|3x)\.|@(linktr\.ee|beacons\.ai|stan\.store|instagram\.com)$|^(u00|x22)|noreply|no-reply)",
    re.I,
)

# Links on the page pointing at digital-product storefronts
SELLING_DOMAINS = [
    "stan.store",
    "gumroad.com",
    "payhip.com",
    "kajabi.com",
    "mykajabi.com",
    "teachable.com",
    "thinkific.com",
    "podia.com",
    "whop.com",
    "sellfy.com",
    "lemonsqueezy.com",
    "skool.com",
    "patreon.com",
    "ko-fi.com/s/",
    "digistore24",
    "samcart.com",
    "systeme.io",
    "clickfunnels.com",
    "beacons.ai/i/",
]
# Words in link titles / page text that indicate a paid digital product
SELLING_PHRASES = [
    "ebook",
    "e-book",
    "online course",
    "my course",
    "masterclass",
    "digital download",
    "digital guide",
    "printables",
    "buy now",
    "add to cart",
    "presets",
    "membership",
]


def _clean_emails(text: str) -> list[str]:
    found: list[str] = []
    for m in EMAIL_RE.findall(text):
        e = m.strip(".").lower()
        if _JUNK_EMAIL.search(e) or e in found:
            continue
        found.append(e)
    return found


def analyse_page(text: str) -> dict[str, list[str]]:
    """Pull contact emails and selling signals out of raw HTML/JSON."""
    t = html.unescape(text).replace("\\u0040", "@").replace("\\/", "/")
    low = t.lower()
    selling = [d for d in SELLING_DOMAINS if d in low]
    selling += [p for p in SELLING_PHRASES if re.search(rf"\b{re.escape(p)}\b", low)]
    mailtos = re.findall(r"mailto:([^\"'?>\s]+)", t, re.I)
    emails = _clean_emails(" ".join(mailtos) + " " + t)
    return {"emails": emails[:3], "selling": selling}


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    async with client.stream("GET", url) as r:
        if r.status_code >= 400:
            return ""
        ctype = r.headers.get("content-type", "")
        if ctype and "html" not in ctype and "json" not in ctype and "text" not in ctype:
            return ""
        chunks: list[bytes] = []
        size = 0
        async for chunk in r.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES:
                break
    return b"".join(chunks).decode("utf-8", errors="ignore")


async def check_links(leads: list[dict[str, Any]]) -> int:
    """Fill ``link_emails`` / ``link_selling`` (and ``email`` when empty) in place.

    Returns how many leads had at least one page fetched. Network errors on a
    single page are ignored — this is best-effort enrichment.
    """
    sem = asyncio.Semaphore(CONCURRENCY)
    checked = 0

    async def one(client: httpx.AsyncClient, lead: dict[str, Any]) -> None:
        nonlocal checked
        urls = [u for u in (lead.get("external_urls") or []) if u][:3]
        if not urls:
            return
        emails: list[str] = []
        selling: list[str] = []
        fetched = False
        async with sem:
            for url in urls:
                try:
                    text = await _fetch(client, url)
                except (httpx.HTTPError, UnicodeError, ValueError) as e:
                    log.debug("link check failed for %s: %s", url, e)
                    continue
                if not text:
                    continue
                fetched = True
                found = analyse_page(text)
                emails += [e for e in found["emails"] if e not in emails]
                selling += [s for s in found["selling"] if s not in selling]
        if fetched:
            checked += 1
        lead["link_emails"] = emails
        lead["link_selling"] = selling
        if not lead.get("email") and emails:
            lead["email"] = emails[0]
            lead["email_source"] = "link_in_bio"

    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
    ) as client:
        await asyncio.gather(*(one(client, lead) for lead in leads))
    return checked
