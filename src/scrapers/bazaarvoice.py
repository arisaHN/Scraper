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
        category_map: dict = None,
        dedupe_family_variants: bool = False,
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
        # Optional retailer-specific map from 4-digit CategoryId prefix → human label.
        # Falls back to the raw CategoryId string when the prefix isn't in the map.
        self.category_map = category_map or {}
        # Some catalogs (e.g. Dior) register a separate product Id per shade/size variant,
        # all sharing one Bazaarvoice "family" (BV's own product-family grouping, exposed as
        # the product's FamilyIds[0]) and rolling up the same shared review pool under
        # BV_FE_EXPAND — verified live: querying a lightly-reviewed shade variant's Id
        # returns reviews natively tagged with *other* sibling Ids, not itself, while one
        # dominant "canonical" member of the family holds the bulk of genuinely-own reviews.
        # When True, discover_products() collapses each family down to a single
        # representative product (preferring one with a real ProductPageUrl, then the
        # highest native review count) instead of returning every variant Id — otherwise a
        # single real product surfaces as dozens/hundreds of near-duplicate DB rows.
        self.dedupe_family_variants = dedupe_family_variants

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
                    raw_cat = p.get("CategoryId") or ""
                    category = self.category_map.get(raw_cat[:4]) or (raw_cat or None)
                    results.append(
                        {
                            "name": p.get("Name") or p["Id"],
                            "source_url": p.get("ProductPageUrl") or "",
                            "external_id": p["Id"],
                            "category": category,
                            "_review_count": review_count,
                            "_family_id": (p.get("FamilyIds") or [None])[0],
                        }
                    )
            offset += 100
            if offset >= (total or 0):
                break
            self._polite_delay()
        if self.dedupe_family_variants:
            results = self._dedupe_by_family(results)
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]

    @staticmethod
    def _dedupe_by_family(results: list[dict]) -> list[dict]:
        # Products with no FamilyIds (standalone) key off their own external_id so they
        # never collide with a real family group.
        best_by_family: dict = {}
        for r in results:
            family_key = r["_family_id"] or r["external_id"]
            current = best_by_family.get(family_key)
            if current is None:
                best_by_family[family_key] = r
                continue
            candidate_has_url = bool(r["source_url"])
            current_has_url = bool(current["source_url"])
            if (candidate_has_url, r["_review_count"]) > (current_has_url, current["_review_count"]):
                best_by_family[family_key] = r
        return list(best_by_family.values())

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
