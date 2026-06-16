from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from .database import get_session
from .models import Brand, Product, Review, RunStatus, ScrapeRun
from .scrapers import SCRAPER_REGISTRY


def run_brand(brand_id: int, brand_name: str, registry_key: str) -> int:
    """Scrape one registry entry for one brand. Returns count of reviews upserted."""
    entry = SCRAPER_REGISTRY[registry_key]
    source_site = entry["source_site"]
    retailer = entry["retailer"]
    scraper = entry["class"](**entry["kwargs"])

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
        products = scraper.discover_products(brand_name)
        print(f"  [{registry_key}] {len(products)} products found", flush=True)

        for i, prod_data in enumerate(products, 1):
            try:
                prod_id = _upsert_product(brand_id, source_site, prod_data, retailer=retailer)
                reviews = list(scraper.scrape_reviews(prod_data, since=since))
                _upsert_reviews(prod_id, reviews)
                count += len(reviews)
                if reviews:
                    print(f"  [{registry_key}] ({i}/{len(products)}) {prod_data['name'][:50]} — {len(reviews)} reviews", flush=True)
            except Exception as exc:
                print(f"  [{registry_key}] ({i}/{len(products)}) SKIP {prod_data.get('external_id')} — {exc}", flush=True)

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
            existing = session.query(Product).filter_by(source_site=source_site, external_id=product_id).first()
            product_name = existing.name if existing else product_id
            product_url = existing.source_url if existing else None

        prod_data = {"external_id": product_id, "name": product_name, "source_url": product_url}
        prod_id = _upsert_product(brand_id, source_site, prod_data, retailer=retailer)

        reviews = list(scraper.scrape_reviews(prod_data, since=since))
        _upsert_reviews(prod_id, reviews)
        count = len(reviews)

        print(f"  [{registry_key}] {product_name[:50]} — {count} reviews", flush=True)
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

def _upsert_product(brand_id: int, site: str, prod_data: dict, retailer: str = None) -> int:
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
            )
            .on_conflict_do_nothing(constraint="uq_product_site_external")
            .returning(Product.id)
        )
        result = session.execute(stmt)
        row = result.fetchone()
        if row:
            return row[0]
        return (
            session.query(Product.id)
            .filter_by(source_site=site, external_id=prod_data.get("external_id"), retailer=retailer)
            .scalar()
        )


def _upsert_reviews(prod_id: int, reviews: list) -> None:
    """Upsert all of one product's reviews in a single transaction."""
    if not reviews:
        return
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
        )
        session.execute(stmt)


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
