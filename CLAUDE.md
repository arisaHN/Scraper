# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Always use the project virtualenv:
```bash
.venv/bin/python       # instead of python
.venv/bin/pip          # instead of pip
.venv/bin/alembic      # instead of alembic
```

Or activate it first: `source .venv/bin/activate`

## Common Commands

```bash
# Run a scrape
python cli.py add-brand <name>               # register brand + initial scrape
python cli.py scrape <brand>                 # re-scrape all sites
python cli.py scrape <brand> --site <site>   # single site (bazaarvoice|trustpilot|amazon|google)
python cli.py list-brands                    # show brands + review counts
python cli.py export <brand> --format csv    # export to CSV/JSON

# Database
python -c "from src.database import init_db; init_db()"   # create tables (first run)
alembic upgrade head                                        # run migrations
alembic revision --autogenerate -m "description"           # generate migration from model changes

# Quick smoke test
python -c "from src.scrapers import SCRAPER_REGISTRY; print(list(SCRAPER_REGISTRY))"
```

## Architecture

The system takes a brand name, discovers matching products across multiple review platforms, scrapes all reviews, and stores them in PostgreSQL with deduplication.

**Data flow:** `cli.py` → `src/runner.py` → scraper → `src/normalizer.py` → PostgreSQL

### Key files

- **`cli.py`** — click CLI entry point; calls `runner.run_brand()` / `run_all_sites()`
- **`src/runner.py`** — orchestrates discovery + scraping for one brand/site; handles `ScrapeRun` audit rows; per-product error isolation so one timeout doesn't abort the whole run
- **`src/scrapers/base.py`** — abstract `BaseScraper`; tenacity retry on `HTTPError`, `ReadTimeout`, `ConnectionError` (4 attempts, exponential backoff); rotating User-Agent; polite delays
- **`src/normalizer.py`** — `NormalizedReview` dataclass + `ReviewNormalizer` with one static method per site (`from_bazaarvoice`, `from_trustpilot`, `from_amazon`, `from_google`)
- **`src/models.py`** — four SQLAlchemy tables: `brands`, `products`, `reviews`, `scrape_runs`
- **`src/database.py`** — `get_session()` context manager (commit on exit, rollback on exception); `init_db()`

### Scrapers

| Site | File | Method |
|------|------|--------|
| Bazaarvoice | `src/scrapers/bazaarvoice.py` | REST API; `Stats=Reviews` filter keeps only products with reviews; locale-aware (`BV_LOCALE` env var, default `en_US`) |
| Trustpilot | `src/scrapers/trustpilot.py` | Full Playwright — both discovery and review scraping (plain requests get 403); parses `__NEXT_DATA__` JSON; pagination via `filters.pagination.totalPages` |
| Amazon | `src/scrapers/amazon.py` | Playwright + `playwright_stealth` v2 (`Stealth().apply_stealth_async(page)`); parses `[data-hook="review"]` cards |
| Google | `src/scrapers/google_reviews.py` | Google Places API if `GOOGLE_PLACES_KEY` set (max 5 reviews/place); Playwright fallback otherwise |

### Database deduplication

Reviews use `UNIQUE(source_site, external_review_id)` — all inserts use `INSERT ... ON CONFLICT DO NOTHING`, making re-runs fully idempotent. Products use `UNIQUE(source_site, external_id)`.

### Configuration (`.env`)

```
DATABASE_URL=postgresql://user@localhost:5432/scraper_db
BV_PASSKEY_DOUGLAS=<key>      # Bazaarvoice retailer passkey (retailer-specific)
BV_LOCALE=it_IT                # locale for BV review fetching (must match retailer)
GOOGLE_PLACES_KEY=             # optional; enables Places API mode for Google scraper
SCRAPE_DELAY_MIN=0.5
SCRAPE_DELAY_MAX=2.0
```

### Adding a new site

1. Add a class in `src/scrapers/` extending `BaseScraper`, implementing `discover_products()` and `scrape_reviews()`
2. Add a normalizer method in `src/normalizer.py` → `ReviewNormalizer`
3. Register in `src/scrapers/__init__.py` → `SCRAPER_REGISTRY`
4. Add the new value to `SiteEnum` in `src/models.py` and generate an Alembic migration

### Adding a new Bazaarvoice retailer

Each retailer needs its own passkey scoped to that retailer's catalog. Add `BV_PASSKEY_<RETAILER>=<key>` to `.env` and instantiate a separate `BazaarvoiceScraper` entry in `SCRAPER_REGISTRY` with the correct passkey and locale.
