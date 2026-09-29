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

Two engines:

**Official Apify Instagram scrapers (default, recommended).** A multi-step pipeline
(`app/pipeline.py`) that reads Instagram directly instead of via web search:

1. **Discover**: hashtags go to `apify/instagram-hashtag-scraper`, and each post's owner becomes a candidate.
   Keywords go to `apify/instagram-search-scraper` (account search), and seed usernames are added as-is.
   Candidates that appear in several hashtags or searches rank first.
2. **Profile check**: the top *N* candidates go to `apify/instagram-profile-scraper` in batches of 100.
   It returns followers, bio, bio links, the last 12 posts and related profiles.
3. **Related creators** (optional): related profiles of accounts that already fit your follower / activity range are checked too.
4. **Bio-link check** (optional, free, runs on your computer): opens Linktree / Beacons / websites and looks for
   contact emails and storefronts (Stan Store, Gumroad, Kajabi, Whop, "ebook", "masterclass", ...).
5. **Filter + score** locally, then write CSVs sorted best-first.

Each Apify run uses the next enabled key. If a key is out of credit or rate-limited, it's skipped for
the rest of the job. If a key is invalid, it's marked bad.

**Single custom actor (legacy).** The original behaviour: one run of the actor you name
(default `coregent~instagram-creator-leads-scraper`). That actor finds profiles through web
search and usually returns very few. Twitter/X always uses this path.

| Field | Notes |
|--------|--------|
| Niche name | Label only (exports / job list) |
| Hashtags | **Main source of creators.** 5–15 specific tags (`toddlermealideas` beats `food`) |
| Keywords | Account-name search, one Apify run per line; keep phrases short |
| Seed usernames | Known creators in the niche; with "related creators" on they snowball |
| Location | Matched against bio, name and tagged post locations (Instagram doesn't expose a profile's country). Shown per lead; only filters when "location confirmed" is ticked |
| Min / max followers | Defaults 10k–100k |
| Min engagement rate % | Computed from the last 12 posts, as (likes + comments) / followers. `0` = off |
| Posted within (days) | Drops inactive accounts; default 60, `0` = off |
| Posts per hashtag / accounts per keyword / max profiles | Scrape size. **Max profiles** is the main cost driver |
| Require public email | Bio, business email or bio-link page |
| Exclude selling digital products | Bio words, bio-link domains and bio-link page contents |
| Skip creators already found | Doesn't pay again for accounts qualified in earlier jobs |

**Good first run:** 8–12 niche hashtags, 150 posts per hashtag, max profiles 300,
require email on, bio-link check on, related creators on. Check the cost in your Apify
console afterwards, then scale up.

### 3. Review results (`/jobs/{id}`)

- Status, timing, key label(s) used, profiles checked / qualified counts, Apify usage
- Qualified leads table sorted by **score** (0–100: has email, engagement, recent posting,
  location match, found in several hashtags, has a website)
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
    pipeline.py       # Official-scraper discovery pipeline
    linkcheck.py      # Bio-link page check (emails + storefronts)
    templates/        # Jinja2 pages
    static/style.css
  data/               # SQLite + job files (gitignored)
  tests/              # pytest, offline (Apify mocked)
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
