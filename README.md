# Review Scraper

Scrapes customer reviews for a given brand from multiple retailer sites and stores them in PostgreSQL. Supports eight scraper types:

- **Bazaarvoice** — REST API scraper (Douglas, Dior, and any other BV-powered retailer). Excludes reviews syndicated from the manufacturer's own site by default, to match what the retailer's storefront actually displays. Dior's own site (`bazaarvoice_dior`) is the exception: as the manufacturer's own site it displays syndicated reviews from other Dior country sites, so it's configured to include them; it also collapses Bazaarvoice's shade/size "family" variants (e.g. ~30 lipstick shades sharing one review pool) down to a single representative product per family, so one real product doesn't surface as dozens of near-duplicate DB rows.
- **Sephora** — Playwright/Camoufox HTML scraper for sephora.it (bypasses Akamai bot protection via in-page fetch to Next.js Server Actions)
- **Notino** — Camoufox for product discovery (Cloudflare-gated pages) + plain `requests` for reviews via Apollo Persisted Queries to the non-gated `/api/product/` endpoint
- **Marionnaud** — Camoufox for product discovery (Akamai-gated OCC/Hybris search API, called via in-page fetch) + plain `requests` for reviews via PowerReviews' display API (separate non-gated domain)
- **Sensation** — plain `requests` for both discovery (product sitemap) and reviews (JSON API on the non-gated `api.sensationprofumerie.it` subdomain) — no browser needed
- **Ditano** — plain `requests` against Shopify's public `products.json` for discovery and Judge.me's public review widget for reviews — no browser needed
- **Pinalli** — same Shopify + Judge.me stack as Ditano, with discovery unioning `products.json` and Algolia (the storefront's own search index) to cover this larger, headless-Shopify catalog
- **Primor** — plain `requests` for both discovery (product sitemap) and reviews (JSON blob embedded in a static page on the non-gated `reviews.primor.eu` subdomain, keyed by product SKU) — no browser needed; configurable (multi-size) products aggregate reviews across each size variant's own SKU

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
docker compose run --rm scraper scrape Dior --site bazaarvoice_douglas
docker compose run --rm scraper scrape Dior --site bazaarvoice_dior
docker compose run --rm -e SEPHORA_ENABLED=1 scraper scrape Dior --site sephora
docker compose run --rm -e NOTINO_ENABLED=1 scraper scrape Dior --site notino
docker compose run --rm -e MARIONNAUD_ENABLED=1 scraper scrape Dior --site marionnaud
docker compose run --rm -e SENSATION_ENABLED=1 scraper scrape Dior --site sensation
docker compose run --rm -e DITANO_ENABLED=1 scraper scrape Dior --site ditano
docker compose run --rm -e PINALLI_ENABLED=1 scraper scrape Dior --site pinalli
docker compose run --rm -e PRIMOR_ENABLED=1 scraper scrape Dior --site primor

# Scrape one specific product by its external ID (skips discovery — the product
# must have already been found by a prior full scrape, so its details are known)
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

# Export reviews for a specific product by its DB ID
docker compose run --rm scraper export Dior --product-id 5397

# Remove a brand and all its data
docker compose run --rm scraper remove-brand Dior

# Remove all products and reviews for a specific retailer (across all brands)
docker compose run --rm scraper remove-retailer notino --yes
```

The exported CSV includes `product_id`, `product_name`, `product_url`, `product_category`, `product_category_group`, `source_site`, and `retailer` columns. `product_category` is the granular label (e.g. `Lipstick`, `Women's Fragrance`) and `product_category_group` is the broad bucket (`Makeup`, `Fragrance`, `Skincare`, `Body Care`, `Haircare`). Files are saved in the current directory with an auto-generated name (e.g. `dior_20260610_143022.csv`). Use `-o <path>` to choose the location.

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Supabase (or any PostgreSQL) connection string |
| `BV_PASSKEY_<RETAILER>` | Bazaarvoice passkey for a retailer (e.g. `BV_PASSKEY_DOUGLAS`) |
| `BV_LOCALE_<RETAILER>` | Locale for that retailer (e.g. `BV_LOCALE_DOUGLAS=it_IT`) |
| `SEPHORA_ENABLED` | Set to `1` (or `true`/`yes`/`on`) to enable the Sephora scraper |
| `NOTINO_ENABLED` | Set to `1` to enable the Notino scraper |
| `MARIONNAUD_ENABLED` | Set to `1` to enable the Marionnaud scraper |
| `SENSATION_ENABLED` | Set to `1` to enable the Sensation scraper |
| `DITANO_ENABLED` | Set to `1` to enable the Ditano scraper |
| `PINALLI_ENABLED` | Set to `1` to enable the Pinalli scraper |
| `PRIMOR_ENABLED` | Set to `1` to enable the Primor scraper |
| `MARIONNAUD_MERCHANT_ID` | Override PowerReviews merchant ID (has a hardcoded default) |
| `MARIONNAUD_APIKEY` | Override PowerReviews API key (has a hardcoded default) |
| `NOTINO_REVIEWS_HASH` | Override Apollo Persisted Query hash for `getReviews` (has a hardcoded default) |
| `SEPHORA_NEXT_ACTION_ID` | Override Next.js server action hash (has a hardcoded default) |
| `DITANO_SHOP_DOMAIN` | Override Ditano's myshopify domain (has a hardcoded default) |
| `PINALLI_PRODUCTS_BASE` | Override the origin Pinalli's products.json is served from (has a hardcoded default) |
| `PINALLI_SHOP_DOMAIN` | Override Pinalli's backend myshopify domain used by Judge.me (has a hardcoded default) |
| `PINALLI_ALGOLIA_APP` / `PINALLI_ALGOLIA_KEY` / `PINALLI_ALGOLIA_INDEX` | Override Pinalli's public Algolia search app/key/index used for discovery (has hardcoded defaults) |
| `SCRAPE_DELAY_MIN` | Min seconds between requests (default: `0.5`) |
| `SCRAPE_DELAY_MAX` | Max seconds between requests (default: `2.0`) |

## Automated Scraping (GitHub Actions)

The workflow at `.github/workflows/scrape-daily.yml` runs every day at 9am UTC with two jobs and can also be triggered manually from the GitHub Actions tab.

- **`scrape-api-sites`** — runs on `ubuntu-latest` (GitHub-hosted runner); runs Douglas, Dior, Sensation, Ditano, Pinalli, and Primor as separate `continue-on-error: true` steps sharing one Docker build, so one site failing doesn't block the others in the same job
- **`scrape-self-hosted-sites`** — runs on `self-hosted` (Italian IP needed for full catalog); runs Notino and Marionnaud as separate `continue-on-error: true` steps sharing one Docker build — merged into a single job since there's only one self-hosted runner, so separate jobs bought no parallelism and just built the image twice

Sephora has its own workflow, `.github/workflows/scrape-sephora.yml` — a separate self-hosted job on a 10am UTC schedule with a 180-minute timeout, kept apart from the shared daily workflow because of its Akamai bot-detection risk (see Architecture below).

**Scraping is incremental** — on each run, only reviews newer than the last successful scrape are fetched. Re-running never creates duplicates.

To enable, add these secrets to your GitHub repo under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DATABASE_URL` | Your Supabase connection string |
| `BV_PASSKEY_DOUGLAS` | Your Bazaarvoice passkey for Douglas |
| `BV_PASSKEY_DIOR` | Your Bazaarvoice passkey for Dior's own site |

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

**Category mapping for a new BV retailer:**
Each retailer uses its own internal `CategoryId` codes. To add granular labels, add an entry to `_BV_CATEGORY_MAPS` in `src/scrapers/__init__.py` keyed by the retailer name (lowercase). The map keys are the first 4 digits of the `CategoryId` (e.g. `"0302"`) and values are human-readable labels. Products whose prefix isn't in the map fall back to the raw `CategoryId` string.

To also assign a broad group to those labels, add the new label → group entry to `CATEGORY_GROUP` in `src/categories.py`.

**Two opt-in flags for edge cases** (both default off, both added in `src/scrapers/__init__.py`; Dior is currently the only retailer using either):
- `_BV_INCLUDE_SYNDICATED` — add a retailer here if its *own* product pages display reviews syndicated in from another site on the same BV account (check by inspecting the live page's review-widget request for an `IsSyndicated` filter). Most retailers only show their own natively-collected reviews and should stay excluded (the default).
- `_BV_DEDUPE_FAMILIES` — add a retailer here if its catalog registers a separate product ID per shade/size variant that all share one Bazaarvoice "family" review pool (exposed as the product's `FamilyIds[0]`). Collapses each family down to a single representative product (preferring one with a real `ProductPageUrl`, then the highest native review count) instead of one DB row per variant.

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

| Test file | Requires | What it tests |
|---|---|---|
| `test_normalizer.py` | nothing | `ReviewNormalizer.from_bazaarvoice()` pure unit tests |
| `test_sephora_normalizer.py` | nothing | Sephora RSC-stream parsing pure unit tests |
| `test_backfill_cursor.py` | `DATABASE_URL` | Sephora backfill cursor upsert SQL against real Postgres |
| `test_douglas_scraper.py` | `BV_PASSKEY_DOUGLAS` | Live Bazaarvoice API integration; count tests use `>=` floor so they don't break as new reviews accumulate |
| `test_dior_scraper.py` | `BV_PASSKEY_DIOR` | Live Bazaarvoice API integration against dior.com's own account; verifies `include_syndicated=True` is needed to match the site's displayed review count |
| `test_bazaarvoice_dedupe.py` | nothing | Pure unit tests for the family-variant dedup logic (`_dedupe_by_family`) against synthetic product dicts |
| `test_notino_scraper.py` | `NOTINO_ENABLED=1` | Live Notino GraphQL API integration (no browser) |
| `test_marionnaud_scraper.py` | `MARIONNAUD_ENABLED=1` | Live PowerReviews API integration |
| `test_sephora_scraper.py` | `SEPHORA_ENABLED=1` | Live Sephora discovery — verifies cross-brand contamination fix (YSL product `P10055930` must not appear in Dior results); requires self-hosted runner (Akamai blocks standard CI) |
| `test_sensation_scraper.py` | nothing (unit) / `SENSATION_ENABLED=1` (live) | Pure unit tests for `from_sensation()` always run; live discovery/review/since-cutoff tests against the real API gated behind the env var |
| `test_ditano_scraper.py` | nothing (unit) / `DITANO_ENABLED=1` (live) | Pure unit tests for the Judge.me widget parser + `from_ditano()`; live Shopify/Judge.me tests gated behind the env var |
| `test_pinalli_scraper.py` | nothing (unit) / `PINALLI_ENABLED=1` (live) | Unit tests for Pinalli's Judge.me/source_url wiring; live tests verify the products.json+Algolia discovery union and are gated behind the env var (discovery test is slow) |
| `test_primor_scraper.py` | nothing (unit) / `PRIMOR_ENABLED=1` (live) | Pure unit tests for `_reviews_url()`, `_extract_variant_skus()`, and `from_primor()`; live discovery (including an Armani slug-inconsistency case), review-fetch, configurable-product variant aggregation, and since-cutoff tests gated behind the env var |

## Architecture

```
cli.py → src/runner.py → scraper class → src/normalizer.py → PostgreSQL
```

| Scraper | Discovery | Reviews |
|---|---|---|
| `bazaarvoice.py` | Calls Bazaarvoice REST API directly | Same |
| `sephora_html.py` | Camoufox browser (Akamai-gated) | In-page `fetch()` to Next.js Server Actions (Akamai-gated) |
| `notino.py` | Camoufox browser (Cloudflare-gated) | Plain `requests` to `/api/product/` Apollo APQ endpoint |
| `marionnaud.py` | Camoufox + in-page `fetch()` to OCC API (Akamai-gated) | Plain `requests` to PowerReviews display API |
| `sensation.py` | Plain `requests` — product sitemap + brand lookup (non-gated `api.*` subdomain) | Plain `requests` to the same non-gated JSON API |
| `ditano.py` / `pinalli.py` | Plain `requests` to Shopify's public `products.json` (shared `shopify_judgeme.py` base; Pinalli also unions in Algolia) | Plain `requests` to Judge.me's public review widget endpoint |
| `primor.py` | Plain `requests` — product sitemap + per-candidate JSON-LD brand confirmation (non-gated site) | Plain `requests` to a static JSON blob on the non-gated `reviews.primor.eu` subdomain, keyed by SKU (aggregated across size variants for configurable products) |

Database migrations run automatically when the container starts (`alembic upgrade head` in `entrypoint.sh`).

The same product sold on two different retailer sites is stored as two separate `products` rows, each with their own reviews. Products are deduplicated via `UNIQUE(source_site, external_id, retailer)` and reviews via `UNIQUE(source_site, external_review_id)`.
