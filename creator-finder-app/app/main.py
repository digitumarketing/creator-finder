"""Creator Finder — FastAPI app (localhost only by default)."""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import apify_client, keystore
from app.apify_client import ApifyAuthError, ApifyError
from app.db import JOBS_DIR, init_db, get_session
from app.jobs import create_job, parse_lines, resume_orphaned_jobs, schedule_job
from app.models import Job
from app.pipeline import DEFAULT_OPTIONS, merged_options

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Never attach token values to log records
logging.getLogger("httpx").setLevel(logging.WARNING)

APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()  # also wipes any API keys an older version saved to the database
    n = keystore.load_from_env()
    if n:
        logging.getLogger("creator_finder").info("Loaded %s Apify key(s) from APIFY_TOKENS", n)
    await resume_orphaned_jobs()
    yield


app = FastAPI(title="Creator Finder", lifespan=lifespan)

# Optional login for when the app runs somewhere other than your own computer.
# Set APP_PASSWORD (and optionally APP_USERNAME, default "admin").
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_APP_USERNAME = os.environ.get("APP_USERNAME", "admin")


@app.middleware("http")
async def require_password(request: Request, call_next):
    if not _APP_PASSWORD or request.url.path == "/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            user, pw = "", ""
        if secrets.compare_digest(user, _APP_USERNAME) and secrets.compare_digest(
            pw, _APP_PASSWORD
        ):
            return await call_next(request)
    return Response(
        "Login required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Creator Finder"'},
    )
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _mask(token: str) -> str:
    t = token or ""
    if len(t) <= 8:
        return "••••••••"
    prefix = t[:6] if t.startswith("apify_") else t[:4]
    return f"{prefix}…{t[-4:]}"


def _fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Present in a readable local-ish ISO (user runs locally)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


templates.env.globals["mask_token"] = _mask
templates.env.globals["fmt_dt"] = _fmt_dt


# ── Scrape (home) ──────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
@app.get("/scrape", response_class=HTMLResponse)
async def scrape_page(request: Request):
    key_count = len(keystore.usable())
    total_keys = len(keystore.all_keys())
    return templates.TemplateResponse(
        request,
        "scrape.html",
        {
            "key_count": key_count,
            "total_keys": total_keys,
            "default_actor": apify_client.DEFAULT_ACTOR,
            "default_instagram_actor": apify_client.DEFAULT_ACTOR,
            "default_twitter_actor": apify_client.DEFAULT_TWITTER_ACTOR,
            "opts": DEFAULT_OPTIONS,
            "error": None,
        },
    )


