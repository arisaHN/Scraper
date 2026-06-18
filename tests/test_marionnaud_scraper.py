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
