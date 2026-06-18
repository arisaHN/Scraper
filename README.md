# Review Scraper

Scrapes customer reviews for a given brand from multiple retailer sites and stores them in PostgreSQL. Supports two scraper types:

- **Bazaarvoice** — REST API scraper (Douglas and any other BV-powered retailer). Excludes reviews syndicated from the manufacturer's own site by default, to match what the retailer's storefront actually displays.
- **Sephora** — Playwright/Camoufox HTML scraper for sephora.it (bypasses bot protection)

## Requirements

- Docker Desktop

No Python or PostgreSQL installation needed on your machine.

## Setup

**1. Clone the repo**
```bash
git clone <repo-url>
cd scraper
```

**2. Create your `.env` file**
```bash
cp .env.example .env
```
Open `.env` and fill in your values (see Configuration section below).

**3. Build the image**
```bash
docker compose build
```

## Usage

Run any command with `docker compose run --rm scraper <command>`.

```bash
# Register a brand
docker compose run --rm scraper add-brand Dior

# Scrape all configured retailers for a brand
docker compose run --rm scraper scrape Dior

# Scrape a specific retailer only
docker compose run --rm scraper scrape Dior --site bazaarvoice
docker compose run --rm scraper scrape Dior --site sephora

# Scrape one specific product by its external ID (skips discovery — the product
# must have already been found by a prior full scrape, so its details are known)
docker compose run --rm scraper scrape Dior --site bazaarvoice --product-id 5010859059

# List all tracked brands with review counts
docker compose run --rm scraper list-brands

# List all products for a brand
docker compose run --rm scraper list-products Dior

# Search products by name
docker compose run --rm scraper list-products Dior --search "Miss Dior"

# Export all reviews to CSV or JSON
docker compose run --rm scraper export Dior --format csv
docker compose run --rm scraper export Dior --format json -o dior_reviews.json

# Export reviews for a specific product by name
docker compose run --rm scraper export Dior --product "Miss Dior"

# Export reviews for a specific product by its DB ID
docker compose run --rm scraper export Dior --product-id 5397

# Remove a brand and all its data
docker compose run --rm scraper remove-brand Dior
```

The exported CSV includes `product_id`, `product_name`, `product_url`, `source_site`, and `retailer` columns. Files are saved in the current directory with an auto-generated name (e.g. `dior_20260610_143022.csv`). Use `-o <path>` to choose the location.

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Supabase (or any PostgreSQL) connection string |
| `BV_PASSKEY_<RETAILER>` | Bazaarvoice passkey for a retailer (e.g. `BV_PASSKEY_DOUGLAS`) |
| `BV_LOCALE_<RETAILER>` | Locale for that retailer (e.g. `BV_LOCALE_DOUGLAS=it_IT`) |
| `SEPHORA_ENABLED` | Set to `1` (or `true`/`yes`/`on`) to enable the Sephora scraper |
| `SCRAPE_DELAY_MIN` | Min seconds between requests (default: `0.5`) |
| `SCRAPE_DELAY_MAX` | Max seconds between requests (default: `2.0`) |

## Automated Scraping (GitHub Actions)

The workflow at `.github/workflows/scrape.yml` runs every day at 8am UTC and can also be triggered manually from the GitHub Actions tab.

**Scraping is incremental** — on each run, only reviews newer than the last successful scrape are fetched. Re-running never creates duplicates.

To enable, add these secrets to your GitHub repo under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Your Supabase connection string |
| `BV_PASSKEY_DOUGLAS` | Your Bazaarvoice passkey for Douglas |

## Adding a New Bazaarvoice Retailer

No code changes needed — just add two lines to `.env`:

```
BV_PASSKEY_NEWRETAILER=<passkey>
BV_LOCALE_NEWRETAILER=it_IT
```

The scraper auto-registers as `bazaarvoice_newretailer` on the next run.

**How to find a retailer's passkey:**
1. Open a product page on the retailer's site that shows BV-powered reviews
2. Open browser DevTools → Network tab → filter by `bazaarvoice`
3. The passkey appears in every `api.bazaarvoice.com` request URL as `passkey=xxxxx`

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

`tests/test_normalizer.py` runs with no setup. `tests/test_douglas_scraper.py` hits the live Bazaarvoice API and requires `BV_PASSKEY_DOUGLAS` to be set (skipped automatically otherwise).

## Architecture

```
cli.py → src/runner.py → scraper class → src/normalizer.py → PostgreSQL
```

| Scraper | How it works |
|---|---|
| `bazaarvoice.py` | Calls the Bazaarvoice REST API directly |
| `sephora_html.py` | Loads pages in a headless Firefox (Camoufox) to bypass bot protection, extracts reviews from the DOM |

Database migrations run automatically when the container starts (`alembic upgrade head` in `entrypoint.sh`).

The same product sold on two different retailer sites is stored as two separate `products` rows, each with their own reviews. Products are deduplicated via `UNIQUE(source_site, external_id, retailer)` and reviews via `UNIQUE(source_site, external_review_id)`.
