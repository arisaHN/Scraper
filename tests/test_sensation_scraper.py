"""
Tests for SensationScraper (sensationprofumerie.it).

The unit tests for the normalizer run with no network. The live integration tests
hit the real api.sensationprofumerie.it JSON API and are skipped unless
SENSATION_ENABLED is set.

Run the live tests with:
    SENSATION_ENABLED=1 .venv/bin/python -m pytest tests/test_sensation_scraper.py -v
"""
import os

import pytest

SENSATION_ENABLED = os.environ.get("SENSATION_ENABLED", "").lower() in ("1", "true", "yes", "on")

# Dior Sauvage Eau de Parfum — a high-review-count product that won't disappear.
PRODUCT = {
    "external_id": "117283",
    "name": "Dior Sauvage Eau De Parfum (Sensation)",
    "source_url": "https://api.sensationprofumerie.it/api/products/117283",
}


# ── pure unit tests (no network) ──────────────────────────────────────────────────


def test_from_sensation_parses_fields():
    from src.normalizer import ReviewNormalizer

    raw = {
        "provider": "trustpilot",
        "reviewId": "69fcd882d63c1301a4213cae",
        "rating": 4,
        "text": "Ottimo profumo",
        "creationDate": "2026-05-07T18:22:58.938Z",
        "authorName": "Adolfo Di Giovambattista",
        "productId": "117284",
    }
    r = ReviewNormalizer.from_sensation(raw)
    assert r.external_review_id == "69fcd882d63c1301a4213cae"
    assert r.source_site == "sensation"
    assert r.author == "Adolfo Di Giovambattista"
    assert r.rating == 4.0
    assert r.title is None
    assert r.text == "Ottimo profumo"
    assert r.review_date is not None and r.review_date.year == 2026
    assert r.verified is False


def test_from_sensation_handles_missing_author():
    from src.normalizer import ReviewNormalizer

    r = ReviewNormalizer.from_sensation(
        {"reviewId": "x1", "rating": None, "text": None, "creationDate": None, "authorName": ""}
    )
    assert r.author == "Anonymous"
    assert r.rating is None
    assert r.text is None
    assert r.review_date is None


# ── live integration tests ────────────────────────────────────────────────────────


@pytest.mark.skipif(not SENSATION_ENABLED, reason="SENSATION_ENABLED not set")
class TestSensationLive:

    def test_discovery_uses_sitemap_not_capped_search(self):
        """Discovery pulls the full catalog from the sitemap, not the ~capped search index.

        Uses a small brand (Elie Saab) so the per-product brandId confirmation stays fast.
        The sitemap yields ~37 products vs ~15 from /api/indexing/search, so a floor above
        the search cap proves we're not using the capped path. Brand resolution is also
        case-insensitive (lowercase input on purpose).
        """
        from src.scrapers.sensation import SensationScraper

        scraper = SensationScraper()
        products = scraper.discover_products("elie saab")  # lowercase on purpose
        assert len(products) >= 25, f"Expected ~37 Elie Saab products, got {len(products)}"
        assert all(p["external_id"].isdigit() for p in products)
        assert all(p["source_url"].startswith("https://www.sensationprofumerie.it/") for p in products)

    def test_scrape_reviews_returns_reviews(self):
        from src.scrapers.sensation import SensationScraper

        scraper = SensationScraper()
        reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        # Floor (not exact) so it stays valid as reviews accumulate.
        assert len(reviews) >= 50, f"Expected >=50 reviews, got {len(reviews)}"
        assert all(r.source_site == "sensation" for r in reviews)
        assert all(r.external_review_id for r in reviews)

    def test_sauvage_elixir_review_count(self):
        """https://www.sensationprofumerie.it/dior-sauvage-elixir-P135324 — the API
        returns this product's full review history in one call, matching the site's
        displayed 'ratingCount'. Exact-count (>=) floor so it survives new reviews."""
        from src.scrapers.sensation import SensationScraper

        elixir = {
            "external_id": "135324",
            "name": "Dior Sauvage Elixir (Sensation)",
            "source_url": "https://www.sensationprofumerie.it/dior-sauvage-elixir-P135324",
        }
        scraper = SensationScraper()
        reviews = list(scraper.scrape_reviews(elixir, since=None))
        assert len(reviews) >= 92, f"Expected >=92 reviews for Sauvage Elixir, got {len(reviews)}"

    def test_since_cutoff(self):
        """Self-deriving cutoff: pick the midpoint review date, re-fetch with since=,
        assert the returned set matches the expected recent subset."""
        from src.scrapers.sensation import SensationScraper

        scraper = SensationScraper()
        all_reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(all_reviews) > 1, "Need at least 2 reviews to pick a meaningful cutoff."

        cutoff = all_reviews[len(all_reviews) // 2].review_date
        expected = [r for r in all_reviews if r.review_date >= cutoff]
        assert expected, "Cutoff produced no expected reviews — pick a different index."

        recent = list(scraper.scrape_reviews(PRODUCT, since=cutoff))
        assert {r.external_review_id for r in recent} == {
            r.external_review_id for r in expected
        }
