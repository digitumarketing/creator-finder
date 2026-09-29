# Verification report — Creator Finder

Date: 2026-09-24 (Asia/Karachi)

## Environment

- Python 3.13 (venv at `.venv/`)
- Installed: `pip install -r requirements.txt`
- Server: `uvicorn app.main:app --host 127.0.0.1 --port 8787`

## Tests run

### 1. Unit tests (offline filters)

```bash
cd /workspace/creator-finder-app
source .venv/bin/activate
pytest -q
```

**Result:** `8 passed`

Covers:

- email extraction from bio
- follower min/max bounds
- require email
- exclude selling (bio + URL keywords: course, stan.store, gumroad, …)
- qualified set on sample Instagram-like rows
- Manyreach CSV column shape
- default keyword lists seeded

### 2. Manual filter script

Ran `filter_leads` on two sample dicts (good lead + course/gumroad seller).

**Result:** `qualified=1 rejected=1` with reason `bio_monetized:course`

### 3. HTTP smoke (local server)

| Endpoint | Status |
|----------|--------|
| `GET /` | 200 |
| `GET /scrape` | 200 |
| `GET /keys` | 200 |
| `GET /jobs` | 200 |
| `GET /health` | 200 |
| `GET /jobs/1` (seeded offline job) | 200 |
| `GET /jobs/1/download/qualified` | 200 |
| `GET /jobs/1/download/manyreach` | 200 |
| `GET /jobs/1/download/rejected` | 200 |

### 4. Security checks

- Added a throwaway token via `POST /keys/add`, then confirmed HTML shows masked form (`apify_…CDEF`) and **does not** contain the full token.
- Demo test token was deleted from SQLite afterward.
- No live Apify scrape was run (offline filter + UI verification only).

### 5. Offline job seed

A succeeded job (`niche=parenting-demo`) was written to SQLite with exports under `data/jobs/1/` so job detail + CSV download paths could be verified without calling Apify.

## How to run (on Roshaan’s machine)

```bash
cd creator-finder-app
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8787
# or: ./start.sh
```

Open http://127.0.0.1:8787 → add keys at `/keys` → start a scrape.

## Important paths

| Path | Purpose |
|------|---------|
| `app/main.py` | FastAPI routes |
| `app/filters.py` | Vendored normalize/filter logic |
| `app/apify_client.py` | Apify REST client |
| `app/jobs.py` | Background runner + key failover |
| `app/models.py` / `app/db.py` | SQLite models |
| `app/templates/` | Jinja2 UI |
| `data/app.db` | Local DB (gitignored) |
| `data/jobs/{id}/` | Per-job CSV/JSON exports |
| `tests/test_filters.py` | Filter unit tests |
| `README.md` | User docs |

## Not tested live

- Real Apify actor run / dataset download (requires Roshaan’s Apify token)
- Round-robin failover against a deliberately bad key (logic present in `app/jobs.py`)