@app.post("/scrape")
async def scrape_submit(
    request: Request,
    niche: str = Form(""),
    platform: str = Form("instagram"),
    keywords: str = Form(""),
    hashtags: str = Form(""),
    seed_usernames: str = Form(""),
    seed_discovery: Optional[str] = Form(None),
    location: str = Form(""),
    min_followers: int = Form(10000),
    max_followers: int = Form(100000),
    require_email: Optional[str] = Form(None),
    exclude_selling: Optional[str] = Form(None),
    min_engagement_rate: float = Form(0),
    max_leads: int = Form(50),
    actor_id: str = Form(""),
    engine: str = Form("pipeline"),
    posts_per_hashtag: int = Form(150),
    accounts_per_keyword: int = Form(50),
    max_profiles: int = Form(300),
    active_within_days: int = Form(60),
    expand_related: Optional[str] = Form(None),
    check_links: Optional[str] = Form(None),
    location_strict: Optional[str] = Form(None),
    skip_seen: Optional[str] = Form(None),
):
    plat = (platform or "instagram").strip().lower()
    if plat in ("x", "twitter", "twitter/x"):
        plat = "twitter"
    else:
        plat = "instagram"
    default_actor = (
        apify_client.DEFAULT_TWITTER_ACTOR if plat == "twitter" else apify_client.DEFAULT_ACTOR
    )

    def _tpl_extras():
        return {
            "default_actor": default_actor,
            "default_instagram_actor": apify_client.DEFAULT_ACTOR,
            "default_twitter_actor": apify_client.DEFAULT_TWITTER_ACTOR,
            "opts": DEFAULT_OPTIONS,
        }

    def _form_ctx(**extra):
        base = {
            "niche": niche,
            "platform": plat,
            "keywords": keywords,
            "hashtags": hashtags,
            "seed_usernames": seed_usernames,
            "seed_discovery": seed_discovery is not None,
            "location": location,
            "engine": engine,
        }
        base.update(extra)
        return base

    session = get_session()
    try:
        key_count = len([k for k in keystore.all_keys() if k.enabled])
        if key_count == 0:
            return templates.TemplateResponse(
                request,
                "scrape.html",
                {
                    "key_count": 0,
                    "total_keys": 0,
                    "error": "Add at least one Apify API key before scraping.",
                    "form": _form_ctx(),
                    **_tpl_extras(),
                },
                status_code=400,
            )

        kw = parse_lines(keywords)
        tags = parse_lines(hashtags) if plat == "instagram" else []
        seeds_raw = parse_lines(seed_usernames)
        seeds: list[str] = []
        seen: set[str] = set()
        norm = (
            apify_client.normalize_twitter_username
            if plat == "twitter"
            else apify_client.normalize_instagram_username
        )
        for raw in seeds_raw:
            handle = norm(raw)
            if not handle:
                continue
            key = handle.casefold()
            if key in seen:
                continue
            seen.add(key)
            seeds.append(handle)

        if plat == "twitter":
            if not kw and not seeds:
                return templates.TemplateResponse(
                    request,
                    "scrape.html",
                    {
                        "key_count": key_count,
                        "total_keys": len(keystore.all_keys()),
                        "error": "Provide at least one keyword or X/Twitter handle.",
                        "form": _form_ctx(),
                        **_tpl_extras(),
                    },
                    status_code=400,
                )
        elif not kw and not tags and not seeds:
            return templates.TemplateResponse(
                request,
                "scrape.html",
                {
                    "key_count": key_count,
                    "total_keys": len(keystore.all_keys()),
                    "error": "Provide at least one keyword, hashtag, or seed username.",
                    "form": _form_ctx(),
                    **_tpl_extras(),
                },
                status_code=400,
            )

        max_leads = max(1, min(int(max_leads), 200))
        use_pipeline = plat == "instagram" and engine != "legacy"
        options = (
            merged_options(
                {
                    "posts_per_hashtag": posts_per_hashtag,
                    "accounts_per_keyword": accounts_per_keyword,
                    "max_profiles": max_profiles,
                    "active_within_days": active_within_days,
                    "expand_related": expand_related is not None,
                    "check_links": check_links is not None,
                    "location_strict": location_strict is not None,
                    "skip_seen": skip_seen is not None,
                }
            )
            if use_pipeline
            else {}
        )
        # Checkbox defaults ON in the template; only honor when seeds are present.
        # Seed discovery is Instagram-only.
        do_seed_discovery = (
            plat == "instagram" and bool(seeds) and (seed_discovery is not None)
        )
        job = create_job(
            session,
            niche=niche,
            keywords=kw,
            hashtags=tags,
            location=location,
            min_followers=min_followers,
            max_followers=max_followers,
            require_email=require_email is not None,
            exclude_selling=exclude_selling is not None,
            min_engagement_rate=min_engagement_rate,
            max_leads=max_leads,
            actor_id=actor_id or default_actor,
            seed_usernames=seeds,
            seed_discovery=do_seed_discovery,
            platform=plat,
            engine="pipeline" if use_pipeline else "legacy",
            options=options,
        )
        job_id = job.id
    finally:
        session.close()

    schedule_job(job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


# ── Keys ───────────────────────────────────────────────────────


@app.get("/keys", response_class=HTMLResponse)
async def keys_page(request: Request, msg: Optional[str] = None, err: Optional[str] = None):
    rows = [
        {
            "id": k.id,
            "label": k.label,
            "masked": k.masked_token(),
            "enabled": k.enabled,
            "is_bad": k.is_bad,
            "source": k.source,
            "last_tested_at": k.last_tested_at,
            "last_test_ok": k.last_test_ok,
            "last_test_username": k.last_test_username,
            "last_test_plan": k.last_test_plan,
            "last_test_error": k.last_test_error,
            "created_at": k.created_at,
        }
        for k in keystore.all_keys()
    ]
    return templates.TemplateResponse(
        request,
        "keys.html",
        {"keys": rows, "msg": msg, "err": err},
    )


@app.post("/keys/add")
async def keys_add(label: str = Form(...), token: str = Form(...)):
    label = label.strip()
    token = token.strip()
    if not label or not token:
        return RedirectResponse(url="/keys?err=Label+and+token+required", status_code=303)
    keystore.add(label, token)
    return RedirectResponse(url="/keys?msg=Key+added+(kept+in+memory+only)", status_code=303)


@app.post("/keys/{key_id}/toggle")
async def keys_toggle(key_id: int):
    k = keystore.get(key_id)
    if not k:
        raise HTTPException(404)
    k.enabled = not k.enabled
    if k.enabled:
        k.is_bad = False  # give it another chance
    return RedirectResponse(url="/keys?msg=Updated", status_code=303)


@app.post("/keys/{key_id}/delete")
async def keys_delete(key_id: int):
    keystore.delete(key_id)
    return RedirectResponse(url="/keys?msg=Deleted", status_code=303)


@app.post("/keys/{key_id}/test")
async def keys_test(key_id: int):
    k = keystore.get(key_id)
    if not k:
        raise HTTPException(404)
    err_msg = None
    username = plan = None
    try:
        info = await apify_client.test_token(k.token)
        ok = True
        username = info["username"]
        plan = info["plan"]
    except (ApifyAuthError, ApifyError) as e:
        ok = False
        err_msg = str(e)
    except Exception as e:
        ok = False
        err_msg = f"Test failed: {e}"

    k.last_tested_at = datetime.now(timezone.utc)
    k.last_test_ok = ok
    k.last_test_username = username
    k.last_test_plan = plan
    k.last_test_error = err_msg
    if not ok and isinstance(err_msg, str) and "Invalid" in err_msg:
        k.is_bad = True
    elif ok:
        k.is_bad = False

    q = "msg=Test+OK" if ok else f"err={err_msg or 'Test failed'}"
    return RedirectResponse(url=f"/keys?{q}", status_code=303)


@app.post("/keys/{key_id}/clear-bad")
async def keys_clear_bad(key_id: int):
    k = keystore.get(key_id)
    if k:
        k.is_bad = False
    return RedirectResponse(url="/keys?msg=Cleared+bad+flag", status_code=303)


# ── Jobs ────────────────────────────────────────────────────────


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request):
    session = get_session()
    try:
        jobs = session.query(Job).order_by(Job.id.desc()).limit(100).all()
        rows = [
            {
                "id": j.id,
                "niche": j.niche,
                "status": j.status,
                "platform": getattr(j, "platform", None) or "instagram",
                "qualified_count": j.qualified_count,
                "raw_count": j.raw_count,
                "api_key_label": j.api_key_label,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
                "error_message": j.error_message,
            }
            for j in jobs
        ]
    finally:
        session.close()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": rows},
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    session = get_session()
    try:
        j = session.get(Job, job_id)
        if not j:
            raise HTTPException(404, "Job not found")
        leads = json.loads(j.results_json) if j.results_json else []
        reject_summary = json.loads(j.reject_summary_json) if j.reject_summary_json else {}
        keywords = json.loads(j.keywords_json or "[]")
        hashtags = json.loads(j.hashtags_json or "[]")
        seed_usernames = json.loads(getattr(j, "seed_usernames_json", None) or "[]")
        seed_discovery = bool(getattr(j, "seed_discovery", False))
        auto_refresh = j.status in ("queued", "running")
        # Snapshot ORM fields before closing session
        job_view = SimpleNamespace(**{
            "id": j.id,
            "niche": j.niche,
            "status": j.status,
            "platform": getattr(j, "platform", None) or "instagram",
            "error_message": j.error_message,
            "raw_count": j.raw_count or 0,
            "qualified_count": j.qualified_count or 0,
            "rejected_count": j.rejected_count or 0,
            "api_key_label": j.api_key_label,
            "created_at": j.created_at,
            "started_at": j.started_at,
            "finished_at": j.finished_at,
            "actor_id": j.actor_id,
            "min_followers": j.min_followers,
            "max_followers": j.max_followers,
            "max_leads": j.max_leads,
            "require_email": j.require_email,
            "exclude_selling": j.exclude_selling,
            "location": j.location,
            "apify_run_id": j.apify_run_id,
            "seed_discovery": seed_discovery,
            "engine": getattr(j, "engine", None) or "legacy",
        })
        usage = json.loads(j.usage_json) if getattr(j, "usage_json", None) else {}
        options = json.loads(j.options_json) if getattr(j, "options_json", None) else {}
        ctx = {
            "job": job_view,
            "leads": leads,
            "reject_summary": reject_summary,
            "keywords": keywords,
            "hashtags": hashtags,
            "seed_usernames": seed_usernames,
            "progress_message": j.progress_message,
            "auto_refresh": auto_refresh,
            "usage": usage,
            "options": options,
        }
    finally:
        session.close()
    return templates.TemplateResponse(request, "job_detail.html", ctx)


@app.get("/jobs/{job_id}/download/{kind}")
async def job_download(job_id: int, kind: str):
    mapping = {
        "qualified": "qualified.csv",
        "manyreach": "manyreach.csv",
        "rejected": "rejected.csv",
        "raw": "raw.json",
    }
    if kind not in mapping:
        raise HTTPException(404)
    path = JOBS_DIR / str(job_id) / mapping[kind]
    if not path.exists():
        raise HTTPException(404, "File not ready")
    media = "application/json" if kind == "raw" else "text/csv"
    return FileResponse(
        path,
        media_type=media,
        filename=f"job{job_id}_{mapping[kind]}",
    )


@app.get("/health")
async def health():
    return {"ok": True}
