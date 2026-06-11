# Bazaarvoice Review Scraper

Given a brand name, this tool discovers all matching products across Bazaarvoice-powered retailer sites, scrapes all customer reviews, and stores them in a PostgreSQL database.

## Requirements

- Docker Desktop

That's it — no Python or PostgreSQL installation needed on your machine.

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
Then open `.env` and fill in your `DATABASE_URL` (Supabase connection string) and Bazaarvoice passkey(s).

**3. Build the image**
```bash
docker compose build
```

## Usage

Run any command with `docker compose run --rm scraper <command>`. The `--rm` flag removes the container after the command finishes.

```bash
# Register a brand and run an initial scrape
docker compose run --rm scraper add-brand Dior

# Re-scrape all configured retailers for a brand
docker compose run --rm scraper scrape Dior

# Scrape a specific retailer
docker compose run --rm scraper scrape Dior --site bazaarvoice_douglas

# Scrape one specific product by its external ID (skips discovery)
docker compose run --rm scraper scrape Dior --site bazaarvoice_douglas --product-id 5010859059

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

# Export reviews for a specific product by its DB ID (most reliable)
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
| `SCRAPE_DELAY_MIN` | Min seconds between requests (default: `0.5`) |
| `SCRAPE_DELAY_MAX` | Max seconds between requests (default: `2.0`) |

## Automated Scraping (GitHub Actions)

The workflow at `.github/workflows/scrape.yml` runs the scraper automatically every day at 8am UTC. It can also be triggered manually from the GitHub Actions tab.

To enable it, add two secrets to your GitHub repo under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Your Supabase connection string |
| `BV_PASSKEY_DOUGLAS` | Your Bazaarvoice passkey |

## Adding a New Bazaarvoice Retailer

No code changes needed. Add two lines to `.env`:

```
BV_PASSKEY_SEPHORA=<passkey>
BV_LOCALE_SEPHORA=fr_FR
```

And add the corresponding `BV_PASSKEY_SEPHORA` secret in GitHub if you want it available in the automated workflow too.

The scraper auto-registers as `bazaarvoice_sephora` on the next run.

**How to find a retailer's passkey:**
1. Open a product page on the retailer's site that shows reviews
2. Open browser DevTools → Network tab → filter by `bazaarvoice`
3. The passkey appears in every `api.bazaarvoice.com` request URL as `passkey=xxxxx`

## Architecture

```
cli.py → src/runner.py → src/scrapers/bazaarvoice.py → src/normalizer.py → PostgreSQL
```

Database migrations run automatically when the container starts (`alembic upgrade head` in `entrypoint.sh`).
Database deduplication is enforced via `UNIQUE(source_site, external_review_id)` — re-running never creates duplicate reviews.

## Notes

- The same product sold on two different retailer sites is stored as two separate products in the database, each with their own reviews.
