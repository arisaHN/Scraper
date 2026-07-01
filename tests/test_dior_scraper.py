"""
Live integration tests for the BazaarVoice/Dior scraper.
Requires BV_PASSKEY_DIOR in .env and an internet connection.

Run with:
    .venv/bin/python -m pytest tests/test_dior_scraper.py -v
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

PASSKEY = os.environ.get("BV_PASSKEY_DIOR")
LOCALE = os.environ.get("BV_LOCALE_DIOR", "it_IT")

# https://www.dior.com/it_it/beauty/products/sauvage-eau-de-parfum-F078524009.html
PRODUCT = {
    "external_id": "F078524009",
    "name": "Sauvage Eau de Parfum (Dior)",
    "source_url": "https://www.dior.com/it_it/beauty/products/sauvage-eau-de-parfum-F078524009.html",
}


@pytest.mark.skipif(not PASSKEY, reason="BV_PASSKEY_DIOR not set")
class TestDiorReviewCount:

    def test_including_syndicated_reviews_matches_site_display(self):
        """
        Dior's own product page displays reviews syndicated in from other Dior country
        sites (e.g. SourceClient="dior-us") — verified live: the page's own review-widget
        request has no IsSyndicated filter, and excluding syndicated drops this product's
        count from ~2076 to ~71. So, unlike Douglas (a retailer that only shows its own
        natively-collected reviews), Dior must be scraped with include_syndicated=True to
        match what's actually shown on the site. Uses >= floor so the test stays valid as
        new reviews accumulate.
        """
        from src.scrapers.bazaarvoice import BazaarvoiceScraper

        scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE, include_syndicated=True)
        reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(reviews) >= 2076, (
            f"Expected at least 2076 reviews (native + syndicated) but got {len(reviews)}."
        )

    def test_excluding_syndicated_undercounts_native_only(self):
        """
        With the default include_syndicated=False, only the ~71 natively-collected it_IT
        reviews come back — confirming the syndicated set really is additive, not a
        duplicate of the native set.
        """
        from src.scrapers.bazaarvoice import BazaarvoiceScraper

        native_scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE)
        native_count = len(list(native_scraper.scrape_reviews(PRODUCT, since=None)))

        full_scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE, include_syndicated=True)
        full_count = len(list(full_scraper.scrape_reviews(PRODUCT, since=None)))

        assert full_count > native_count, (
            "Including syndicated reviews should return more reviews than native-only."
        )

    def test_since_cutoff_stops_at_correct_review(self):
        """
        Reviews are descending by SubmissionTime, so passing `since` should early-stop
        pagination and return exactly the reviews newer than the cutoff. Derives the
        cutoff and expected count from a full fetch first, so the test stays valid
        indefinitely instead of only passing right now.
        """
        from src.scrapers.bazaarvoice import BazaarvoiceScraper

        scraper = BazaarvoiceScraper(passkey=PASSKEY, locale=LOCALE, include_syndicated=True)
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
