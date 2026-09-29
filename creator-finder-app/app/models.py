"""SQLAlchemy models for scrape jobs. (API keys are never stored — see keystore.py.)"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    niche = Column(String(200), nullable=False, default="")
    status = Column(String(40), nullable=False, default="queued")
    # queued | running | succeeded | failed
    error_message = Column(Text, nullable=True)
    progress_message = Column(Text, nullable=True)

    # Form / filter params (JSON snapshots)
    keywords_json = Column(Text, nullable=False, default="[]")
    hashtags_json = Column(Text, nullable=False, default="[]")
    seed_usernames_json = Column(Text, nullable=False, default="[]")
    seed_discovery = Column(Boolean, default=False)
    location = Column(String(300), nullable=True, default="")
    min_followers = Column(Integer, default=10000)
    max_followers = Column(Integer, default=100000)
    require_email = Column(Boolean, default=True)
    exclude_selling = Column(Boolean, default=True)
    min_engagement_rate = Column(Integer, default=0)  # stored as percent * 100? or float as string
    # Use string for float flexibility
    min_engagement_rate_str = Column(String(20), default="0")
    max_leads = Column(Integer, default=50)
    actor_id = Column(String(200), default="coregent~instagram-creator-leads-scraper")
    platform = Column(String(40), default="instagram", nullable=False)  # instagram | twitter
    # "pipeline" = official Apify IG scrapers (app/pipeline.py); "legacy" = single actor
    engine = Column(String(40), default="legacy", nullable=False)
    options_json = Column(Text, nullable=True)  # pipeline options
    usage_json = Column(Text, nullable=True)  # Apify usage + warnings

    # API keys live in memory only (app/keystore.py); jobs just record the label.
    api_key_id = Column(Integer, nullable=True)  # unused, kept for old databases
    api_key_label = Column(String(120), nullable=True)
    apify_run_id = Column(String(120), nullable=True)
    apify_dataset_id = Column(String(120), nullable=True)

    raw_count = Column(Integer, default=0)
    qualified_count = Column(Integer, default=0)
    rejected_count = Column(Integer, default=0)
    reject_summary_json = Column(Text, nullable=True)  # {"no_email": 3, ...}
    results_json = Column(Text, nullable=True)  # qualified leads JSON for table

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
