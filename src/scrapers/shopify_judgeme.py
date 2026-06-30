from datetime import datetime
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

_PRODUCTS_PER_PAGE = 250  # Shopify products.json page-size hard cap
# Shopify's public products.json refuses page > 100 (page 101 → HTTP 400), so the most a
# products.json scan can ever reach is 100 * 250 = 25,000 products. Stores larger than that
# (e.g. pinalli, ~38k) can't be fully covered by products.json alone — see PinalliScraper.
_PRODUCTS_JSON_PAGE_CAP = 100
_REVIEWS_PER_PAGE = 30
_WIDGET_URL = "https://judge.me/reviews/reviews_for_widget"


def _norm(name: str) -> str:
    """Casefold + drop non-alphanumerics, for tolerant vendor/brand matching."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _parse_review_widget(html: str) -> list[dict]:
    """Parse Judge.me's rendered review-widget HTML into plain dicts.

    Each review is a `.jdgm-rev` element whose data-attributes + child spans carry all
    the fields we need (id, score, timestamp, author, title, body, verified buyer).
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for el in soup.select(".jdgm-rev"):
        rating = el.select_one(".jdgm-rev__rating")
        ts = el.select_one(".jdgm-rev__timestamp")
        author = el.select_one(".jdgm-rev__author")
        title = el.select_one(".jdgm-rev__title")
        body = el.select_one(".jdgm-rev__body")
        out.append({
            "review_id": el.get("data-review-id"),
            "verified": el.get("data-verified-buyer") == "true",
            "thumb_up": el.get("data-thumb-up-count") or 0,
            "score": rating.get("data-score") if rating else None,
            # data-content looks like "2025-10-06 15:19:30 UTC"
            "timestamp": ts.get("data-content") if ts else None,
            "author": author.get_text(strip=True) if author else None,
            "title": title.get_text(strip=True) if title else None,
            "body": body.get_text(" ", strip=True) if body else None,
        })
    return out


class ShopifyJudgemeScraper(BaseScraper):
    """Base scraper for Shopify storefronts whose reviews are powered by Judge.me.

    No browser needed — discovery uses Shopify's public ``/products.json`` feed and reviews
    use Judge.me's public ``reviews_for_widget`` endpoint (no API token; it's the same
    request the storefront widget makes). Both are plain ``requests``.

    Subclasses set these class attributes:
      - ``site_name`` — stored as the review/product source_site.
      - ``products_base`` — origin serving products.json (the storefront, or the myshopify
        backend when the storefront is behind a bot wall, as with pinalli).
      - ``storefront_base`` — public origin for the product ``source_url``.
      - ``shop_domain`` — the myshopify domain Judge.me keys reviews by.
      - ``category_from_product_type`` — set True only when the store's Shopify
        ``product_type`` holds real category labels (ditano) rather than SKU/barcode junk
        (pinalli); otherwise ``category`` is left null.
    """

    supports_backfill = False

    products_base: str = ""
    storefront_base: str = ""
    shop_domain: str = ""
    category_from_product_type: bool = False

    # ── product discovery (Shopify products.json) ─────────────────────────────────

    def discover_products(self, brand_name: str) -> list[dict]:
        """Discover a brand's products from Shopify's products.json, filtered by ``vendor``.

        Shopify exposes the whole catalog at /products.json (250/page); ``vendor`` is the
        brand, so we paginate to exhaustion and keep products whose vendor matches
        ``brand_name`` by tolerant (alnum-only, casefolded) comparison.
        """
        return list(self._discover_via_products_json(brand_name).values())

    def _product_dict(self, p: dict) -> dict:
        """Build a discovery record from a Shopify products.json product object."""
        category = p.get("product_type") or None if self.category_from_product_type else None
        return {
            "external_id": str(p["id"]),
            "name": p.get("title") or str(p["id"]),
            "source_url": f"{self.storefront_base}/products/{p['handle']}",
            "category": category,
        }

    def _discover_via_products_json(self, brand_name: str) -> dict[str, dict]:
        """Page products.json (capped at Shopify's 100-page limit), keeping the brand's
        products. Returns a dict keyed by external_id so callers can union with other
        sources (e.g. PinalliScraper merges in Algolia hits)."""
        target = _norm(brand_name)
        products: dict[str, dict] = {}
        for page in range(1, _PRODUCTS_JSON_PAGE_CAP + 1):
            resp = self._get(
                f"{self.products_base}/products.json",
                params={"limit": _PRODUCTS_PER_PAGE, "page": page},
            )
            batch = (resp.json() or {}).get("products") or []
            if not batch:
                break
            for p in batch:
                if _norm(p.get("vendor") or "") == target:
                    products[str(p["id"])] = self._product_dict(p)
            self._polite_delay()
        return products

    # ── review scraping (Judge.me widget) ─────────────────────────────────────────

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        """Fetch a product's Judge.me reviews via the public widget endpoint.

        The widget's ``product_id`` is the Shopify product id (same value products.json
        returns). Pagination is bounded by the reported ``total_count``. The widget's review
        order isn't a guaranteed date-sort, so we filter each review against ``since``
        individually (skip, not early-stop) — safe because these stores' per-product review
        counts are small.
        """
        pid = product["external_id"]
        page = 1
        collected = 0
        while True:
            resp = self._get(
                _WIDGET_URL,
                params={
                    "url": self.shop_domain,
                    "shop_domain": self.shop_domain,
                    "platform": "shopify",
                    "product_id": pid,
                    "page": page,
                    "per_page": _REVIEWS_PER_PAGE,
                },
            )
            data = resp.json() or {}
            total = int(data.get("total_count") or 0)
            raws = _parse_review_widget(data.get("html") or "")
            if not raws:
                break
            for raw in raws:
                if not raw.get("review_id"):
                    continue
                review = ReviewNormalizer.from_judgeme(raw, self.site_name)
                if self._past_cutoff(review.review_date, since):
                    continue
                yield review
            collected += len(raws)
            if collected >= total:
                break
            page += 1
            self._polite_delay()
