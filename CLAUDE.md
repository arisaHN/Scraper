# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Always use the project virtualenv:
```bash
.venv/bin/python       # instead of python
.venv/bin/pip          # instead of pip
.venv/bin/alembic      # instead of alembic
```

## Dev Commands

```bash
# Smoke test — verify registry loads
python -c "from src.scrapers import SCRAPER_REGISTRY; print(list(SCRAPER_REGISTRY))"

# Database migrations
alembic upgrade head                                       # apply all pending migrations
alembic revision --autogenerate -m "description"           # generate migration from model changes

# Inspect a Sephora page (verify selectors)
docker compose run --rm --entrypoint python -e SEPHORA_ENABLED=1 scraper -c \
  "from src.scrapers.sephora_html import SephoraHTMLScraper; s = SephoraHTMLScraper(); s.inspect('<url>')"
```

## Architecture

**Data flow:** `cli.py` → `src/runner.py` → scraper class → `src/normalizer.py` → PostgreSQL

### Key files

- **`cli.py`** — click CLI; commands: `add-brand`, `scrape`, `list-brands`, `list-products`, `export`, `remove-brand`
- **`src/runner.py`** — orchestrates discovery + scraping; `run_brand(brand_id, brand_name, registry_key)`, `run_single_product(...)`, `run_all_sites(...)`; per-product error isolation; `ScrapeRun` audit rows; passes `since=last_successful_run.finished_at` for incremental scraping
- **`src/scrapers/__init__.py`** — auto-builds `SCRAPER_REGISTRY` by scanning `BV_PASSKEY_*` env vars; registers `SephoraHTMLScraper` when `SEPHORA_ENABLED=1`
- **`src/scrapers/base.py`** — abstract `BaseScraper`; tenacity retry on `HTTPError`, `ReadTimeout`, `ConnectionError` (4 attempts, exponential backoff); rotating User-Agent; `_polite_delay()`
- **`src/scrapers/bazaarvoice.py`** — REST API scraper; `Stats=Reviews` filter; locale-aware; early-stop pagination when `review_date < since`
- **`src/scrapers/sephora_html.py`** — Playwright/Camoufox HTML scraper for sephora.it; see section below
- **`src/normalizer.py`** — `NormalizedReview` dataclass + `ReviewNormalizer.from_bazaarvoice()`, `.from_sephora()`
- **`src/models.py`** — four SQLAlchemy tables: `brands`, `products`, `reviews`, `scrape_runs`; `SiteEnum` includes `bazaarvoice` and `sephora`
- **`src/database.py`** — `get_session()` context manager (commit on exit, rollback on exception)
- **`src/exporter.py`** — `export_brand(brand_name, fmt, output_path, product_filter, product_id_filter)`

### SCRAPER_REGISTRY format

Each entry is a dict, not a bare class:
```python
{
    "class": BazaarvoiceScraper,
    "source_site": "bazaarvoice",   # stored in DB
    "kwargs": {"passkey": "...", "locale": "it_IT"},
    "retailer": "douglas",          # stored in Product.retailer
}
```
Runner uses `entry["source_site"]` for DB storage and `registry_key` (e.g. `"bazaarvoice_douglas"`) for logging.

### Database deduplication

Reviews: `UNIQUE(source_site, external_review_id)` — inserts use `ON CONFLICT DO NOTHING`.
Products: `UNIQUE(source_site, external_id)`.

### Incremental scraping

Before each brand scrape, `runner.py` queries the last successful `ScrapeRun.finished_at` for that site. This timestamp is passed as `since` to `scraper.scrape_reviews()`. Both scrapers stop paginating when they encounter reviews older than `since`.

### Adding a new Bazaarvoice retailer

Add to `.env` only — no code changes:
```
BV_PASSKEY_<RETAILER>=<key>
BV_LOCALE_<RETAILER>=<locale>
```
Auto-registers as `bazaarvoice_<retailer>` in `SCRAPER_REGISTRY`.

### SephoraHTMLScraper

Uses Camoufox (anti-bot Firefox) to bypass Akamai bot protection on sephora.it.

**Product discovery flow:**
1. Loads `https://www.sephora.it/{brand_lower}/{brand_upper}-HubPage.html` to find all `?scgid=C*` category tab URLs
2. Visits each category tab at `https://www.sephora.it/marche/dalla-a-alla-z/{brand_lower}-{brand_lower}/?scgid=CXX`
3. Extracts all `<a href>` links matching `-(P\d+)\.html` pattern, deduplicates by product ID

**Review scraping flow:**
- Loads product page (React/Next.js frontend, separate from the Demandware catalog pages)
- Review items are `<li>` elements inside `#product-detail-reviews`
- No `data-testid` on individual review fields — extraction uses JavaScript `evaluate()` on each `<li>`
- Rating extracted from `--fillRatio` CSS variable on star `<span>` elements
- "Load more" pagination via `[data-testid='load-more-button']`
- Italian dates (`"11 giu 2026"`) translated to English before parsing

**Dockerfile patches for Playwright Firefox:**
The `coreBundle.js` driver crashes when bot-detection JS throws errors without location info. Three sed patches in the Dockerfile add optional chaining + fallback defaults to `pageError.location` properties.

**Enabling Sephora:**
```
SEPHORA_ENABLED=1   # in .env or as -e flag to docker run
```
