# Bazaarvoice Review Scraper

Given a brand name, this tool discovers all matching products across Bazaarvoice-powered retailer sites, scrapes all customer reviews, and stores them in a PostgreSQL database.

## Requirements

- Python 3.11+
- PostgreSQL

## Installation

```bash
git clone <repo-url>
cd scraper

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://user@localhost:5432/scraper_db
BV_PASSKEY_DOUGLAS=<your-bazaarvoice-passkey>
BV_LOCALE_DOUGLAS=it_IT
SCRAPE_DELAY_MIN=0.5
SCRAPE_DELAY_MAX=2.0
```

- `BV_PASSKEY_DOUGLAS` — Bazaarvoice passkeys are retailer-specific. The passkey for Douglas Italy only works with the Douglas catalog.
- `BV_LOCALE_DOUGLAS` — must match the retailer's locale (e.g. `it_IT` for Douglas Italy).

## Database Setup

```bash
alembic upgrade head
```

## Usage

```bash
# Register a brand and run an initial scrape
python cli.py add-brand Dior

# Re-scrape all configured retailers for a brand
python cli.py scrape Dior

# Scrape a specific retailer
python cli.py scrape Dior --site bazaarvoice_douglas

# Scrape one specific product by its external ID (skips discovery)
python cli.py scrape Dior --site bazaarvoice_douglas --product-id 5010859059

# List all tracked brands with review counts
python cli.py list-brands

# List all products for a brand
python cli.py list-products Dior

# Search products by name
python cli.py list-products Dior --search "Miss Dior"

# Export all reviews to CSV or JSON
python cli.py export Dior --format csv
python cli.py export Dior --format json -o dior_reviews.json

# Export reviews for a specific product by name
python cli.py export Dior --product "Miss Dior"

# Export reviews for a specific product by its DB ID (most reliable)
python cli.py export Dior --product-id 5397

# Remove a brand and all its data
python cli.py remove-brand Dior
```

The exported CSV includes `product_id`, `product_name`, `product_url`, `source_site`, and `retailer` columns. Files are saved in the current directory with an auto-generated name (e.g. `dior_20260610_143022.csv`). Use `-o <path>` to choose the location.

## Architecture

```
cli.py → src/runner.py → src/scrapers/bazaarvoice.py → src/normalizer.py → PostgreSQL
```

Database deduplication is enforced via `UNIQUE(source_site, external_review_id)` — re-running never creates duplicate reviews.

## Adding a New Bazaarvoice Retailer

No code changes needed. Add two lines to `.env`:

```
BV_PASSKEY_SEPHORA=<passkey>
BV_LOCALE_SEPHORA=fr_FR
```

The scraper auto-registers as `bazaarvoice_sephora` on the next run.

**How to find a retailer's passkey:**
1. Open a product page on the retailer's site that shows reviews
2. Open browser DevTools → Network tab → filter by `bazaarvoice`
3. The passkey appears in every `api.bazaarvoice.com` request URL as `passkey=xxxxx`

## Notes

- The same product sold on two different retailer sites is stored as two separate products in the database, each with their own reviews.
