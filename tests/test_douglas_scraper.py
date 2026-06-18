"""
Live integration tests for the BazaarVoice/Douglas scraper.
Requires BV_PASSKEY_DOUGLAS in .env and an internet connection.

Run with:
    .venv/bin/python -m pytest tests/test_douglas_scraper.py -v
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

PASSKEY = os.environ.get("BV_PASSKEY_DOUGLAS")
LOCALE = os.environ.get("BV_LOCALE_DOUGLAS", "it_IT")

# https://www.douglas.it/it/p/3001042193?variant=995604
PRODUCT = {
    "external_id": "3001042193",
    "name": "Dior Sauvage EDT (Douglas)",
    "source_url": "https://www.douglas.it/it/p/3001042193",
}


@pytest.mark.skipif(not PASSKEY, reason="BV_PASSKEY_DOUGLAS not set")
class TestDouglasReviewCount:

    def test_excludes_syndicated_reviews_by_default(self):
        """
        IsSyndicated=false (default) — excludes reviews syndicated from the manufacturer's
        own site (here, 19 reviews written on dior.com under SourceClient="dior-it") that
        Douglas's own storefront doesn't display. Expected: 443 native Douglas reviews.
        """
        from src.scrapers.bazaarvoice import BazaarvoiceScraper

        scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE)
        reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(reviews) == 443, (
            f"Expected 443 native Douglas reviews but got {len(reviews)}."
        )

    def test_including_syndicated_reviews(self):
        """
        IsSyndicated=true — includes the syndicated Dior reviews as well.
        Expected: 462 total reviews (443 native + 19 syndicated).
        """
        from src.scrapers.bazaarvoice import BazaarvoiceScraper

        scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE, include_syndicated=True)
        reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(reviews) == 462, (
            f"Expected 462 total reviews but got {len(reviews)}."
        )

    def test_since_cutoff_stops_at_correct_review(self):
        """
        Reviews are descending by SubmissionTime, so passing `since` should early-stop
        pagination and return exactly the reviews newer than the cutoff. Rather than
        hardcoding a fixed date/count (which would drift as new reviews come in over
        time), this derives the cutoff and expected count from a full fetch first, so
        the test stays valid indefinitely instead of only passing right now.
        """
        from src.scrapers.bazaarvoice import BazaarvoiceScraper

        scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE)
        all_reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(all_reviews) > 1, "Need at least 2 reviews to pick a meaningful cutoff."

        cutoff_index = len(all_reviews) // 2
        cutoff = all_reviews[cutoff_index].review_date
        # _past_cutoff stops only on review_date < since, so a review dated exactly at
        # the cutoff is still included.
        expected = [r for r in all_reviews if r.review_date >= cutoff]
        assert expected, "Cutoff produced no expected reviews — pick a different index."

        recent_reviews = list(scraper.scrape_reviews(PRODUCT, since=cutoff))
        assert {r.external_review_id for r in recent_reviews} == {
            r.external_review_id for r in expected
        }
