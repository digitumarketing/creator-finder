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
        if "platform" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN platform VARCHAR(40) DEFAULT 'instagram'"
            )


def get_session():
    if SessionLocal is None:
        get_engine()
    return SessionLocal()
