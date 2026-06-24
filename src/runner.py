import os
import time
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

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
# continuous run was enough to trip Akamai's request-volume-based blocking, so this is
# kept well under that (5 pages = ~110 reviews/run) and the cursor persists progress
# across runs rather than fetching a product's full history in one pass.
SEPHORA_BACKFILL_PAGES_PER_RUN = int(os.environ.get("SEPHORA_BACKFILL_PAGES_PER_RUN", "5"))


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
                if n_fetched:
                    backfill_note = ""
                    if supports_backfill:
                        with get_session() as session:
                            cur = session.query(SephoraBackfillCursor).filter_by(product_id=prod_id).first()
                        if cur and cur.total_reviews:
                            pct = int(cur.offset * 100 / cur.total_reviews)
                            backfill_note = f" [backfill {cur.offset}/{cur.total_reviews} ({pct}%)]"
                    dup_note = f" ({n_fetched - n_inserted} already in DB)" if n_fetched != n_inserted else ""
                    print(f"  [{registry_key}] ({i}/{len(products)}) {prod_data['name'][:50]} — {n_inserted} new reviews{dup_note}{backfill_note}", flush=True)
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
            )
            # Update category on re-discovery so existing products get their label
            # filled in when the scraper starts returning category data for the first time.
            .on_conflict_do_update(
                constraint="uq_product_site_external",
                set_={"category": category},
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
