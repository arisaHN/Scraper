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

# Run tests
.venv/bin/python -m pytest tests/ -v
```

## Testing

- `tests/test_normalizer.py` — pure unit tests for `ReviewNormalizer.from_bazaarvoice()` against fixture dicts; no network access, runs without any credentials.
- `tests/test_douglas_scraper.py` — live integration tests against the real Bazaarvoice API for a known Douglas product; skipped automatically (`pytest.mark.skipif`) unless `BV_PASSKEY_DOUGLAS` is set. All count assertions use `>=` floor values (not exact counts) so they stay valid as new reviews accumulate on the live product. Includes a self-deriving `since`-cutoff test that derives the cutoff and expected count from a fresh full fetch each run.
- `tests/test_sephora_normalizer.py` — pure unit tests for Sephora's parsing logic (`ReviewNormalizer.from_sephora()`, `_safe_from_sephora()`, `_router_state_tree()`, `_parse_rsc()`) against fixture/synthetic RSC-stream data; no network or browser, deliberately avoids hitting the live sephora.it site given the Akamai request-volume blocking risk documented below.
- `tests/test_backfill_cursor.py` — exercises the `SephoraBackfillCursor` upsert logic in `runner.py::_scrape_product()` using a `FakeSephoraScraper` test double (no browser/network) against a real Postgres connection, so the actual `ON CONFLICT` SQL is verified; skipped automatically unless `DATABASE_URL` is set. Creates and tears down a throwaway brand/product per test.
- `tests/test_notino_scraper.py` — live integration tests for `NotinoScraper` against a known Dior Sauvage EDP product (78 text reviews); skipped automatically unless `NOTINO_ENABLED` is set. Includes a self-deriving `since`-cutoff test. Only `scrape_reviews()` is tested (plain `requests` to the non-Cloudflare-gated `/api/product/` endpoint — no browser needed).
- `tests/test_marionnaud_scraper.py` — live integration tests against the real PowerReviews display API and the live marionnaud.it brand catalog; skipped automatically unless `MARIONNAUD_ENABLED` is set. Includes the same self-deriving `since`-cutoff pattern as the Notino/Douglas tests.
- `tests/test_sensation_scraper.py` — pure unit tests for `ReviewNormalizer.from_sensation()` (always run, no network) plus live integration tests against the real `api.sensationprofumerie.it` JSON API (discovery, review fetch, self-deriving `since`-cutoff); the live tests are skipped automatically unless `SENSATION_ENABLED` is set. No browser/credentials needed — the API subdomain has no bot gating. Uses `>=` floor count assertions so they stay valid as reviews accumulate.
- `tests/test_ditano_scraper.py` — pure unit tests for `_parse_review_widget()` (Judge.me widget HTML → dicts) and `ReviewNormalizer.from_ditano()` against a fixture HTML fragment (always run, no network), plus live integration tests against Shopify's `products.json` (vendor-filtered discovery) and Judge.me's public widget endpoint (review fetch for a known reviewed product); the live tests are skipped automatically unless `DITANO_ENABLED` is set. No browser/credentials needed.
- `tests/test_pinalli_scraper.py` — unit tests for Pinalli's wiring (reviews keyed by the myshopify backend, public `source_url`, that discovery is overridden, `from_judgeme(..., "pinalli")` source_site) plus live integration tests (the union products.json+Algolia discovery returns ~858 DIOR — a floor above the products.json-only count proves the union; review fetch for a known reviewed product); live tests skipped unless `PINALLI_ENABLED` is set. The discovery test is slow (pages up to 100 products.json pages). The shared Judge.me widget parsing/normalizing is covered by `tests/test_ditano_scraper.py`.
- `tests/test_sephora_scraper.py` — live integration tests for `SephoraHTMLScraper` product discovery; skipped automatically unless `SEPHORA_ENABLED` is set. Requires a self-hosted runner — Akamai blocks standard CI IPs. Verifies that the cross-brand contamination fix holds: the known YSL product `P10055930` (Black Opium Over Red) must not appear in Dior discovery results. Tests only `discover_products()`, not review scraping (deliberate — avoids triggering Akamai's request-volume-based IP blocking).

## Architecture

**Data flow:** `cli.py` → `src/runner.py` → scraper class → `src/normalizer.py` → PostgreSQL

### Key files

- **`cli.py`** — click CLI; commands: `add-brand`, `scrape`, `list-brands`, `list-products`, `export`, `remove-brand`, `remove-retailer`
- **`src/runner.py`** — orchestrates discovery + scraping; `run_brand(brand_id, brand_name, registry_key)`, `run_single_product(...)`, `run_all_sites(...)`; per-product error isolation; `ScrapeRun` audit rows; passes `since=last_successful_run.finished_at` for incremental scraping (including `run_single_product`); batches a product's reviews into one `_upsert_reviews()` transaction instead of one insert per review; calls `scraper.close()` in a `finally` block
- **`src/scrapers/__init__.py`** — auto-builds `SCRAPER_REGISTRY` by scanning `BV_PASSKEY_*` env vars; registers `SephoraHTMLScraper` when `SEPHORA_ENABLED` is `1`/`true`/`yes`/`on`; registers `NotinoScraper` when `NOTINO_ENABLED` is `1`/`true`/`yes`/`on`; registers `MarionnaudScraper` when `MARIONNAUD_ENABLED` is `1`/`true`/`yes`/`on`; registers `SensationScraper` when `SENSATION_ENABLED` is `1`/`true`/`yes`/`on`; registers `DitanoScraper` when `DITANO_ENABLED` is `1`/`true`/`yes`/`on`; registers `PinalliScraper` when `PINALLI_ENABLED` is `1`/`true`/`yes`/`on`; contains `_BV_CATEGORY_MAPS` — a per-retailer dict mapping 4-digit `CategoryId` prefixes to human-readable labels (e.g. `"0302"` → `"Lipstick"`); `_DOUGLAS_CATEGORY_MAP` covers 35 subcategories for Douglas; new retailers can be added to `_BV_CATEGORY_MAPS` without touching the scraper
- **`src/scrapers/base.py`** — abstract `BaseScraper`; tenacity retry on `HTTPError`, `ReadTimeout`, `ConnectionError` (4 attempts, exponential backoff); rotating User-Agent; `_polite_delay()`; `close()` (no-op default, overridden by scrapers holding a live resource); `_past_cutoff(review_date, since)` shared by all scrapers for incremental-scraping comparisons — strips tz-info from *both* `review_date` and `since` before comparing, since `since` isn't always naive (e.g. a tz-aware datetime passed directly in tests would otherwise raise `TypeError: can't compare offset-naive and offset-aware datetimes`); `CamoufoxBrowserMixin` — shared Camoufox lifecycle (`_open_browser`/`_close_browser`/`_refresh_browser`/`_new_page`, plus `close()`/`__del__`) mixed into `SephoraHTMLScraper`, `NotinoScraper`, and `MarionnaudScraper` (discovery only, for the last one — see below), the browser-driven scrapers
- **`src/scrapers/bazaarvoice.py`** — REST API scraper; `Stats=Reviews` filter; locale-aware; early-stop pagination when `review_date < since`. Multiple `Filter` conditions must be sent as *repeated* `Filter` query params (Bazaarvoice has no separate `Filter_<Field>` key) — `requests` only does this from a list of tuples, not a dict, since a dict can't hold two same-named keys. `include_ratings_only`/`include_syndicated` (both default `False`) *omit* the corresponding `Filter` entirely when `True`, rather than flipping it to `true` — filtering `IsSyndicated:true` would return only the syndicated subset, not the union of native + syndicated. Syndicated reviews are ones Bazaarvoice copies onto a retailer's listing from the manufacturer's own site (`SourceClient`/`IsSyndicated`); retailers' own storefronts typically don't display them, so they're excluded by default to match what's visible on the retailer's site. `category_map` (optional dict, passed via registry kwargs) maps the first 4 digits of `CategoryId` to a human-readable label; falls back to the raw `CategoryId` string if the prefix isn't mapped
- **`src/scrapers/sephora_html.py`** — Playwright/Camoufox HTML scraper for sephora.it; see section below
- **`src/scrapers/notino.py`** — `NotinoScraper` for notino.it: Camoufox renders the brand page to extract products from SSR-embedded JSON (`masterProductCode`/`name`/`url`), then reviews are fetched via a plain `requests`-based Apollo Persisted Query POST to `/api/product/` (not Cloudflare-gated, unlike the HTML pages) using the `getReviews` operation; `_REVIEWS_HASH` (overridable via `NOTINO_REVIEWS_HASH`) is the persisted-query hash, recapture from DevTools if Notino redeploys; `supports_backfill = False` — descending `since`-based pagination only, no cursor needed
- **`src/scrapers/marionnaud.py`** — `MarionnaudScraper` for marionnaud.it, which runs PowerReviews (not Bazaarvoice/Sephora's stack despite Marionnaud also being beauty-retail). Product discovery is Camoufox-driven: `www.marionnaud.it` and `api.marionnaud.it` both sit behind the same Akamai edge as Sephora, so the brand catalog is fetched via `page.evaluate(fetch(...))` inside an already-loaded page against the SAP Commerce (Hybris) OCC search API (`api.marionnaud.it/api/v2/mit-spa/search?categoryCode=...`), paginated at `pageSize=40` (Hybris silently caps any higher value). The brand's numeric `categoryCode` is looked up from `/brandslist` by normalized link text (alnum-only, casefolded) rather than a guessed URL slug, since slugs don't always match the brand name (e.g. "Dolce & Gabbana" → `/dolce-gabbana/b/0108`, link text `"Dolce&Gabbana"`). Reviews, by contrast, need **no browser at all**: PowerReviews' `display.powerreviews.com` display API is a separate, non-Akamai-gated domain, so `scrape_reviews()` uses plain `requests` like `BazaarvoiceScraper`. `_MERCHANT_ID`/`_APIKEY` (overridable via `MARIONNAUD_MERCHANT_ID`/`MARIONNAUD_APIKEY` env vars) are the PowerReviews account credentials, scraped from a `powerreviews.merchantgroup/apikey.it_IT` config blob embedded in the homepage — stable until Marionnaud rotates accounts. `paging.size` is capped server-side at 25. Unlike Bazaarvoice retailers, Marionnaud's own product pages display syndicated reviews (verified: a product's displayed "101 recensioni" count matches the API's `total_results` including `source: "syndicated"` entries), so — deliberately, unlike `BazaarvoiceScraper` — there's no syndicated-exclusion filter; all reviews are included to match what's shown on the site. `supports_backfill = False` — descending (`sort=Newest`) `since`-based pagination only, no cursor needed, same reasoning as Notino (no per-request bot-detection cost on the PowerReviews API)
- **`src/scrapers/sensation.py`** — `SensationScraper` for sensationprofumerie.it, an Angular SPA backed by a plain Express/JSON API on `api.sensationprofumerie.it`. **No browser needed** — unlike the `www` frontend (behind Cloudflare), the `api.*` subdomain has no bot gating, so both discovery and reviews use plain `requests` like `BazaarvoiceScraper`. **Discovery uses the product SITEMAP, not the search API.** `GET /api/indexing/search?brands={name}` is a curated autocomplete index capped at ~264 hits/brand and misses the bulk of a brand's catalog (e.g. DIOR: 264 via search vs ~950 real products — verified), and the site's own brand grid uses that same capped index. So `discover_products()` instead: (1) resolves the brand record (`brandId`/`slug`/exact `name`) from `GET /api/brands` via `_resolve_brand()`, tolerant (alnum-only, casefolded) match on name or slug; (2) fetches `https://www.sensationprofumerie.it/sitemap/prodotti_it.xml` and keeps every product slug starting with the brand slug — a *loose* prefix (no trailing `-`) so fused brand-name slugs like `diorshow-…`/`diorskin-…` are included, with the trailing `-P<id>` giving the product id; (3) confirms each candidate's `brandId` against `GET /api/products/{id}` (cheap, un-gated) to drop slug-prefix collisions, taking the real `title` and the public `source_url` from it. Stale sitemap entries (discontinued products) that 404 on detail are skipped, not fatal. Reviews come from `GET /api/products/{id}/reviews` which returns the **entire list in one call, no pagination**, newest-first — so `scrape_reviews()` early-stops via `_past_cutoff()` once a review predates `since`. The reviews endpoint **aggregates a product's reviews across its sibling/variant productIds** (returned reviews carry their own differing `productId`); the cross-variant overlap is harmless since `UNIQUE(source_site, external_review_id)` + `ON CONFLICT DO NOTHING` keeps each review once. Reviews are syndicated from third-party aggregators (`provider` is `trustpilot`/`feedaty`); there's no title and no per-review verified-purchase flag, so `from_sensation()` sets `title=None`/`verified=False`. Product detail exposes no product-category field (only `line`, the fragrance line), so `category` is left unset. `supports_backfill = False` — full single-call fetch each run, same reasoning as Notino/Marionnaud (no per-request bot-detection cost). Enable with `SENSATION_ENABLED=1` (also accepts `true`/`yes`/`on`).
- **`src/scrapers/shopify_judgeme.py`** — `ShopifyJudgemeScraper`, the shared base for **Shopify storefronts whose reviews are powered by the Judge.me app** (`ditano`, `pinalli`). No browser — both halves use plain `requests`. Discovery uses Shopify's public `GET {products_base}/products.json?limit=250&page=N` feed (paginated to exhaustion), keeping products whose `vendor` matches the brand by tolerant (alnum-only, casefolded) compare. Reviews use Judge.me's **public widget endpoint** `GET https://judge.me/reviews/reviews_for_widget?shop_domain={myshopify}&platform=shopify&product_id={shopify_id}&page&per_page` — no API token (it's the same request the storefront widget makes); the `product_id` is the Shopify product id (identical to the products.json id), and pagination is bounded by the response's `total_count`. The endpoint returns `{html, total_count, page}` where `html` is rendered review markup, parsed with BeautifulSoup/lxml in `_parse_review_widget()` (each `.jdgm-rev` element's data-attributes + child spans give review-id/score/timestamp/author/title/body/verified-buyer). The widget's review order isn't a guaranteed date-sort, so `scrape_reviews()` filters each review against `since` individually (skip, not early-stop) — fine because these stores' per-product review counts are small. `from_judgeme(raw, source_site)` normalizes the extracted dict. `supports_backfill = False`. Subclasses set `site_name`, `products_base` (origin serving products.json), `storefront_base` (public origin for `source_url`), `shop_domain` (Judge.me key), and `category_from_product_type` (True only when Shopify `product_type` holds real categories).
- **`src/scrapers/ditano.py`** — `DitanoScraper` (`ShopifyJudgemeScraper` subclass) for ditano.com. products.json is served from the storefront itself (`ditano.com`), and Shopify `product_type` holds real category labels (`Fragranze`, `Skincare`, `Makeup`, `Hair`, `Solari` → mapped to `category_group` in `src/categories.py`), so `category_from_product_type = True`. `DITANO_SHOP_DOMAIN` overridable. `from_ditano()` delegates to `from_judgeme(raw, "ditano")`. Enable with `DITANO_ENABLED=1`.
- **`src/scrapers/pinalli.py`** — `PinalliScraper` (`ShopifyJudgemeScraper` subclass) for pinalli.it, a **headless Shopify** store (Next.js frontend). **Reviews** are inherited unchanged (Judge.me widget, keyed by the backend myshopify domain `pinalli-headless-prod.myshopify.com`). **Discovery unions two sources** because neither alone is complete (measured for DIOR: products.json 702, Algolia 342, overlap only ~186 → union ~858; both already include out-of-stock products): (1) the inherited products.json scan (`products_base` = the backend myshopify domain, since the `www` frontend is Cloudflare-gated) — capped at Shopify's 100-page / 25k-product limit, so it misses this ~38k store's tail but catches many products the storefront search index doesn't list; (2) Algolia (`_algolia_vendor_hits()`), the index powering the storefront's brand pages — no page cap, server-side `vendor` facet filter (case-insensitive), POST to `{app}-dsn.algolia.net/1/indexes/{index}/query`, paged by `nbPages` — which catches products beyond products.json's 25k window. Merged by Shopify product id (Algolia `id` == products.json id == Judge.me `product_id`; Algolia `objectID` is the *variant* id, unused). Public Algolia search-only key/app/index (`VN9XEZ6ACP`/`headless_products`) are scraped from the storefront JS — overridable via `PINALLI_ALGOLIA_APP`/`PINALLI_ALGOLIA_KEY`/`PINALLI_ALGOLIA_INDEX`; `PINALLI_PRODUCTS_BASE`/`PINALLI_SHOP_DOMAIN` also overridable. `storefront_base` (product `source_url`) is `https://www.pinalli.it`. `product_type` is SKU/barcode junk, so `category` is left null. Note: products.json paging is the slow part — discovery pages up to 100 pages of the whole catalog per brand (a caching/optimization opportunity if scraping many brands). Enable with `PINALLI_ENABLED=1`.
- **`src/categories.py`** — `CATEGORY_GROUP` dict mapping granular category labels → broad group (`Fragrance`, `Makeup`, `Skincare`, `Body Care`, `Haircare`); `category_group(label)` helper returns the group or `None`; shared by `runner.py` (writes to DB) and `exporter.py` (reads from DB). When adding new category labels in `src/scrapers/__init__.py`, add the corresponding group entry here too.
- **`src/normalizer.py`** — `NormalizedReview` dataclass + `ReviewNormalizer.from_bazaarvoice()`, `.from_sephora()`, `.from_notino()`, `.from_marionnaud()`, `.from_sensation()`, `.from_judgeme()` (shared by Shopify+Judge.me sites; `.from_ditano()` delegates to it)
- **`src/models.py`** — five SQLAlchemy tables: `brands`, `products`, `reviews`, `scrape_runs`, `sephora_backfill_cursors`; `SiteEnum` includes `bazaarvoice`, `sephora`, `notino`, `marionnaud`, `sensation`, `ditano`, and `pinalli`; `Product` unique constraint is `(source_site, external_id, retailer)` — `retailer` is part of the key so two Bazaarvoice retailers can't collide on the same `external_id`; `Product.category` is a nullable `String(255)` for the granular label (e.g. `"Lipstick"`); `Product.category_group` is a nullable `String(255)` for the broad group (e.g. `"Makeup"`), computed from `category` via `src/categories.py` and stored by `_upsert_product()`
- **`src/database.py`** — `get_session()` context manager (commit on exit, rollback on exception)
- **`src/exporter.py`** — `export_brand(brand_name, fmt, output_path, product_filter, product_id_filter)`; includes both `product_category` (granular) and `product_category_group` (broad group) columns in CSV/JSON output, read directly from the DB

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

Reviews: `UNIQUE(source_site, external_review_id)` — inserts use `ON CONFLICT DO NOTHING`, batched per-product via `_upsert_reviews()`; returns the count of rows actually inserted via `RETURNING`.
Products: `UNIQUE(source_site, external_id, retailer)` — upserts use `ON CONFLICT DO UPDATE SET category = ...` so existing products get their `category` backfilled when re-discovered (e.g. after category mapping is first added for a retailer).

### Incremental scraping

Before each brand scrape (and before a `--product-id` single-product scrape), `runner.py` queries the last successful `ScrapeRun.finished_at` for that site. This timestamp is passed as `since` to `scraper.scrape_reviews()`. Both scrapers stop paginating when they encounter reviews older than `since`, via the shared `BaseScraper._past_cutoff()` helper.

`run_single_product` requires the product to have been discovered by a prior full `scrape` (so its `source_url` is known); scrapers that need a real URL to navigate (e.g. `SephoraHTMLScraper`) raise a clear error if it's missing instead of failing with an opaque browser error.

### Backfill cursor (Sephora only)

Live-tested against sephora.it: scraping one product's full review history in one continuous
run (~305 requests over ~10 minutes for a 6,700-review product) reliably trips Akamai's
request-volume-based blocking — and the block applies to the IP broadly (confirmed: blocked
even for plain page loads to a *different* product immediately after, from the same IP that
still browses fine in a real browser). Refreshing the browser/cookies between products does
not help; this is server-side IP-level risk scoring, not a client-side fingerprint issue.

To stay under that threshold, scrapers can opt into a persisted, capped backfill mechanism:
- `BaseScraper.supports_backfill` (default `False`) — set `True` on scrapers whose
  `scrape_reviews()` honors `backfill_offset`/`max_backfill_pages`. Only `SephoraHTMLScraper`
  sets this; `BazaarvoiceScraper` ignores the params (its REST API has no per-request
  bot-detection cost, so it just does a full `since`-based pagination every run).
- `sephora_backfill_cursors` table (`product_id` unique FK, `offset`, `completed`) persists
  how far the **ascending**-sort (oldest-first) pass has reached per product. Ascending order
  keeps the offset stable across runs — new reviews append at the *end* of an ascending list,
  so they never shift earlier offsets the way they would in descending order.
- `runner.py`'s `_scrape_product()` helper reads the cursor before calling `scrape_reviews()`,
  passes `max_backfill_pages=SEPHORA_BACKFILL_PAGES_PER_RUN` (env var, default 5 → ~110
  reviews/run, well under the ~305 that tripped blocking in testing), then reads
  `scraper.backfill_offset`/`scraper.backfill_completed` (set by the scraper as a side effect,
  since a generator can't also return a value) and upserts the cursor.
- `SephoraHTMLScraper.scrape_reviews()` runs up to two passes per call:
  1. **Watermark** (descending, stops at `since`) — only runs when `since is not None`. On a
     brand-new product (no prior successful run, `since=None`) this pass is skipped entirely,
     since there's no cutoff to bound it and it would otherwise walk the *entire* history before
     the backfill cap even applies.
  2. **Backfill** (ascending, capped at `max_backfill_pages`) — runs whenever the cursor isn't
     `completed`, continuing from the persisted offset. Once it reaches the end of the review
     list (`reviewCount` or an empty page), it marks `completed=True` and the watermark pass
     alone keeps the product up to date in future runs.

### Adding a new Bazaarvoice retailer

Add to `.env` only — no code changes:
```
BV_PASSKEY_<RETAILER>=<key>
BV_LOCALE_<RETAILER>=<locale>
```
Auto-registers as `bazaarvoice_<retailer>` in `SCRAPER_REGISTRY`.

### SephoraHTMLScraper

Browser-driven scraper for sephora.it: Camoufox (anti-bot Firefox) renders the product page,
then the review data is fetched by running `fetch()` *inside that page* against a Next.js
Server Action endpoint — not via a separate Python HTTP client.

**Why fetch() runs inside the page, not via `requests`:** an early version exported the
browser's cookies to a plain `requests.post()` call. Akamai's edge WAF rejected it outright
(generic "Access Denied" page) because the request didn't carry the headers a real browser
fetch produces — `sec-fetch-*`, `sec-ch-ua*`, and especially `next-router-state-tree` (Next.js's
own route-fingerprint header), several of which JS can't even set manually (the browser sets
them itself based on real context). Cookies alone aren't sufficient; the whole request shape is
checked. Issuing the POST via `page.evaluate()` while the tab is open sidesteps this entirely —
the browser produces a request indistinguishable from a real user's, including TLS fingerprint.

**Product discovery flow** (fully browser-based — needs rendered links):
1. Loads `https://www.sephora.it/{brand_lower}/{brand_upper}-HubPage.html` to find all `?scgid=C*` category tab URLs (logs a WARNING if the hub looks Akamai-blocked/empty).
2. For each category, navigates to `https://www.sephora.it/marche/dalla-a-alla-z/{brand_lower}-{brand_lower}/?scgid=CXX&sz=300` and reads the rendered product grid from the **DOM**, scrolling until the product-link count stabilises (the grid lazy-loads). `sz=300` lifts the grid's default 24-item page; the `/{brand}-{brand}/` catalog path is itself brand-scoped, so no separate brand filter is needed.
3. Extracts all `<a href>` links matching `-(P\d+)\.html`, deduplicates by product ID. Per-category counts are logged.

**Cross-brand cleanup (gradual, Akamai-safe):** the *old* discovery bug (pre-`cgid`+brand-filter era, and again after Sephora's client-render migration broke the filter) filed products from many brands under whatever brand was being scraped — e.g. ~870 products under "Dior" were mostly Chloé/Hugo Boss/Kayali/Armani/YSL. `SephoraHTMLScraper.fetch_brand(product)` reads a product's *true* brand from its rendered page (the `<h1>`'s first line + the adjacent `/marche/dalla-a-alla-z/<slug>/` link; parsed by `_BRAND_JS`/`_brand_slug_from_href`). It **raises on an Akamai "Access Denied" page** so a block is never mistaken for a wrong brand (which would wrongly delete a real product). `runner.py::_sephora_cleanup_batch()` verifies up to `SEPHORA_VERIFY_PER_RUN` (env, default 15) not-yet-checked products per run — capped like the backfill to stay under Akamai's volume threshold — deleting those whose real brand ≠ the scraped brand and marking the rest `products.brand_checked=True` so the whole DB is cleaned over successive runs. It runs at the end of `run_brand` for `source_site == "sephora"` only. **Do not raise the cap much** — each check is a full product-page load on top of the run's review scraping; 870 rapid loads reliably trips the IP block (verified).

**Why DOM extraction, not the SFCC AJAX endpoint:** discovery previously fetched `Search-Show?...&prefn1=brand&prefv1={brand}&format=ajax` inside the tab. As of mid-2026 Sephora moved the category grid to **client-side rendering** — that endpoint now returns an empty page shell and the `prefv1=brand` refinement returns 0 results, so discovery silently returned 0 products. Product *page* URLs are unchanged (`/p/...-P\d+.html`), so review scraping was unaffected. Navigation is retried up to 3× on transient `NS_ERROR_NET_HTTP3_PROTOCOL_ERROR` (a flaky Camoufox↔Sephora QUIC error that otherwise drops whole categories). If this regresses again, run `discover_products` locally — it now logs hub-page block state, category-id count, and per-category product counts to pinpoint the break.

**Review scraping flow:**
- `scrape_reviews()` opens the product page once (`_wait_and_consent`), then repeatedly calls
  `page.evaluate(_FETCH_JS, ...)` on that same page to POST to the product page's own URL with
  a `next-action` header — this tells Next.js to execute the `getReviews` server function
  instead of rendering the page. Payload is a positional JSON array:
  `[productId, offset, limit, ratingFilter, sortOrder]`
  (e.g. `["P2266017", 0, 22, [], "SubmissionTime:desc"]`); paginated 22 reviews at a time.
- `_router_state_tree(slug)` builds the required `next-router-state-tree` header value —
  a JSON route descriptor Next.js needs to resolve which server action to run — by templating
  in the product's URL slug and the fixed `it-IT` locale segment, then `urllib.parse.quote`-ing
  the compact JSON (mirrors what Next.js's own client runtime sends).
- Response is a Next.js RSC stream (`Content-Type: text/x-component`), not JSON — lines look
  like `0:{...}` / `1:{...}`; `_parse_rsc()` finds the line whose JSON has a `data.reviews` key
  and returns that `data` dict (`reviewCount` + `reviews` array).
- `NEXT_ACTION_ID` (module constant, overridable via `SEPHORA_NEXT_ACTION_ID` env var) is a
  hash of the server function — stable until Sephora redeploys. If requests start coming back
  as HTML instead of an RSC stream, this ID has likely changed and needs recapturing from
  DevTools.
- `ReviewNormalizer.from_sephora()` parses the review JSON directly (no DOM scraping): strips
  the RSC `"$D"` date-type prefix from `createdAt`, treats the literal string `"$undefined"` as
  `None`, and uses `purchaserType == "BUYER"` as the verified-purchase signal.
  `external_review_id` is the site's own numeric `id` field — no hashing needed.
- If a fetch returns non-200 or `_parse_rsc()` can't find the expected payload, the page is
  reloaded once (refreshing Akamai cookies) and the same offset is retried; a second failure
  raises rather than retrying indefinitely.

**Browser lifecycle:**
One browser + one page is held open for the whole `scrape_reviews()` call (discovery, on the
other hand, opens/closes a page per category). `close()` explicitly tears down the Camoufox/
Firefox process; `runner.py` calls it in a `finally` block after each `run_brand`/
`run_single_product`. `__del__` just calls `close()` as a GC-time safety net — don't rely on
`__del__` alone for cleanup.

**Dockerfile patches for Playwright Firefox:**
The `coreBundle.js` driver crashes when bot-detection JS throws errors without location info. Three sed patches in the Dockerfile add optional chaining + fallback defaults to `pageError.location` properties. A post-sed `grep` check fails the build loudly if the patch didn't apply (e.g. after a Playwright version bump changes the bundled file). `playwright` is pinned explicitly in `requirements.txt` to the version the patch was verified against — if you bump it, rebuild and confirm the build still succeeds (it will fail fast if the patch no-ops) and re-verify the path/version pair, since playwright's driver bundle layout can change between versions.

**Enabling Sephora:**
```
SEPHORA_ENABLED=1   # also accepts true/yes/on, in .env or as -e flag to docker run
```
