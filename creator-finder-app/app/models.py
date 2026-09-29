"""SQLAlchemy models for API keys and scrape jobs."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(String(120), nullable=False)
    token = Column(Text, nullable=False)  # full token, never shown in UI after save
    enabled = Column(Boolean, default=True, nullable=False)
    is_bad = Column(Boolean, default=False, nullable=False)  # auth failure flag
    last_tested_at = Column(DateTime(timezone=True), nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    last_test_username = Column(String(200), nullable=True)
    last_test_plan = Column(String(200), nullable=True)
    last_test_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    jobs = relationship("Job", back_populates="api_key")

    def masked_token(self) -> str:
        t = self.token or ""
        if len(t) <= 8:
            return "••••••••"
        prefix = t[:6] if t.startswith("apify_") else t[:4]
        return f"{prefix}…{t[-4:]}"


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

    api_key_id = Column(Integer, ForeignKey("api_keys.id"), nullable=True)
    api_key_label = Column(String(120), nullable=True)  # snapshot
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

    api_key = relationship("ApiKey", back_populates="jobs")
