"""Background scrape job runner with round-robin Apify key failover."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app import apify_client, keystore
from app.apify_client import ApifyAuthError, ApifyError
from app.db import JOBS_DIR, get_session
from app.filters import filter_leads, write_exports
from app.models import Job

log = logging.getLogger("creator_finder.jobs")

# Round-robin cursor (in-memory; resets on restart which is fine)
_rr_index = 0
_running_tasks: dict[int, asyncio.Task] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pick_next_key() -> keystore.Key | None:
    """Pick next enabled, non-bad key in round-robin order."""
    global _rr_index
    keys = keystore.usable() or [k for k in keystore.all_keys() if k.enabled]
    if not keys:
        return None
    idx = _rr_index % len(keys)
    _rr_index = idx + 1
    return keys[idx]


def parse_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def create_job(
    session,
    *,
    niche: str,
    keywords: list[str],
    hashtags: list[str],
    location: str,
    min_followers: int,
    max_followers: int,
    require_email: bool,
    exclude_selling: bool,
    min_engagement_rate: float,
    max_leads: int,
    actor_id: str,
    seed_usernames: list[str] | None = None,
    seed_discovery: bool = False,
    platform: str = "instagram",
    engine: str = "legacy",
    options: dict[str, Any] | None = None,
) -> Job:
    max_leads = max(1, min(int(max_leads), 200))
    seeds = list(seed_usernames or [])
    plat = (platform or "instagram").strip().lower()
    if plat in ("x", "twitter", "twitter/x"):
        plat = "twitter"
    else:
        plat = "instagram"
    default_actor = (
        apify_client.DEFAULT_TWITTER_ACTOR if plat == "twitter" else apify_client.DEFAULT_ACTOR
    )
    job = Job(
        niche=niche.strip() or "untitled",
        status="queued",
        platform=plat,
        keywords_json=json.dumps(keywords),
        hashtags_json=json.dumps(hashtags),
        seed_usernames_json=json.dumps(seeds),
        seed_discovery=bool(seed_discovery) if seeds else False,
        location=(location or "").strip(),
        min_followers=int(min_followers),
        max_followers=int(max_followers),
        require_email=bool(require_email),
        exclude_selling=bool(exclude_selling),
        min_engagement_rate_str=str(min_engagement_rate),
        max_leads=max_leads,
        actor_id=actor_id.strip() or default_actor,
        engine=engine if plat == "instagram" else "legacy",
        options_json=json.dumps(options or {}),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def schedule_job(job_id: int) -> None:
    """Fire-and-forget background task for a job."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.error("No running event loop; cannot schedule job %s", job_id)
        return
    if job_id in _running_tasks and not _running_tasks[job_id].done():
        return
    task = loop.create_task(run_job(job_id))
    _running_tasks[job_id] = task


