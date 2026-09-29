# Creator Finder

Local web app for **Instagram creator discovery + filtering** aimed at micro-creator partnership outreach (Manyreach).

Niche is an input. You manage multiple Apify API keys; the app runs Apify Instagram scrapers, applies filters, shows results, and exports CSV.

Runs only on your machine. Binds to `127.0.0.1` by default. Secrets live in local SQLite under `data/` (gitignored).

## Stack

- Python 3.11+ · FastAPI · SQLite (SQLAlchemy) · Jinja2 + Tailwind CDN · uvicorn · httpx

## Install

```bash
cd creator-finder-app
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8787
```

Or use the helpers:

```bash
./start.sh          # macOS / Linux
start.bat           # Windows
```

Open **http://127.0.0.1:8787**

## How to use

### 1. Add Apify API keys (`/keys`)

1. Open **API keys**.
2. Enter a label (e.g. `main`) and your Apify API token.
3. After save, only a masked token is shown (`apify_…xxxx`).
4. Use **Test** to call `GET https://api.apify.com/v2/users/me` and confirm username + plan.
5. Enable/disable or delete keys as needed.

When a scrape starts, the app picks the next enabled key (round-robin). On auth failure it marks that key bad and tries the next.

### 2. Start a scrape (`/` or `/scrape`)

Fill in:

| Field | Notes |
|--------|--------|
| Niche name | Label only (exports / job list) |
| Keywords | One search term per line (optional if seed usernames are set) |
| Hashtags | One per line; `#` optional |
| Seed usernames | One Instagram handle per line; enables seed-only runs |
| Seed discovery | When on, actor finds related creators from seeds (default on) |
| Location | Optional; mapped countries use `preferredCountry` |
| Min / max followers | Defaults 10k–100k |
| Require public email | Default on |
| Exclude selling digital products | Default on (bio + URL keyword lists) |
| Min engagement rate % | `0` = off |
| Max leads | Default 50, hard cap 200 |
| Apify actor | Default `coregent~instagram-creator-leads-scraper` |


**Tip — seed discovery (e.g. home fitness US micros):** when keyword search is rate-limited, paste 5–10 known micro creators in your niche and leave “Discover related creators” checked. Suggested settings: min followers 5k, max 250k, location `United States`, require public email off. Avoid mega accounts (e.g. `growwithjo`) as seeds — they skew discovery away from micros.

On submit the app:

1. Creates a job (queued → running)
2. Builds actor input and starts an Apify run
3. Polls until done
4. Downloads dataset items
5. Normalizes + filters
6. Saves results under `data/jobs/{id}/` and in SQLite
7. Marks the job succeeded or failed

### 3. Review results (`/jobs/{id}`)

- Status, timing, key label used, raw / qualified counts
- Table of qualified leads
- Downloads: **qualified CSV**, **Manyreach CSV**, **rejected CSV** (with reasons), raw JSON
- Reject-reason summary counts

Manyreach CSV columns: `email`, `first_name`, `instagram_username`, `instagram_url`, `followers`, `bio`, `website`.

## Layout

```
creator-finder-app/
  app/
    main.py           # FastAPI routes
    db.py             # SQLite engine
    models.py         # ApiKey, Job
    apify_client.py   # Apify REST helpers
    filters.py        # Normalize + filter (vendored)
    jobs.py           # Background runner + key failover
    templates/        # Jinja2 pages
    static/style.css
  data/               # SQLite + job files (gitignored)
  tests/test_filters.py
  requirements.txt
  start.sh / start.bat
  VERIFY.md
```

## Security notes

- Bind to `127.0.0.1` only (do not expose to the network without auth).
- Tokens are stored only in local SQLite; never logged.
- Full tokens are never rendered in HTML after save.
- `data/` is gitignored — do not commit real keys.

## Filter defaults (exclude selling)

Bio keywords include: course, ebook, gumroad, stan.store, teachable, kajabi, whop, digistore, payhip, shop my, …

URL keywords include: gumroad.com, stan.store, teachable.com, kajabi.com, digistore24, payhip.com, whop.com, …

## Tests

```bash
source .venv/bin/activate
pytest -q
```
