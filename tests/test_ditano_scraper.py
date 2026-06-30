"""
Tests for DitanoScraper (ditano.com — Shopify storefront + Judge.me reviews).

The parsing/normalizer unit tests run with no network. The live integration tests hit
Shopify's products.json and Judge.me's public widget endpoint, and are skipped unless
DITANO_ENABLED is set.

Run the live tests with:
    DITANO_ENABLED=1 .venv/bin/python -m pytest tests/test_ditano_scraper.py -v
"""
import os

import pytest

DITANO_ENABLED = os.environ.get("DITANO_ENABLED", "").lower() in ("1", "true", "yes", "on")

# A known reviewed product (Calvin Klein CK Sheer Peach Hair & Body Perfume Mist).
REVIEWED_PRODUCT = {
    "external_id": "10359391486116",
    "name": "CK Sheer Peach Hair & Body Perfume Mist",
    "source_url": "https://ditano.com/products/ck-sheer-peach-hair-body-perfume-mist",
}

# A real two-review Judge.me widget HTML fragment (trimmed) for deterministic parse tests.
_WIDGET_HTML = """
<div class='jdgm-rev-widg__reviews'>
  <div class='jdgm-rev jdgm-divider-top' data-verified-buyer='true'
       data-review-id='ff6e78f7-fd0f-4d56-9263-1e002b379a0f' data-thumb-up-count='3'>
    <div class='jdgm-rev__header'>
      <span class='jdgm-rev__rating' data-score='5'></span>
      <span class='jdgm-rev__timestamp' data-content='2025-10-06 15:19:30 UTC'></span>
      <span class='jdgm-rev__author'>Marzia</span>
    </div>
    <div class='jdgm-rev__content'>
      <b class='jdgm-rev__title'>Body mist CK Sheer Peach</b>
      <div class='jdgm-rev__body'><p>Sentore di pesca zuccherina. La consiglio vivamente.</p></div>
    </div>
  </div>
  <div class='jdgm-rev' data-verified-buyer='false' data-review-id='aaa-222' data-thumb-up-count='0'>
    <div class='jdgm-rev__header'>
      <span class='jdgm-rev__rating' data-score='4'></span>
      <span class='jdgm-rev__timestamp' data-content='2025-09-01 09:00:00 UTC'></span>
      <span class='jdgm-rev__author'>Anon</span>
    </div>
    <div class='jdgm-rev__content'>
      <div class='jdgm-rev__body'><p>Buono.</p></div>
    </div>
  </div>
</div>
"""


# ── pure unit tests (no network) ──────────────────────────────────────────────────


def test_parse_widget_extracts_both_reviews():
    from src.scrapers.ditano import _parse_review_widget

    rows = _parse_review_widget(_WIDGET_HTML)
    assert len(rows) == 2
    first = rows[0]
    assert first["review_id"] == "ff6e78f7-fd0f-4d56-9263-1e002b379a0f"
    assert first["score"] == "5"
    assert first["author"] == "Marzia"
    assert first["title"] == "Body mist CK Sheer Peach"
    assert "pesca zuccherina" in first["body"]
    assert first["verified"] is True
    assert first["thumb_up"] == "3"


def test_from_ditano_normalizes_fields():
    from src.scrapers.ditano import _parse_review_widget
    from src.normalizer import ReviewNormalizer

    rows = _parse_review_widget(_WIDGET_HTML)
    r0 = ReviewNormalizer.from_ditano(rows[0])
    assert r0.external_review_id == "ff6e78f7-fd0f-4d56-9263-1e002b379a0f"
    assert r0.source_site == "ditano"
    assert r0.rating == 5.0
    assert r0.author == "Marzia"
    assert r0.review_date is not None and r0.review_date.year == 2025
    assert r0.helpful_count == 3
    assert r0.verified is True

    r1 = ReviewNormalizer.from_ditano(rows[1])
    assert r1.verified is False
    assert r1.title is None  # second review has no title element


def test_from_ditano_handles_missing_author():
    from src.normalizer import ReviewNormalizer

    r = ReviewNormalizer.from_ditano(
        {"review_id": "x", "score": None, "author": None, "title": None, "body": None,
         "timestamp": None, "thumb_up": None, "verified": False}
    )
    assert r.author == "Anonymous"
    assert r.rating is None
    assert r.helpful_count == 0


# ── live integration tests ────────────────────────────────────────────────────────


@pytest.mark.skipif(not DITANO_ENABLED, reason="DITANO_ENABLED not set")
class TestDitanoLive:

    def test_discovery_filters_by_vendor(self):
        """products.json discovery returns a brand's products (Calvin Klein ~30)."""
        from src.scrapers.ditano import DitanoScraper

        scraper = DitanoScraper()
        products = scraper.discover_products("calvin klein")  # lowercase on purpose
        assert len(products) >= 20, f"Expected ~30 Calvin Klein products, got {len(products)}"
        assert all(p["external_id"].isdigit() for p in products)
        assert all(p["source_url"].startswith("https://ditano.com/products/") for p in products)

    def test_scrape_reviews_known_product(self):
        from src.scrapers.ditano import DitanoScraper

        scraper = DitanoScraper()
        reviews = list(scraper.scrape_reviews(REVIEWED_PRODUCT, since=None))
        assert len(reviews) >= 1, "Expected at least 1 review for the known reviewed product"
        r = reviews[0]
        assert r.source_site == "ditano"
        assert r.external_review_id
        assert r.rating is not None

    def test_sauvage_edp_review_count(self):
        """https://ditano.com/products/sauvage-eau-de-parfum (Shopify id 8807626866852)
        has 50 Judge.me reviews — this also exercises pagination (per_page=30 → 2 pages).
        Floor (>=) so the test survives new reviews; all ids must be unique."""
        from src.scrapers.ditano import DitanoScraper

        sauvage = {
            "external_id": "8807626866852",
            "name": "Sauvage Eau de Parfum",
            "source_url": "https://ditano.com/products/sauvage-eau-de-parfum",
        }
        scraper = DitanoScraper()
        reviews = list(scraper.scrape_reviews(sauvage, since=None))
        assert len(reviews) >= 50, f"Expected >=50 reviews for Sauvage EDP, got {len(reviews)}"
        # No duplicates across paginated pages.
        assert len({r.external_review_id for r in reviews}) == len(reviews)
