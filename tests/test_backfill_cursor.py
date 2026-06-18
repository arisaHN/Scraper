"""
Integration tests for the Sephora backfill-cursor bookkeeping in
src/runner.py::_scrape_product. Uses a fake scraper (no browser, no network) so only
the database interaction is under test. Requires DATABASE_URL (real Postgres) since the
cursor upsert uses Postgres-specific ON CONFLICT — skipped automatically otherwise.

Run with:
    .venv/bin/python -m pytest tests/test_backfill_cursor.py -v
"""
import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


class FakeSephoraScraper:
    """Test stand-in for SephoraHTMLScraper — yields canned reviews and sets the
    backfill_* attributes _scrape_product() reads, without any browser or network call."""

    supports_backfill = True

    def __init__(self, reviews, offset, completed, total, raise_after=False):
        self._reviews = reviews
        self._offset = offset
        self._completed = completed
        self._total = total
        self._raise_after = raise_after
        self.backfill_offset = None
        self.backfill_completed = False
        self.backfill_total = None

    def scrape_reviews(self, product, since=None, backfill_offset=None, max_backfill_pages=None):
        for review in self._reviews:
            yield review
        self.backfill_offset = self._offset
        self.backfill_completed = self._completed
        self.backfill_total = self._total
        if self._raise_after:
            raise RuntimeError("simulated mid-stream failure")


@pytest.fixture
def test_product():
    from src.database import get_session
    from src.models import Brand, Product, Review, SephoraBackfillCursor

    unique = uuid.uuid4().hex[:8]
    with get_session() as session:
        brand = Brand(name=f"__test_brand_{unique}")
        session.add(brand)
        session.flush()
        product = Product(
            brand_id=brand.id,
            name=f"__test_product_{unique}",
            source_site="sephora",
            source_url="https://www.sephora.it/p/test.html",
            external_id=f"P{unique}",
            retailer="sephora",
        )
        session.add(product)
        session.flush()
        brand_id, product_id = brand.id, product.id

    yield {"external_id": f"P{unique}", "name": f"__test_product_{unique}", "source_url": "https://www.sephora.it/p/test.html"}, product_id

    with get_session() as session:
        session.query(SephoraBackfillCursor).filter_by(product_id=product_id).delete()
        session.query(Review).filter_by(product_id=product_id).delete()
        session.query(Product).filter_by(id=product_id).delete()
        session.query(Brand).filter_by(id=brand_id).delete()


def _get_cursor(product_id):
    from src.database import get_session
    from src.models import SephoraBackfillCursor

    with get_session() as session:
        cursor = session.query(SephoraBackfillCursor).filter_by(product_id=product_id).first()
        if cursor is None:
            return None
        return {
            "offset": cursor.offset,
            "completed": cursor.completed,
            "total_reviews": cursor.total_reviews,
        }


def test_first_run_creates_cursor(test_product):
    from src.runner import _scrape_product

    prod_data, product_id = test_product
    scraper = FakeSephoraScraper(reviews=[], offset=44, completed=False, total=200)

    _scrape_product(scraper, product_id, prod_data, since=None, supports_backfill=True)

    cursor = _get_cursor(product_id)
    assert cursor == {"offset": 44, "completed": False, "total_reviews": 200}


def test_resuming_existing_cursor_updates_in_place(test_product):
    from src.database import get_session
    from src.models import SephoraBackfillCursor
    from src.runner import _scrape_product

    prod_data, product_id = test_product
    with get_session() as session:
        session.add(SephoraBackfillCursor(product_id=product_id, offset=44, completed=False, total_reviews=200))

    scraper = FakeSephoraScraper(reviews=[], offset=88, completed=False, total=200)
    _scrape_product(scraper, product_id, prod_data, since=None, supports_backfill=True)

    cursor = _get_cursor(product_id)
    assert cursor == {"offset": 88, "completed": False, "total_reviews": 200}

    from src.database import get_session as get_session2
    from src.models import SephoraBackfillCursor as Cursor2
    with get_session2() as session:
        count = session.query(Cursor2).filter_by(product_id=product_id).count()
    assert count == 1


def test_reaching_completion_flips_flag(test_product):
    from src.runner import _scrape_product

    prod_data, product_id = test_product
    scraper = FakeSephoraScraper(reviews=[], offset=200, completed=True, total=200)

    _scrape_product(scraper, product_id, prod_data, since=None, supports_backfill=True)

    cursor = _get_cursor(product_id)
    assert cursor["completed"] is True
    assert cursor["offset"] == 200


def test_mid_stream_exception_still_persists_progress(test_product):
    from src.normalizer import NormalizedReview
    from src.runner import _scrape_product

    prod_data, product_id = test_product
    review = NormalizedReview(
        external_review_id=f"r-{product_id}",
        source_site="sephora",
        author="Anonymous",
        rating=5.0,
        title="ok",
        text="ok",
        review_date=None,
    )
    scraper = FakeSephoraScraper(reviews=[review], offset=22, completed=False, total=200, raise_after=True)

    with pytest.raises(RuntimeError, match="simulated mid-stream failure"):
        _scrape_product(scraper, product_id, prod_data, since=None, supports_backfill=True)

    cursor = _get_cursor(product_id)
    assert cursor == {"offset": 22, "completed": False, "total_reviews": 200}

    from src.database import get_session
    from src.models import Review
    with get_session() as session:
        saved = session.query(Review).filter_by(product_id=product_id).count()
    assert saved == 1
