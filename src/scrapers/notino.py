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
        for selector in [
            "button:has-text('Accetta')", "button:has-text('Accetto')",
            "button:has-text('Accept')", "button[id*='accept']",
        ]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=5_000)
                    break
            except Exception:
                pass

    def _parse_products_from_ssr(self, html: str) -> list[dict]:
        """Extract product objects embedded in the SSR script blobs on the brand page.

        Next.js serialises full product objects into __NEXT_DATA__ / RSC payload
        script tags. Each object contains masterProductCode, name, and url.
        """
        seen: set[str] = set()
        products: list[dict] = []
        for m in re.finditer(r'"masterProductCode"\s*:\s*"([^"]+)"', html):
            code = m.group(1)
            if code in seen:
                continue
            seen.add(code)
            start = max(0, m.start() - 500)
            end = min(len(html), m.end() + 400)
            ctx = html[start:end]
            name_m = re.search(r'"name"\s*:\s*"([^"]+)"', ctx)
            url_m = re.search(r'"url"\s*:\s*"(/[^"]+)"', ctx)
            if not name_m:
                continue
            products.append({
                "code": code,
                "name": name_m.group(1),
                "url": url_m.group(1) if url_m else None,
            })
        return products

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
