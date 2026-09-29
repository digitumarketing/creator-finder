# Creator Finder

Local web app for **Instagram creator discovery + filtering** aimed at micro-creator partnership outreach (Manyreach).

Niche is an input. You manage multiple Apify API keys; the app runs Apify Instagram scrapers, applies filters, shows results, and exports CSV.

Runs on your machine (binds to `127.0.0.1`) or in a GitHub Codespace. **Apify keys are kept in memory only.
They are never written to the database, disk or logs.** Job results live in local SQLite under `data/` (gitignored).

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

### Run in the cloud (GitHub Codespaces)

1. On the repo page on GitHub, pick the branch, then click **Code → Codespaces → Create codespace**.
2. Wait about 2 minutes. The app installs and starts by itself, and a browser tab opens on port 8787.
   If it doesn't, open the **Ports** tab and click the globe icon next to 8787.
3. That `https://…app.github.dev` link is your temporary cloud link. It's **private by default**:
   only you, signed in to GitHub, can open it. Don't switch the port to "Public".
4. Stop the codespace when you're done (github.com/codespaces → ⋯ → Stop) to save free hours.

### Keeping keys across restarts (optional)

Keys you paste on the API keys page disappear when the app stops. To load them automatically, set the
`APIFY_TOKENS` environment variable before starting:

```bash
APIFY_TOKENS="main:apify_api_xxx,backup:apify_api_yyy" ./start.sh
```

In Codespaces, add `APIFY_TOKENS` under GitHub → Settings → Codespaces → Secrets. GitHub stores it
encrypted, outside the repo. Never put tokens in a file inside this folder.

### Password (when not on your own computer)

Set `APP_PASSWORD` (and optionally `APP_USERNAME`, default `admin`). Every page then asks for a login.
Use this if you ever host the app anywhere other than your laptop or a private codespace.

## How to use

### 1. Add Apify API keys (`/keys`)

1. Open **API keys**.
2. Enter a label (e.g. `main`) and your Apify API token.
3. After adding, only a masked token is shown (`apify_…xxxx`). The key lives in memory until the app stops.
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
    models.py         # Job
    keystore.py       # In-memory Apify keys
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
- API tokens are held in memory only (`app/keystore.py`); never written to SQLite, files or logs.
  On startup the app deletes any keys an older version saved in `data/app.db`.
- Full tokens are never rendered in HTML after adding.
- Optional `APP_PASSWORD` login for non-local hosting.
- `data/` is gitignored.

## Filter defaults (exclude selling)

Bio keywords include: course, ebook, gumroad, stan.store, teachable, kajabi, whop, digistore, payhip, shop my, …

URL keywords include: gumroad.com, stan.store, teachable.com, kajabi.com, digistore24, payhip.com, whop.com, …

## Tests

```bash
source .venv/bin/activate
pytest -q
```
