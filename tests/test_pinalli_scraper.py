"""
Tests for PinalliScraper (pinalli.it — headless Shopify + Judge.me reviews).

Pinalli shares all its logic with DitanoScraper via ShopifyJudgemeScraper; the widget
parsing/normalizer is already covered by test_ditano_scraper.py, so here we mainly assert
the Pinalli-specific wiring (backend products.json domain, public source_url, source_site)
plus a couple of live checks.

Run the live tests with:
    PINALLI_ENABLED=1 .venv/bin/python -m pytest tests/test_pinalli_scraper.py -v
"""
import os

import pytest

PINALLI_ENABLED = os.environ.get("PINALLI_ENABLED", "").lower() in ("1", "true", "yes", "on")

# Phyto Scrub Lavante Purificante — a known product with at least one Judge.me review.
REVIEWED_PRODUCT = {
    "external_id": "7392331104321",
    "name": "Phyto Scrub Lavante Purificante",
    "source_url": "https://www.pinalli.it/products/trattamenti-capelli-phyto-scrub-lavante-purificante-1py0000000017",
}


# ── pure unit tests (no network) ──────────────────────────────────────────────────


def test_pinalli_config_targets_backend_and_public_frontend():
    from src.scrapers.pinalli import PinalliScraper

    assert PinalliScraper.site_name == "pinalli"
    # Reviews are keyed by the myshopify backend (the www frontend is Cloudflare-gated)…
    assert "myshopify.com" in PinalliScraper.shop_domain
    # …but product URLs point at the public storefront.
    assert PinalliScraper.storefront_base == "https://www.pinalli.it"
    # Discovery is overridden to use Algolia (products.json caps at 25k of ~38k products).
    assert "discover_products" in PinalliScraper.__dict__


def test_from_judgeme_sets_pinalli_source_site():
    from src.normalizer import ReviewNormalizer

    raw = {"review_id": "abc", "score": "5", "author": "Lia", "title": "Top",
           "body": "Ottimo", "timestamp": "2025-10-06 15:19:30 UTC",
           "thumb_up": "2", "verified": True}
    r = ReviewNormalizer.from_judgeme(raw, "pinalli")
    assert r.source_site == "pinalli"
    assert r.external_review_id == "abc"
    assert r.rating == 5.0
    assert r.verified is True


# ── live integration tests ────────────────────────────────────────────────────────


@pytest.mark.skipif(not PINALLI_ENABLED, reason="PINALLI_ENABLED not set")
class TestPinalliLive:

    def test_discovery_unions_products_json_and_algolia(self):
        """Discovery unions products.json + Algolia (neither alone is complete). For DIOR the
        union is ~858, well above either source alone (products.json ~702, Algolia ~342), so a
        floor above the products.json-only count proves the union is in effect. Slow: pages up
        to 100 products.json pages.
        """
        from src.scrapers.pinalli import PinalliScraper

        scraper = PinalliScraper()
        products = scraper.discover_products("dior")  # lowercase on purpose
        assert len(products) >= 750, f"Expected union (~858) of DIOR products, got {len(products)}"
        ids = {p["external_id"] for p in products}
        assert len(ids) == len(products), "Union must be deduped by product id"
        assert all(p["external_id"].isdigit() for p in products)
        assert all(p["source_url"].startswith("https://www.pinalli.it/products/") for p in products)
        assert all(p["category"] is None for p in products)  # product_type is junk here

    def test_scrape_reviews_known_product(self):
        from src.scrapers.pinalli import PinalliScraper

        scraper = PinalliScraper()
        reviews = list(scraper.scrape_reviews(REVIEWED_PRODUCT, since=None))
        assert len(reviews) >= 1, "Expected at least 1 review for the known reviewed product"
        r = reviews[0]
        assert r.source_site == "pinalli"
        assert r.external_review_id
        assert r.rating is not None
