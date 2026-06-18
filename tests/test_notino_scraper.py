"""
Live integration tests for NotinoScraper.
Requires NOTINO_ENABLED=1 and an internet connection.

Run with:
    NOTINO_ENABLED=1 .venv/bin/python -m pytest tests/test_notino_scraper.py -v
"""
import os

import pytest

NOTINO_ENABLED = os.environ.get("NOTINO_ENABLED", "").lower() in ("1", "true", "yes", "on")

# https://www.notino.it/dior/sauvage-eau-de-parfum-per-uomo/
# masterProductCode extracted from page's Apollo SSR cache (productMasterCode param)
PRODUCT = {
    "external_id": "CHDSVGM_AEDP10",
    "name": "Dior Sauvage Eau de Parfum (Notino)",
    "source_url": "https://www.notino.it/dior/sauvage-eau-de-parfum-per-uomo/",
}


@pytest.mark.skipif(not NOTINO_ENABLED, reason="NOTINO_ENABLED not set")
class TestNotinoReviewCount:

    def test_total_text_reviews(self):
        """
        Notino's getReviews filters contentTypes=["WithText"], so only reviews with
        written text are returned. Expected: 78 text reviews for this product.
        """
        from src.scrapers.notino import NotinoScraper

        scraper = NotinoScraper()
        reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(reviews) == 78, (
            f"Expected 78 text reviews but got {len(reviews)}."
        )

    def test_since_cutoff_stops_at_correct_review(self):
        """
        Self-deriving cutoff: fetch all reviews, pick the midpoint as cutoff, then
        re-fetch with since= and assert the returned set matches the expected subset.
        Does not rot as new reviews accumulate over time.
        """
        from src.scrapers.notino import NotinoScraper

        scraper = NotinoScraper()
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
