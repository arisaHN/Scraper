from typing import Iterator

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

PRODUCTS_URL = "https://api.bazaarvoice.com/data/products.json"
REVIEWS_URL = "https://api.bazaarvoice.com/data/reviews.json"


class BazaarvoiceScraper(BaseScraper):
    site_name = "bazaarvoice"

    def __init__(self, passkey: str, locale: str = "en_US"):
        super().__init__()
        self.passkey = passkey
        self.locale = locale

    def discover_products(self, brand_name: str) -> list[dict]:
        # Search by brand name and include review stats so we can skip products with 0 reviews.
        results, offset, total = [], 0, None
        while True:
            params = {
                "apiversion": "5.4",
                "passkey": self.passkey,
                "search": brand_name,
                "Stats": "Reviews",
                "Limit": 100,
                "Offset": offset,
            }
            resp = self._get(PRODUCTS_URL, params=params)
            data = resp.json()
            if total is None:
                total = data.get("TotalResults", 0)
            batch = data.get("Results", [])
            if not batch:
                break
            for p in batch:
                if not p.get("Id"):
                    continue
                review_count = (p.get("ReviewStatistics") or {}).get("TotalReviewCount", 0)
                if review_count > 0:
                    results.append(
                        {
                            "name": p.get("Name") or p["Id"],
                            "source_url": p.get("ProductPageUrl") or "",
                            "external_id": p["Id"],
                        }
                    )
            offset += 100
            if offset >= (total or 0):
                break
            self._polite_delay()
        return results

    def scrape_reviews(self, product: dict) -> Iterator[NormalizedReview]:
        offset = 0
        total = None
        while True:
            params = {
                "apiversion": "5.4",
                "passkey": self.passkey,
                "Filter": f"ProductId:{product['external_id']}",
                "Filter_IsRatingsOnly": "false",
                "Sort": "SubmissionTime:desc",
                "Limit": 100,
                "Offset": offset,
                "locale": self.locale,
            }
            resp = self._get(REVIEWS_URL, params=params)
            data = resp.json()
            if total is None:
                total = data.get("TotalResults", 0)
            results = data.get("Results", [])
            if not results:
                break
            for raw in results:
                yield ReviewNormalizer.from_bazaarvoice(raw)
            offset += 100
            if offset >= total:
                break
            self._polite_delay()
