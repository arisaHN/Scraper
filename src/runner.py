import os
import time
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from .categories import category_group as _category_group
from .database import get_session
from .models import Brand, Product, Review, RunStatus, ScrapeRun, SephoraBackfillCursor
from .scrapers import SCRAPER_REGISTRY

# Consecutive per-product failures this high usually mean a live block (e.g. bot
# detection) rather than a one-off bad page — abort early instead of grinding
# through every remaining product with a doomed retry each.
MAX_CONSECUTIVE_FAILURES = 10

# Seconds to sleep after a per-product failure before trying the next product.
# Gives Akamai's request-volume risk score time to decay between blocked requests.
SKIP_RECOVERY_SLEEP = int(os.environ.get("SKIP_RECOVERY_SLEEP", "20"))

# How many backfill pages (22 reviews each) a scraper that supports_backfill may fetch
# per product per run. Calibrated empirically against sephora.it: ~305 requests in one
# continuous run was enough to trip Akamai's request-volume-based blocking. Default is 1
# page (~22 reviews/product/run) to minimise per-product request volume — the cursor
# persists progress across runs, so deep histories still complete, just over more runs.
# Raise via the env var only if you're comfortably clear of blocks.
SEPHORA_BACKFILL_PAGES_PER_RUN = int(os.environ.get("SEPHORA_BACKFILL_PAGES_PER_RUN", "1"))

# How many not-yet-verified Sephora products to brand-check per run. Each check is one page
# load, so this is capped (like the backfill) to stay under Akamai's volume threshold; the
# `brand_checked` flag persists progress so the whole DB is cleaned over successive runs.
SEPHORA_VERIFY_PER_RUN = int(os.environ.get("SEPHORA_VERIFY_PER_RUN", "15"))


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").casefold() if c.isalnum())


def _remove_product(product_id: int) -> None:
    """Delete a product plus its reviews and any Sephora backfill cursor."""
    with get_session() as session:
        session.query(SephoraBackfillCursor).filter_by(product_id=product_id).delete(synchronize_session=False)
        session.query(Review).filter_by(product_id=product_id).delete(synchronize_session=False)
        session.query(Product).filter_by(id=product_id).delete(synchronize_session=False)


def _sephora_cleanup_batch(brand_id: int, source_site: str, brand_name: str, scraper, cap: int) -> tuple[int, int]:
    """Verify the true brand of up to `cap` not-yet-checked products and delete any that
    belong to a different brand (cross-brand mislabels from the old discovery bug).

    An Akamai block raises inside `fetch_brand`, so a blocked page never causes a deletion;
    consecutive blocks abort the batch early (progress resumes next run via `brand_checked`).
    """
    target = _norm(brand_name)
    with get_session() as session:
        rows = (
            session.query(Product)
            .filter_by(source_site=source_site, brand_id=brand_id, brand_checked=False)
            .order_by(Product.id)
            .limit(cap)
            .all()
        )
        suspects = [(p.id, p.external_id, p.name, p.source_url) for p in rows]

    checked = removed = fails = 0
    for pid, ext, name, url in suspects:
        try:
            slug = scraper.fetch_brand({"external_id": ext, "source_url": url})
        except Exception as exc:
            fails += 1
            print(f"  [sephora-cleanup] block/error on {ext} — {exc}", flush=True)
            time.sleep(SKIP_RECOVERY_SLEEP)
            if fails >= MAX_CONSECUTIVE_FAILURES:
                print("  [sephora-cleanup] aborting batch (likely Akamai block)", flush=True)
                break
            continue
        fails = 0
        if slug is None:
            continue  # brand unreadable but page loaded — leave unchecked, retry next run
        if target and target in _norm(slug):
            with get_session() as session:
                session.query(Product).filter_by(id=pid).update({"brand_checked": True})
            checked += 1
        else:
            _remove_product(pid)
            removed += 1
            print(f"  [sephora-cleanup] removed mislabeled {ext} ({(name or '')[:40]}) → real brand '{slug}'", flush=True)
        scraper._polite_delay()

    if suspects:
        print(f"  [sephora-cleanup] batch done: {checked} confirmed, {removed} removed, "
              f"{len(suspects) - checked - removed} deferred", flush=True)
    return checked, removed


