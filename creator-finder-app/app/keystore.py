"""Apify API keys, held in memory only.

Keys are never written to the database, disk or logs. They live as long as
the app process: after a restart you paste them again on the API keys page,
or set the APIFY_TOKENS environment variable so they load automatically:

    APIFY_TOKENS="main:apify_api_xxx,backup:apify_api_yyy"

(a bare token without "label:" works too).
"""
from __future__ import annotations

import itertools
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Key:
    id: int
    label: str
    token: str = field(repr=False)
    enabled: bool = True
    is_bad: bool = False  # Apify rejected the token
    source: str = "dashboard"  # dashboard | env
    created_at: datetime | None = None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_username: str | None = None
    last_test_plan: str | None = None
    last_test_error: str | None = None

    def masked_token(self) -> str:
        t = self.token or ""
        if len(t) <= 8:
            return "••••••••"
        prefix = t[:6] if t.startswith("apify_") else t[:4]
        return f"{prefix}…{t[-4:]}"


_keys: dict[int, Key] = {}
_ids = itertools.count(1)
_lock = threading.Lock()


def add(label: str, token: str, source: str = "dashboard") -> Key:
    with _lock:
        for k in _keys.values():
            if k.token == token:
                return k  # same token pasted twice
        key = Key(id=next(_ids), label=label, token=token, source=source,
                  created_at=datetime.now().astimezone())
        _keys[key.id] = key
        return key


def get(key_id: int) -> Key | None:
    return _keys.get(key_id)


def delete(key_id: int) -> None:
    with _lock:
        _keys.pop(key_id, None)


def all_keys() -> list[Key]:
    return sorted(_keys.values(), key=lambda k: k.id)


def usable() -> list[Key]:
    return [k for k in all_keys() if k.enabled and not k.is_bad]


def clear() -> None:
    with _lock:
        _keys.clear()


def load_from_env(var: str = "APIFY_TOKENS") -> int:
    raw = os.environ.get(var, "")
    n = 0
    for i, part in enumerate(p.strip() for p in raw.split(",")):
        if not part:
            continue
        label, sep, token = part.partition(":")
        if not sep or label.startswith("apify_api"):
            label, token = f"env-{i + 1}", part
        add(label.strip(), token.strip(), source="env")
        n += 1
    return n
