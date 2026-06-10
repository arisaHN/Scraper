# Multi-Site Review Scraper

Given a brand name, this tool discovers all matching products across multiple review platforms, scrapes all customer reviews, and stores them in a unified PostgreSQL database.

**Supported platforms:** Bazaarvoice (retailer sites e.g. Douglas), Trustpilot, Amazon, Google Reviews

## Features

- Automatic product discovery across all supported sites
- Idempotent scraping — re-running never creates duplicate reviews
- Per-product error isolation — one timeout does not abort the whole run
- Export reviews to CSV or JSON with exact product URLs
- CLI for all operations

## Requirements

- Python 3.11+
- PostgreSQL
- Chromium (for Playwright-based scrapers)

## Installation

```bash
git clone <repo-url>
cd scraper

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

## Configuration

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://user@localhost:5432/scraper_db
BV_PASSKEY_DOUGLAS=<your-bazaarvoice-passkey>
BV_LOCALE=it_IT
GOOGLE_PLACES_KEY=          # optional; enables Google Places API mode
SCRAPE_DELAY_MIN=0.5
SCRAPE_DELAY_MAX=2.0
```

- `BV_PASSKEY_DOUGLAS` — Bazaarvoice passkeys are retailer-specific. The passkey for Douglas Italy only works with the Douglas catalog.
- `BV_LOCALE` — must match the retailer's locale (e.g. `it_IT` for Douglas Italy, `en_US` for US retailers).
- `GOOGLE_PLACES_KEY` — if omitted, the Google scraper falls back to Playwright on Google Maps.

## Database Setup

```bash
# Create tables (first run)
python -c "from src.database import init_db; init_db()"

# Or run Alembic migrations
alembic upgrade head
```

## Usage

```bash
# Register a brand and run an initial scrape across all sites
python cli.py add-brand Dior

# Re-scrape all sites for a brand
python cli.py scrape Dior

# Scrape a single site
python cli.py scrape Dior --site bazaarvoice
python cli.py scrape Dior --site trustpilot
python cli.py scrape Dior --site amazon
python cli.py scrape Dior --site google

# List all tracked brands with review counts
python cli.py list-brands

# Export reviews to CSV or JSON
python cli.py export Dior --format csv
python cli.py export Dior --format json -o dior_reviews.json

# Remove a brand and all its data
python cli.py remove-brand Dior
```

The exported file includes `product_name` and `product_url` columns so you can see exactly which site each review came from.

## Architecture

```
cli.py → src/runner.py → scraper → src/normalizer.py → PostgreSQL
```

| Site | Method |
|------|--------|
| Bazaarvoice | REST API; retailer-scoped passkey; locale-aware |
| Trustpilot | Playwright + `__NEXT_DATA__` JSON parsing |
| Amazon | Playwright + `playwright_stealth` |
| Google | Places API (if key set) or Playwright fallback |

Database deduplication is enforced via `UNIQUE(source_site, external_review_id)` on the reviews table — all inserts use `ON CONFLICT DO NOTHING`.

## Adding a New Bazaarvoice Retailer

Each retailer needs its own passkey. Add `BV_PASSKEY_<RETAILER>=<key>` to `.env`, then register a new scraper entry in `src/scrapers/__init__.py`.

## Notes

- Amazon and Google scrapers use browser automation and may be affected by anti-bot measures.
- Google Places API free tier returns a maximum of 5 reviews per place.
- The same product sold on two different retailer sites is stored as two separate products in the database, each with their own reviews.
