"""
Live integration tests for MarionnaudScraper.
Requires MARIONNAUD_ENABLED=1 and an internet connection.

Run with:
    MARIONNAUD_ENABLED=1 .venv/bin/python -m pytest tests/test_marionnaud_scraper.py -v
"""
import os

import pytest

MARIONNAUD_ENABLED = os.environ.get("MARIONNAUD_ENABLED", "").lower() in ("1", "true", "yes", "on")

# https://www.marionnaud.it/wella/ultimate-repair/miracle-hair-rescue/p/BP_502700
PRODUCT = {
    "external_id": "BP_502700",
    "name": "Miracle Hair Rescue (Marionnaud)",
    "source_url": "https://www.marionnaud.it/wella/ultimate-repair/miracle-hair-rescue/p/BP_502700",
}

# https://www.marionnaud.it/dior/sauvage/elixir/p/BP_143440
DIOR_SAUVAGE_ELIXIR = {
    "external_id": "BP_143440",
    "name": "Elixir (Dior Sauvage, Marionnaud)",
    "source_url": "https://www.marionnaud.it/dior/sauvage/elixir/p/BP_143440",
}


@pytest.mark.skipif(not MARIONNAUD_ENABLED, reason="MARIONNAUD_ENABLED not set")
class TestMarionnaudReviewScraping:

    def test_since_cutoff_stops_at_correct_review(self):
        """
        Self-deriving cutoff: fetch all reviews, pick the midpoint as cutoff, then
        re-fetch with since= and assert the returned set matches the expected subset.
        Does not rot as new reviews accumulate over time.
        """
        from src.scrapers.marionnaud import MarionnaudScraper

        scraper = MarionnaudScraper()
        all_reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(all_reviews) > 1, "Need at least 2 reviews to pick a meaningful cutoff."

        cutoff_index = len(all_reviews) // 2
        cutoff = all_reviews[cutoff_index].review_date
        expected = [r for r in all_reviews if r.review_date >= cutoff]
        assert expected, "Cutoff produced no expected reviews — pick a different index."

        recent_reviews = list(scraper.scrape_reviews(PRODUCT, since=cutoff))
        assert {r.external_review_id for r in recent_reviews} == {
            r.external_review_id for r in expected
        }

    def test_total_review_count_dior_sauvage_elixir(self):
        """
        Dior Sauvage Elixir is known to have exactly 92 reviews on marionnaud.it
        (verified manually against the site at the time this test was written).
        Catches pagination regressions on a product with several full pages of
        reviews, unlike a product with just a handful.
        """
        from src.scrapers.marionnaud import MarionnaudScraper

        scraper = MarionnaudScraper()
        reviews = list(scraper.scrape_reviews(DIOR_SAUVAGE_ELIXIR, since=None))
        external_ids = [r.external_review_id for r in reviews]
        assert len(external_ids) == len(set(external_ids)), "scrape_reviews returned duplicate reviews"
        assert len(reviews) == 92

    def test_discover_products(self):
        """Wella's brand catalog page on Marionnaud should yield its full product list."""
        from src.scrapers.marionnaud import MarionnaudScraper

        scraper = MarionnaudScraper()
        try:
            products = scraper.discover_products("Wella")
        finally:
            scraper.close()

        assert len(products) >= 40
        assert all(p["external_id"].startswith("BP_") for p in products)
        assert any(p["external_id"] == PRODUCT["external_id"] for p in products)

    def test_discover_products_dior_count(self):
        """
        Dior's catalog on marionnaud.it is known to list exactly 254 products
        (verified manually against the site at the time this test was written).
        Catches pagination regressions (e.g. an off-by-one in totalPages handling,
        or Hybris silently capping pageSize) that a smaller brand's catalog might
        not surface.
        """
        from src.scrapers.marionnaud import MarionnaudScraper

        scraper = MarionnaudScraper()
        try:
            products = scraper.discover_products("Dior")
        finally:
            scraper.close()

        external_ids = [p["external_id"] for p in products]
        assert len(external_ids) == len(set(external_ids)), "discover_products returned duplicate products"
        assert len(products) == 254
