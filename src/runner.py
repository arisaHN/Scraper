from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from .database import get_session
from .models import Brand, Product, Review, RunStatus, ScrapeRun
from .scrapers import SCRAPER_REGISTRY
from .config import settings


def run_brand(brand_id: int, brand_name: str, site: str, ScraperClass) -> int:
    """Scrape one site for one brand. Returns count of reviews upserted."""
    with get_session() as session:
        run = ScrapeRun(brand_id=brand_id, site=site, status=RunStatus.running)
        session.add(run)
        session.flush()
        run_id = run.id

    count = 0
    try:
        kwargs = {}
        retailer = None
        if site == "bazaarvoice":
            kwargs["passkey"] = settings.BV_PASSKEY_DOUGLAS
            kwargs["locale"] = settings.BV_LOCALE
            retailer = settings.BV_RETAILER_DOUGLAS
        scraper = ScraperClass(**kwargs)
        products = scraper.discover_products(brand_name)
        print(f"  [{site}] {len(products)} products found", flush=True)

        for i, prod_data in enumerate(products, 1):
            try:
                prod_id = _upsert_product(brand_id, site, prod_data, retailer=retailer)
                prod_reviews = 0
                for review in scraper.scrape_reviews(prod_data):
                    _upsert_review(prod_id, review)
                    count += 1
                    prod_reviews += 1
                if prod_reviews:
                    print(f"  [{site}] ({i}/{len(products)}) {prod_data['name'][:50]} — {prod_reviews} reviews", flush=True)
            except Exception as exc:
                print(f"  [{site}] ({i}/{len(products)}) SKIP {prod_data.get('external_id')} — {exc}", flush=True)

        _finish_run(run_id, RunStatus.success, count)
    except Exception as exc:
        _finish_run(run_id, RunStatus.failed, count, str(exc)[:2000])
        raise

    return count


def run_single_product(brand_id: int, brand_name: str, site: str, ScraperClass, product_id: str) -> int:
    """Scrape one specific product by external ID. Skips discovery. Returns review count."""
    with get_session() as session:
        run = ScrapeRun(brand_id=brand_id, site=site, status=RunStatus.running)
        session.add(run)
        session.flush()
        run_id = run.id

    count = 0
    try:
        kwargs = {}
        retailer = None
        if site == "bazaarvoice":
            kwargs["passkey"] = settings.BV_PASSKEY_DOUGLAS
            kwargs["locale"] = settings.BV_LOCALE
            retailer = settings.BV_RETAILER_DOUGLAS
        scraper = ScraperClass(**kwargs)

        with get_session() as session:
            existing = session.query(Product).filter_by(source_site=site, external_id=product_id).first()
            product_name = existing.name if existing else product_id

        prod_data = {"external_id": product_id, "name": product_name, "source_url": ""}
        prod_id = _upsert_product(brand_id, site, prod_data, retailer=retailer)

        for review in scraper.scrape_reviews(prod_data):
            _upsert_review(prod_id, review)
            count += 1

        print(f"  [{site}] {product_name[:50]} — {count} reviews", flush=True)
        _finish_run(run_id, RunStatus.success, count)
    except Exception as exc:
        _finish_run(run_id, RunStatus.failed, count, str(exc)[:2000])
        raise

    return count


def run_all_sites(brand_name: str) -> dict[str, int]:
    """Run all scrapers for a brand. Returns {site: review_count}."""
    with get_session() as session:
        brand = session.query(Brand).filter_by(name=brand_name).first()
        if not brand:
            raise ValueError(f"Brand '{brand_name}' not found.")
        brand_id, bid_name = brand.id, brand.name

    results = {}
    for site, ScraperClass in SCRAPER_REGISTRY.items():
        try:
            results[site] = run_brand(brand_id, bid_name, site, ScraperClass)
        except Exception as exc:
            results[site] = -1
            print(f"[runner] {brand_name}/{site} failed: {exc}")
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
        # Already exists — look it up
        return (
            session.query(Product.id)
            .filter_by(source_site=site, external_id=prod_data.get("external_id"))
            .scalar()
        )


def _upsert_review(prod_id: int, review) -> None:
    with get_session() as session:
        stmt = (
            pg_insert(Review)
            .values(
                product_id=prod_id,
                source_site=review.source_site,
                external_review_id=review.external_review_id,
                author=review.author,
                rating=review.rating,
                title=review.title,
                text=review.text,
                review_date=review.review_date,
                helpful_count=review.helpful_count,
                verified=review.verified,
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
