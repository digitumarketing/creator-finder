"""SQLite database setup."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base

# App root: /workspace/creator-finder-app
APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = APP_ROOT / "data"
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "app.db"

_engine = None
SessionLocal = None


def get_engine():
    global _engine, SessionLocal
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DB_PATH}"
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Keep existing SQLite databases compatible with newly-added columns.
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)")}
        if "progress_message" not in columns:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN progress_message TEXT")
        if "seed_usernames_json" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN seed_usernames_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "seed_discovery" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN seed_discovery BOOLEAN DEFAULT 0"
            )
        if "engine" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN engine VARCHAR(40) NOT NULL DEFAULT 'legacy'"
            )
        if "options_json" not in columns:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN options_json TEXT")
        if "usage_json" not in columns:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN usage_json TEXT")
        # API keys are kept in memory only (app/keystore.py). Remove any that
        # older versions saved here, and VACUUM so they're gone from the file.
        tables = {row[0] for row in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "api_keys" in tables:
            conn.exec_driver_sql("UPDATE jobs SET api_key_id = NULL")
            wiped = conn.exec_driver_sql("DELETE FROM api_keys").rowcount
        else:
            wiped = 0
        if "platform" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN platform VARCHAR(40) DEFAULT 'instagram'"
            )
    if wiped:
        with engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT").exec_driver_sql("VACUUM")


def get_session():
    if SessionLocal is None:
        get_engine()
    return SessionLocal()