async def _poll_and_finalize(
    session,
    job: Job,
    used_key: keystore.Key,
    run_id: str,
    dataset_id: str | None,
    initial_run_data: dict[str, Any] | None = None,
) -> None:
    """Poll an existing Apify run, then fetch and filter its dataset."""
    terminal = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}
    elapsed = 0.0
    run_data = initial_run_data or {}
    status = (run_data.get("status") or "RUNNING").upper()
    while status not in terminal:
        if elapsed >= apify_client.POLL_TIMEOUT_SEC:
            raise ApifyError("Apify run timed out waiting for completion")
        await asyncio.sleep(apify_client.POLL_INTERVAL_SEC)
        elapsed += apify_client.POLL_INTERVAL_SEC
        try:
            run_data = await apify_client.get_run(used_key.token, run_id)
        except ApifyAuthError:
            used_key.is_bad = True
            raise
        status = (run_data.get("status") or "").upper()
        dataset_id = run_data.get("defaultDatasetId") or dataset_id
        job.apify_dataset_id = dataset_id
        job.progress_message = (run_data.get("statusMessage") or "")[:500]
        if dataset_id:
            try:
                dataset_info = await apify_client.get_dataset_info(used_key.token, dataset_id)
                job.raw_count = int(dataset_info.get("itemCount") or 0)
            except ApifyError:
                log.warning("Job %s could not refresh dataset info", job.id, exc_info=True)
        session.commit()

    if status != "SUCCEEDED":
        raise ApifyError(f"Apify run ended with status {status}")
    if not dataset_id:
        raise ApifyError("Run succeeded but no dataset id")

    raw_items = await apify_client.fetch_dataset_items(used_key.token, dataset_id)
    job_dir = JOBS_DIR / str(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "raw.json").write_text(
        json.dumps(raw_items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:
        min_er = float(job.min_engagement_rate_str or 0)
    except ValueError:
        min_er = 0.0
    filters: dict[str, Any] = {
        "min_followers": job.min_followers,
        "max_followers": job.max_followers,
        "require_email": job.require_email,
        "exclude_selling_digital": job.exclude_selling,
        "min_engagement_rate": min_er,
    }
    plat = getattr(job, "platform", None) or "instagram"
    qualified, rejected, reason_counts = filter_leads(
        raw_items, filters, niche=job.niche, platform=plat
    )
    write_exports(job_dir, qualified, rejected)

    job.raw_count = len(raw_items)
    job.qualified_count = len(qualified)
    job.rejected_count = len(rejected)
    job.reject_summary_json = json.dumps(reason_counts)
    job.results_json = json.dumps(qualified)
    job.status = "succeeded"
    job.finished_at = _utcnow()
    job.error_message = None
    job.progress_message = None
    session.commit()
    log.info("Job %s succeeded raw=%s qualified=%s", job.id, job.raw_count, job.qualified_count)


async def _resume_job(job_id: int) -> None:
    """Resume polling for a job whose Apify run survived an app restart."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job or not job.apify_run_id:
            return
        # Keys live in memory, so match by label (e.g. reloaded from APIFY_TOKENS)
        key = next(
            (k for k in keystore.all_keys() if k.label == job.api_key_label), None
        )
        if not key:
            raise ApifyError(
                "The app restarted during this scrape and its API key isn't loaded any more. "
                "Start the scrape again."
            )
        await _poll_and_finalize(session, job, key, job.apify_run_id, job.apify_dataset_id)
    except Exception as e:
        log.exception("Resumed job %s failed", job_id)
        try:
            job = session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(e)[:2000]
                job.finished_at = _utcnow()
                session.commit()
        except Exception:
            log.exception("Failed to mark resumed job %s as failed", job_id)
    finally:
        session.close()
        _running_tasks.pop(job_id, None)


async def resume_orphaned_jobs() -> None:
    """Schedule all persisted running jobs that have an Apify run id."""
    session = get_session()
    try:
        # Pipeline jobs chain several runs in memory and can't be resumed.
        for job in (
            session.query(Job)
            .filter(Job.status.in_(("queued", "running")), Job.engine == "pipeline")
            .all()
        ):
            job.status = "failed"
            job.error_message = "The app was restarted while this scrape was running. Start it again."
            job.finished_at = _utcnow()
        session.commit()
        job_ids = [
            row.id
            for row in session.query(Job)
            .filter(Job.status == "running", Job.apify_run_id.isnot(None))
            .all()
        ]
    finally:
        session.close()
    loop = asyncio.get_running_loop()
    for job_id in job_ids:
        if job_id not in _running_tasks or _running_tasks[job_id].done():
            _running_tasks[job_id] = loop.create_task(_resume_job(job_id))


async def run_job(job_id: int) -> None:
    log.info("Job %s starting", job_id)
    session = get_session()
    try:
        job = session.get(Job, job_id)
        if not job:
            return
        job.status = "running"
        job.started_at = _utcnow()
        session.commit()

        if job.engine == "pipeline":
            from app.pipeline import Pipeline

            await Pipeline(session, job).run()
            return

        keywords = json.loads(job.keywords_json or "[]")
        hashtags = json.loads(job.hashtags_json or "[]")
        seed_usernames = json.loads(job.seed_usernames_json or "[]")
        plat = getattr(job, "platform", None) or "instagram"
        run_input = apify_client.build_actor_input(
            keywords,
            hashtags,
            job.location or "",
            job.max_leads,
            job.actor_id,
            min_followers=job.min_followers,
            max_followers=job.max_followers,
            seed_usernames=seed_usernames,
            seed_discovery=bool(job.seed_discovery),
            platform=plat,
            require_email=bool(job.require_email),
            exclude_selling=bool(job.exclude_selling),
        )

        # Try keys with failover on auth failure
        tried_ids: set[int] = set()
        last_err: str | None = None
        run_data: dict[str, Any] | None = None
        used_key: keystore.Key | None = None

        while True:
            candidates = [k for k in keystore.all_keys() if k.enabled and k.id not in tried_ids]
            if not candidates:
                raise ApifyError(last_err or "No Apify API keys loaded — add one on the API keys page")
            key = pick_next_key()
            if key is None or key.id in tried_ids:
                key = next((k for k in candidates if not k.is_bad), candidates[0])
            tried_ids.add(key.id)

            try:
                log.info("Job %s using key label=%s", job_id, key.label)
                run_data = await apify_client.start_actor_run(
                    key.token, job.actor_id, run_input
                )
                used_key = key
                break
            except ApifyAuthError as e:
                last_err = str(e)
                log.warning("Job %s auth failed for key label=%s", job_id, key.label)
                key.is_bad = True
                continue
            except apify_client.ApifyKeyUnavailableError as e:
                last_err = str(e)
                log.warning("Job %s key label=%s unavailable; trying next", job_id, key.label)
                continue

        assert used_key is not None and run_data is not None
        job.api_key_label = used_key.label
        run_id = run_data.get("id") or run_data.get("actRunId")
        dataset_id = run_data.get("defaultDatasetId")
        job.apify_run_id = run_id
        job.apify_dataset_id = dataset_id
        session.commit()

        await _poll_and_finalize(session, job, used_key, run_id, dataset_id, run_data)
    except Exception as e:
        log.exception("Job %s failed", job_id)
        try:
            job = session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(e)[:2000]
                job.finished_at = _utcnow()
                session.commit()
        except Exception:
            log.exception("Failed to mark job %s as failed", job_id)
    finally:
        session.close()
        _running_tasks.pop(job_id, None)