def run_brand(brand_id: int, brand_name: str, registry_key: str) -> int:
    """Scrape one registry entry for one brand. Returns count of reviews newly inserted."""
    entry = SCRAPER_REGISTRY[registry_key]
    source_site = entry["source_site"]
    retailer = entry["retailer"]
    scraper = entry["class"](**entry["kwargs"])
    supports_backfill = getattr(entry["class"], "supports_backfill", False)

    with get_session() as session:
        last_run = (
            session.query(ScrapeRun)
            .filter_by(brand_id=brand_id, site=source_site, status=RunStatus.success)
            .order_by(ScrapeRun.finished_at.desc())
            .first()
        )
        since = last_run.finished_at if last_run else None
        run = ScrapeRun(brand_id=brand_id, site=source_site, status=RunStatus.running)
        session.add(run)
        session.flush()
        run_id = run.id

    print(f"  [{registry_key}] since={since!r}", flush=True)

    count = 0
    try:
        # Sephora only: verify a capped batch of not-yet-checked products' true brand and
        # delete cross-brand mislabels left by the old discovery bug. Runs FIRST, before the
        # heavy review scraping, so it (a) always runs even when scraping later hits an Akamai
        # block and returns early, and (b) uses the freshest request budget of the run.
        if source_site == "sephora" and SEPHORA_VERIFY_PER_RUN > 0:
            try:
                _sephora_cleanup_batch(brand_id, source_site, brand_name, scraper, SEPHORA_VERIFY_PER_RUN)
            except Exception as exc:
                print(f"  [sephora-cleanup] skipped — {exc}", flush=True)

        products = scraper.discover_products(brand_name)
        print(f"  [{registry_key}] {len(products)} products found", flush=True)

        consecutive_failures = 0
        for i, prod_data in enumerate(products, 1):
            try:
                prod_id = _upsert_product(brand_id, source_site, prod_data, retailer=retailer)
                # New products have no prior scrape history — fetch their full review
                # history regardless of the site-wide since cutoff.
                with get_session() as session:
                    has_reviews = session.query(Review).filter_by(product_id=prod_id).first() is not None
                product_since = since if has_reviews else None
                n_fetched, n_inserted = _scrape_product(scraper, prod_id, prod_data, product_since, supports_backfill)
                count += n_inserted
                consecutive_failures = 0
                # Sephora: the product page was just loaded during scraping, so mark this
                # (brand-scoped discovery => genuine) product brand_checked for free — the
                # cleanup batch then only spends its capped budget on the un-scraped fakes.
                brand = getattr(scraper, "product_brand", None)
                if source_site == "sephora" and brand and _norm(brand_name) in _norm(brand):
                    with get_session() as session:
                        session.query(Product).filter_by(id=prod_id).update({"brand_checked": True})
                if n_inserted:
                    backfill_note = ""
                    if supports_backfill:
                        with get_session() as session:
                            cur = session.query(SephoraBackfillCursor).filter_by(product_id=prod_id).first()
                        if cur and cur.total_reviews:
                            pct = int(cur.offset * 100 / cur.total_reviews)
                            backfill_note = f" [backfill {cur.offset}/{cur.total_reviews} ({pct}%)]"
                    print(f"  [{registry_key}] ({i}/{len(products)}) {prod_data['name'][:50]} — {n_inserted} new reviews{backfill_note}", flush=True)
            except Exception as exc:
                consecutive_failures += 1
                print(f"  [{registry_key}] ({i}/{len(products)}) SKIP {prod_data.get('external_id')} — {exc}", flush=True)
                time.sleep(SKIP_RECOVERY_SLEEP)
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    msg = (
                        f"Aborted after {consecutive_failures} consecutive product failures "
                        f"(likely blocked) at product {i}/{len(products)}."
                    )
                    print(f"  [{registry_key}] {msg}", flush=True)
                    _finish_run(run_id, RunStatus.partial, count, msg)
                    return count

        _finish_run(run_id, RunStatus.success, count)
    except Exception as exc:
        _finish_run(run_id, RunStatus.failed, count, str(exc)[:2000])
        raise
    finally:
        scraper.close()

    return count


def run_single_product(brand_id: int, brand_name: str, registry_key: str, product_id: str) -> int:
    """Scrape one specific product by external ID. Skips discovery. Returns review count."""
    entry = SCRAPER_REGISTRY[registry_key]
    source_site = entry["source_site"]
    retailer = entry["retailer"]
    scraper = entry["class"](**entry["kwargs"])
    supports_backfill = getattr(entry["class"], "supports_backfill", False)

    with get_session() as session:
        last_run = (
            session.query(ScrapeRun)
            .filter_by(brand_id=brand_id, site=source_site, status=RunStatus.success)
            .order_by(ScrapeRun.finished_at.desc())
            .first()
        )
        since = last_run.finished_at if last_run else None
        run = ScrapeRun(brand_id=brand_id, site=source_site, status=RunStatus.running)
        session.add(run)
        session.flush()
        run_id = run.id

    count = 0
    try:
        with get_session() as session:
            existing = session.query(Product).filter_by(source_site=source_site, external_id=product_id, retailer=retailer).first()
            product_name = existing.name if existing else product_id
            product_url = existing.source_url if existing else None

        prod_data = {"external_id": product_id, "name": product_name, "source_url": product_url}
        prod_id = _upsert_product(brand_id, source_site, prod_data, retailer=retailer)

        n_fetched, count = _scrape_product(scraper, prod_id, prod_data, since, supports_backfill)

        dup_note = f" ({n_fetched - count} already in DB)" if n_fetched != count else ""
        print(f"  [{registry_key}] {product_name[:50]} — {count} new reviews{dup_note}", flush=True)
        _finish_run(run_id, RunStatus.success, count)
    except Exception as exc:
        _finish_run(run_id, RunStatus.failed, count, str(exc)[:2000])
        raise
    finally:
        scraper.close()

    return count


