import os
import re
import time
from datetime import datetime
from typing import Iterator, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper, CamoufoxBrowserMixin

_GRAPHQL_URL = "https://www.notino.it/api/product/"
_PAGE_SIZE = 20

# Stable hash until Notino redeploys their bundle. Override via env var if it rotates.
_REVIEWS_HASH = os.environ.get(
    "NOTINO_REVIEWS_HASH",
    "9b49406d4f6df65fb02600d8ef3194612a78b86b984d4b18aff31e0d5c85fa0b",
)


def _collect_notino_products(obj: object, products: dict) -> None:
    """Iterative DFS over parsed JSON; collects all dicts that have masterProductCode."""
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            code = node.get("masterProductCode")
            if isinstance(code, str) and code and code not in products:
                name = node.get("name")
                url = node.get("url")
                if isinstance(name, str) and name:
                    products[code] = {"code": code, "name": name, "url": url if isinstance(url, str) else None}
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


class NotinoScraper(CamoufoxBrowserMixin, BaseScraper):
    site_name = "notino"
    supports_backfill = False

    # ── GraphQL client (plain requests — /api/ endpoint is not Cloudflare-gated) ──

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=3, max=60),
        retry=retry_if_exception_type(
            (requests.HTTPError, requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError)
        ),
        reraise=True,
    )
    def _graphql(self, operation_name: str, variables: dict, sha256_hash: str) -> dict:
        """POST an Apollo Persisted Query (APQ) to the Notino GraphQL endpoint."""
        payload = {
            "operationName": operation_name,
            "variables": variables,
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": sha256_hash,
                }
            },
        }
        resp = self.session.post(_GRAPHQL_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── product discovery ────────────────────────────────────────────────────────

    def _wait_for_page(self, page, url: str):
        """Navigate and wait for Cloudflare challenge to auto-solve before returning."""
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_function(
                "document.title !== 'Un momento…' && document.title !== 'Just a moment...'",
                timeout=30_000,
            )
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        self._dismiss_consent(page)

    def _parse_products_from_ssr(self, html: str) -> list[dict]:
        """Extract product objects from the __NEXT_DATA__ JSON blob on the brand page.

        Parses the structured JSON directly so field positions don't matter, unlike a
        fixed-width regex context window that silently drops products in large blobs.
        """
        import json as _json

        products: dict[str, dict] = {}

        # Try structured parse of __NEXT_DATA__ first
        m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>([^<]+)</script>', html)
        if m:
            try:
                data = _json.loads(m.group(1))
                _collect_notino_products(data, products)
                if products:
                    return list(products.values())
            except Exception:
                pass

        # Fallback: scan all inline JSON script blobs
        for script_m in re.finditer(r'<script[^>]*type=["\']application/json["\'][^>]*>([^<]{100,})</script>', html):
            try:
                data = _json.loads(script_m.group(1))
                _collect_notino_products(data, products)
            except Exception:
                pass

        return list(products.values())

    def discover_products(self, brand_name: str) -> list[dict]:
        brand_slug = brand_name.lower().replace(" ", "-")
        brand_url = f"https://www.notino.it/{brand_slug}/"

        page = self._new_page()
        try:
            self._wait_for_page(page, brand_url)
            # Scroll to trigger any lazy-loaded product grids
            for _ in range(15):
                page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                time.sleep(0.4)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            html = page.content()
        finally:
            page.close()

        raw = self._parse_products_from_ssr(html)
        results = []
        for item in raw:
            url_path = item.get("url") or ""
            results.append({
                "name": item["name"],
                "source_url": f"https://www.notino.it{url_path}" if url_path else None,
                "external_id": item["code"],
            })
        return results

    # ── review scraping ─────────────────────────────────────────────────────────

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        code = product["external_id"]
        page = 1
        reached_cutoff = False

        while not reached_cutoff:
            data = self._graphql(
                "getReviews",
                {
                    "code": code,
                    "orderBy": "DateTime",
                    "orderDesc": True,
                    "page": page,
                    "hideTranslated": False,
                    "rating": [],
                    "contentTypes": ["WithText"],
                    "variantIds": [],
                    "shopIds": [],
                },
                _REVIEWS_HASH,
            )
            inner = (
                ((data.get("data") or {}).get("reviewsAndFilters") or {})
                .get("data") or {}
            )
            reviews = inner.get("reviews") or []
            total_pages = inner.get("totalPages") or 1

            if not reviews:
                if page == 1 and (data.get("data") is None or data.get("errors")):
                    print(
                        f"  [notino] WARNING: getReviews returned no data for {code!r} "
                        f"— APQ hash may be stale. Override via NOTINO_REVIEWS_HASH env var. "
                        f"errors={data.get('errors')}",
                        flush=True,
                    )
                break

            for raw in reviews:
                review = ReviewNormalizer.from_notino(raw)
                if self._past_cutoff(review.review_date, since):
                    reached_cutoff = True
                    break
                yield review

            if page >= total_pages:
                break
            page += 1
            self._polite_delay()
