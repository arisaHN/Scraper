"""
Live integration tests for SephoraHTMLScraper.
Requires SEPHORA_ENABLED=1 and must run on the self-hosted runner — sephora.it's
Akamai bot-detection blocks requests from standard CI runners and local machines.

Run with:
    SEPHORA_ENABLED=1 .venv/bin/python -m pytest tests/test_sephora_scraper.py -v
"""
import os

import pytest

SEPHORA_ENABLED = os.environ.get("SEPHORA_ENABLED", "").lower() in ("1", "true", "yes", "on")

# YSL product that appeared in Dior discovery results when the scraper used a
# free-text `q=Dior` SFCC search — that search returns cross-brand results
# (related products, cross-promotions). The fix was to remove the text search
# and rely only on category-ID (`cgid=`) lookups, which are scoped to the
# brand's own hub page.
# https://www.sephora.it/p/black-opium-over-red---eau-de-parfum-P10055930.html
_CROSS_BRAND_PID = "P10055930"


@pytest.mark.skipif(not SEPHORA_ENABLED, reason="SEPHORA_ENABLED not set")
class TestSephoraDiscovery:

    def test_discover_products_excludes_other_brand_products(self):
        """
        Dior product discovery must not return products from other brands.
        Regression for: free-text `q=Dior` SFCC search was polluting results
        with YSL (and potentially other brand) products.
        """
        from src.scrapers.sephora_html import SephoraHTMLScraper

        scraper = SephoraHTMLScraper()
        try:
            products = scraper.discover_products("Dior")
        finally:
            scraper.close()

        pids = {p["external_id"] for p in products}
        assert _CROSS_BRAND_PID not in pids, (
            f"Product {_CROSS_BRAND_PID} (YSL Black Opium Over Red) was returned "
            f"as a Dior product — cross-brand contamination from text search is back."
        )

    def test_discover_products_returns_dior_products(self):
        """Sanity check: Dior discovery should return a meaningful number of products."""
        from src.scrapers.sephora_html import SephoraHTMLScraper

        scraper = SephoraHTMLScraper()
        try:
            products = scraper.discover_products("Dior")
        finally:
            scraper.close()

        assert len(products) >= 10, (
            f"Expected at least 10 Dior products on sephora.it, got {len(products)}"
        )
        assert all(p["external_id"].startswith("P") for p in products)
        assert all(p["source_url"].startswith("https://www.sephora.it/") for p in products)
