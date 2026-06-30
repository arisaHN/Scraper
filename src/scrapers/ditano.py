import os
from datetime import datetime
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

_STOREFRONT = "https://ditano.com"
# Judge.me's public widget endpoint keys reviews by the storefront's myshopify domain.
_SHOP_DOMAIN = os.environ.get("DITANO_SHOP_DOMAIN", "ditano.myshopify.com")
_WIDGET_URL = "https://judge.me/reviews/reviews_for_widget"
_PRODUCTS_PER_PAGE = 250  # Shopify products.json hard cap
_REVIEWS_PER_PAGE = 30


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


class DitanoScraper(BaseScraper):
    """Scraper for ditano.com — a Shopify storefront whose reviews are powered by Judge.me.

    No browser needed: product discovery uses Shopify's public ``/products.json`` feed, and
    reviews use Judge.me's public ``reviews_for_widget`` endpoint (no API token — it's the
    same request the storefront widget makes). Both are plain ``requests``.
    """

    site_name = "ditano"
    supports_backfill = False

    # ── product discovery (Shopify products.json) ─────────────────────────────────

    def discover_products(self, brand_name: str) -> list[dict]:
        """Discover a brand's products from Shopify's products.json, filtered by ``vendor``.

        Shopify exposes the whole catalog at /products.json (250/page); ``vendor`` is the
        brand, so we paginate and keep products whose vendor matches ``brand_name`` by
        tolerant (alnum-only, casefolded) comparison.
        """
        target = _norm(brand_name)
        products: list[dict] = []
        page = 1
        while True:
            resp = self._get(
                f"{_STOREFRONT}/products.json",
                params={"limit": _PRODUCTS_PER_PAGE, "page": page},
            )
            batch = (resp.json() or {}).get("products") or []
            if not batch:
                break
            for p in batch:
                if _norm(p.get("vendor") or "") != target:
                    continue
                products.append({
                    "external_id": str(p["id"]),
                    "name": p.get("title") or str(p["id"]),
                    "source_url": f"{_STOREFRONT}/products/{p['handle']}",
                    # Shopify product_type is the store's own category (e.g. "Fragranze").
                    "category": p.get("product_type") or None,
                })
            page += 1
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

        The widget's ``product_id`` is the Shopify product id. Pagination is bounded by
        the reported ``total_count``. The widget's review order isn't a guaranteed
        date-sort, so we filter each review against ``since`` individually (skip, don't
        early-stop) — safe here because this store's per-product review counts are small.
        """
        pid = product["external_id"]
        page = 1
        collected = 0
        while True:
            resp = self._get(
                _WIDGET_URL,
                params={
                    "url": _SHOP_DOMAIN,
                    "shop_domain": _SHOP_DOMAIN,
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
                review = ReviewNormalizer.from_ditano(raw)
                if self._past_cutoff(review.review_date, since):
                    continue
                yield review
            collected += len(raws)
            if collected >= total:
                break
            page += 1
            self._polite_delay()
