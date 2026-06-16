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
- **`src/runner.py`** — orchestrates discovery + scraping; `run_brand(brand_id, brand_name, registry_key)`, `run_single_product(...)`, `run_all_sites(...)`; per-product error isolation; `ScrapeRun` audit rows; passes `since=last_successful_run.finished_at` for incremental scraping (including `run_single_product`); batches a product's reviews into one `_upsert_reviews()` transaction instead of one insert per review; calls `scraper.close()` in a `finally` block
- **`src/scrapers/__init__.py`** — auto-builds `SCRAPER_REGISTRY` by scanning `BV_PASSKEY_*` env vars; registers `SephoraHTMLScraper` when `SEPHORA_ENABLED` is `1`/`true`/`yes`/`on`
- **`src/scrapers/base.py`** — abstract `BaseScraper`; tenacity retry on `HTTPError`, `ReadTimeout`, `ConnectionError` (4 attempts, exponential backoff); rotating User-Agent; `_polite_delay()`; `close()` (no-op default, overridden by scrapers holding a live resource); `_past_cutoff(review_date, since)` shared by both scrapers for incremental-scraping comparisons
- **`src/scrapers/bazaarvoice.py`** — REST API scraper; `Stats=Reviews` filter; locale-aware; early-stop pagination when `review_date < since`
- **`src/scrapers/sephora_html.py`** — Playwright/Camoufox HTML scraper for sephora.it; see section below
- **`src/normalizer.py`** — `NormalizedReview` dataclass + `ReviewNormalizer.from_bazaarvoice()`, `.from_sephora()`
- **`src/models.py`** — four SQLAlchemy tables: `brands`, `products`, `reviews`, `scrape_runs`; `SiteEnum` includes `bazaarvoice` and `sephora`; `Product` unique constraint is `(source_site, external_id, retailer)` — `retailer` is part of the key so two Bazaarvoice retailers can't collide on the same `external_id`
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

Reviews: `UNIQUE(source_site, external_review_id)` — inserts use `ON CONFLICT DO NOTHING`, batched per-product via `_upsert_reviews()`.
Products: `UNIQUE(source_site, external_id, retailer)`.

### Incremental scraping

Before each brand scrape (and before a `--product-id` single-product scrape), `runner.py` queries the last successful `ScrapeRun.finished_at` for that site. This timestamp is passed as `since` to `scraper.scrape_reviews()`. Both scrapers stop paginating when they encounter reviews older than `since`, via the shared `BaseScraper._past_cutoff()` helper.

`run_single_product` requires the product to have been discovered by a prior full `scrape` (so its `source_url` is known); scrapers that need a real URL to navigate (e.g. `SephoraHTMLScraper`) raise a clear error if it's missing instead of failing with an opaque browser error.

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
- Rating extracted from `--fillRatio` CSS variable on star `<span>` elements, summed and clamped to `[0, 5]`
- Author/title taken from non-empty bold `<p>` elements only (icon-only badges with no text are filtered out before positional `[0]`/`[1]` assignment)
- "Verified" badge detected via a hardcoded SVG fill color, with a text-match fallback (`/verificat|verified/i`) in case the badge color changes
- "Load more" pagination via `[data-testid='load-more-button']`; each pass only processes the `<li>` elements appended since the last pass (tracked via an index), so reviews already yielded are never re-extracted/re-yielded
- Italian dates (`"11 giu 2026"`) translated to English before parsing
- `external_review_id` is `sha256(author|date|rating|full_text)` — full review text (not a truncated prefix) is hashed to minimize collisions between distinct anonymous reviews

**Browser lifecycle:**
`close()` explicitly tears down the Camoufox/Firefox process; `runner.py` calls it in a `finally` block after each `run_brand`/`run_single_product`. `__del__` just calls `close()` as a GC-time safety net — don't rely on `__del__` alone for cleanup.

**Dockerfile patches for Playwright Firefox:**
The `coreBundle.js` driver crashes when bot-detection JS throws errors without location info. Three sed patches in the Dockerfile add optional chaining + fallback defaults to `pageError.location` properties. A post-sed `grep` check fails the build loudly if the patch didn't apply (e.g. after a Playwright version bump changes the bundled file). `playwright` is pinned explicitly in `requirements.txt` to the version the patch was verified against — if you bump it, rebuild and confirm the build still succeeds (it will fail fast if the patch no-ops) and re-verify the path/version pair, since playwright's driver bundle layout can change between versions.

**Enabling Sephora:**
```
SEPHORA_ENABLED=1   # also accepts true/yes/on, in .env or as -e flag to docker run
```
