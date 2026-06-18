from datetime import datetime
from typing import Iterator, Optional

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

PRODUCTS_URL = "https://api.bazaarvoice.com/data/products.json"
REVIEWS_URL = "https://api.bazaarvoice.com/data/reviews.json"


class BazaarvoiceScraper(BaseScraper):
    site_name = "bazaarvoice"

    def __init__(
        self,
        passkey: str,
        locale: str = "en_US",
        include_ratings_only: bool = False,
        include_syndicated: bool = False,
    ):
        super().__init__()
        self.passkey = passkey
        self.locale = locale
        self.include_ratings_only = include_ratings_only
        # Retailers can syndicate reviews written for the manufacturer's own site (e.g.
        # Douglas shows reviews written on dior.com under SourceClient="dior-it"). The
        # retailer's storefront only displays its own native reviews, so default to
        # excluding syndicated ones to match what's actually shown on the retailer's site.
        self.include_syndicated = include_syndicated

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

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        # No backfill cursor needed: the public REST API has no per-request bot-detection
        # cost, so a full since-based pagination each run is cheap and safe.
        offset = 0
        total = None
        while True:
            # Bazaarvoice expects multiple Filter conditions as repeated "Filter" query
            # params (e.g. Filter=ProductId:X&Filter=IsRatingsOnly:false), not as a
            # separate "Filter_IsRatingsOnly" key — a dict can't hold two "Filter" keys,
            # so this must be a list of tuples for both filters to actually apply.
            # The "include_*" flags mean "don't restrict on this field" rather than
            # "flip the filter to true" — filtering IsSyndicated:true would return only
            # the syndicated reviews, not the union of native + syndicated.
            params = [
                ("apiversion", "5.4"),
                ("passkey", self.passkey),
                ("Filter", f"ProductId:{product['external_id']}"),
                ("Sort", "SubmissionTime:desc"),
                ("Limit", 100),
                ("Offset", offset),
                ("locale", self.locale),
            ]
            if not self.include_ratings_only:
                params.append(("Filter", "IsRatingsOnly:false"))
            if not self.include_syndicated:
                params.append(("Filter", "IsSyndicated:false"))
            resp = self._get(REVIEWS_URL, params=params)
            data = resp.json()
            if total is None:
                total = data.get("TotalResults", 0)
            results = data.get("Results", [])
            if not results:
                break
            stop = False
            for raw in results:
                review = ReviewNormalizer.from_bazaarvoice(raw)
                if self._past_cutoff(review.review_date, since):
                    stop = True
                    break
                yield review
            if stop:
                break
            offset += 100
            if offset >= total:
                break
            self._polite_delay()