def run_all_sites(brand_name: str) -> dict[str, int]:
    """Run all scrapers for a brand. Returns {registry_key: review_count}."""
    with get_session() as session:
        brand = session.query(Brand).filter_by(name=brand_name).first()
        if not brand:
            raise ValueError(f"Brand '{brand_name}' not found.")
        brand_id, bid_name = brand.id, brand.name

    results = {}
    for registry_key in SCRAPER_REGISTRY:
        try:
            results[registry_key] = run_brand(brand_id, bid_name, registry_key)
        except Exception as exc:
            results[registry_key] = -1
            print(f"[runner] {brand_name}/{registry_key} failed: {exc}")
    return results


# ── helpers ──────────────────────────────────────────────────────────────────

def _scrape_product(scraper, prod_id: int, prod_data: dict, since, supports_backfill: bool) -> tuple[int, int]:
    """Run scrape_reviews for one product, upserting reviews and persisting the backfill
    cursor (if the scraper supports it) as they're produced rather than only after the
    generator finishes — so a mid-stream failure (e.g. a real block partway through a
    capped backfill pass) still saves whatever was fetched and advances the cursor,
    instead of discarding already-fetched pages and re-requesting them next run.

    Returns (fetched, inserted) counts."""
    backfill_offset = None
    max_backfill_pages = None
    existing_total = None
    if supports_backfill:
        with get_session() as session:
            cursor = session.query(SephoraBackfillCursor).filter_by(product_id=prod_id).first()
        if cursor:
            existing_total = cursor.total_reviews
        if not cursor or not cursor.completed:
            backfill_offset = cursor.offset if cursor else 0
            max_backfill_pages = SEPHORA_BACKFILL_PAGES_PER_RUN

    reviews = []
    inserted = 0
    try:
        for review in scraper.scrape_reviews(
            prod_data, since=since, backfill_offset=backfill_offset, max_backfill_pages=max_backfill_pages
        ):
            reviews.append(review)
    finally:
        inserted = _upsert_reviews(prod_id, reviews)
        if supports_backfill and backfill_offset is not None:
            new_offset = getattr(scraper, "backfill_offset", backfill_offset)
            new_completed = getattr(scraper, "backfill_completed", False)
            new_total = getattr(scraper, "backfill_total", None) or existing_total
            with get_session() as session:
                stmt = (
                    pg_insert(SephoraBackfillCursor)
                    .values(product_id=prod_id, offset=new_offset, completed=new_completed, total_reviews=new_total)
                    .on_conflict_do_update(
                        index_elements=["product_id"],
                        set_={
                            "offset": new_offset,
                            "completed": new_completed,
                            "total_reviews": new_total,
                            "updated_at": datetime.utcnow(),
                        },
                    )
                )
                session.execute(stmt)

    return len(reviews), inserted


def _upsert_product(brand_id: int, site: str, prod_data: dict, retailer: str = None) -> int:
    category = prod_data.get("category")
    group = _category_group(category)
    with get_session() as session:
        stmt = (
            pg_insert(Product)
            .values(
                brand_id=brand_id,
                source_site=site,
                name=prod_data["name"],
                source_url=prod_data.get("source_url"),
                external_id=prod_data.get("external_id"),
                retailer=retailer,
                category=category,
                category_group=group,
            )
            # Update category fields on re-discovery so existing products get their
            # labels filled in when the scraper starts returning category data.
            .on_conflict_do_update(
                constraint="uq_product_site_external",
                set_={"category": category, "category_group": group},
            )
            .returning(Product.id)
        )
        result = session.execute(stmt)
        return result.scalar_one()


def _upsert_reviews(prod_id: int, reviews: list) -> int:
    """Upsert all of one product's reviews in a single transaction.
    Returns the count of rows actually inserted (conflicts excluded)."""
    if not reviews:
        return 0
    with get_session() as session:
        stmt = (
            pg_insert(Review)
            .values(
                [
                    {
                        "product_id": prod_id,
                        "source_site": review.source_site,
                        "external_review_id": review.external_review_id,
                        "author": review.author,
                        "rating": review.rating,
                        "title": review.title,
                        "text": review.text,
                        "review_date": review.review_date,
                        "helpful_count": review.helpful_count,
                        "verified": review.verified,
                    }
                    for review in reviews
                ]
            )
            .on_conflict_do_nothing(constraint="uq_review_site_external")
            .returning(Review.id)
        )
        result = session.execute(stmt)
        return len(result.fetchall())


def _finish_run(run_id: int, status: RunStatus, count: int, error: str = None):
    with get_session() as session:
        session.query(ScrapeRun).filter_by(id=run_id).update(
            {
                "status": status,
                "reviews_found": count,
                "finished_at": datetime.utcnow(),
                "error_msg": error,
            }
        )
