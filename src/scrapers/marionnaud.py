import os
import re
from datetime import datetime
from typing import Iterator, Optional

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper, CamoufoxBrowserMixin

# Product search runs through www.marionnaud.it's own Akamai-gated OCC API, so it has to be
# called via page.evaluate(fetch) from inside an already-loaded page (carries the real browser
# fingerprint/cookies), same trick as SephoraHTMLScraper's SFCC AJAX search.
_SEARCH_URL_TMPL = (
    "https://api.marionnaud.it/api/v2/mit-spa/search"
    "?fields=FULL&categoryCode={code}&lang=it_IT&curr=EUR&pageSize=40&currentPage={page}"
)
# Hybris caps the effective page size at 40 regardless of the requested pageSize value.
_SEARCH_PAGE_SIZE = 40

# Reviews live on PowerReviews' own display API, a separate, non-Akamai-gated domain — plain
# requests work directly, no browser needed (unlike product discovery above).
_REVIEWS_URL_TMPL = "https://display.powerreviews.com/m/{merchant_id}/l/it_IT/product/{page_id}/reviews"
_REVIEWS_PAGE_SIZE = 25  # PowerReviews rejects paging.size > 25

# Stable until Marionnaud rotates their PowerReviews account; override via env if so.
_MERCHANT_ID = os.environ.get("MARIONNAUD_MERCHANT_ID", "1598157158")
_APIKEY = os.environ.get("MARIONNAUD_APIKEY", "3ab6644d-ed6b-413c-be19-01749f0af7b5")


class MarionnaudScraper(CamoufoxBrowserMixin, BaseScraper):
    site_name = "marionnaud"
    supports_backfill = False

    # ── product discovery ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    @staticmethod
    def _category_from_hybris(p: dict) -> Optional[str]:
        """Best-effort category label from the Hybris FULL-fields product payload.

        Unverified against a live response (Akamai blocks a plain probe request) — the
        standard Hybris OCC `categories` field is a list of `{code, name, ...}` dicts ordered
        root-to-leaf, so the last entry is the most specific label. Returns None (no crash)
        if the field is absent or shaped differently than expected.
        """
        categories = p.get("categories")
        if not isinstance(categories, list) or not categories:
            return None
        name = categories[-1].get("name") if isinstance(categories[-1], dict) else None
        return name or None

    def discover_products(self, brand_name: str) -> list[dict]:
        target = self._normalize(brand_name)

        list_page = self._new_page()
        try:
            list_page.goto("https://www.marionnaud.it/brandslist", wait_until="domcontentloaded", timeout=60_000)
            list_page.wait_for_timeout(2000)
            self._dismiss_consent(list_page)
            links = list_page.evaluate(
                """() => [...document.querySelectorAll('a[href]')]
                    .map(a => [a.textContent.trim(), a.getAttribute('href')])
                    .filter(([t, h]) => h && /\\/b\\/\\d+$/.test(h))
                """
            )
        finally:
            list_page.close()

        matched_href = None
        for text, href in links:
            if self._normalize(text) == target:
                matched_href = href
                break
        if not matched_href:
            raise ValueError(f"marionnaud: could not find brand code for {brand_name!r} on /brandslist")

        brand_code = matched_href.rsplit("/", 1)[-1]
        brand_url = f"https://www.marionnaud.it{matched_href}"
        page = self._new_page()
        try:
            page.goto(brand_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            self._dismiss_consent(page)

            all_products: dict[str, dict] = {}
            current_page = 0
            total_pages = 1
            while current_page < total_pages:
                url = _SEARCH_URL_TMPL.format(code=brand_code, page=current_page)
                data = page.evaluate(
                    """async (url) => {
                        const resp = await fetch(url, {credentials: "include"});
                        return await resp.json();
                    }""",
                    url,
                )
                pagination = data.get("pagination") or {}
                total_pages = pagination.get("totalPages", 1) or 1
                for p in data.get("products") or []:
                    code = p.get("code")
                    if not code:
                        continue
                    all_products.setdefault(
                        code,
                        {
                            "name": p.get("name") or code,
                            "source_url": f"https://www.marionnaud.it{p.get('url', '')}",
                            "external_id": code,
                            "category": self._category_from_hybris(p),
                        },
                    )
                current_page += 1
                if current_page < total_pages:
                    self._polite_delay()
        finally:
            page.close()

        return list(all_products.values())

    # ── review scraping ─────────────────────────────────────────────────────────

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        # No backfill cursor needed: PowerReviews' display API has no per-request
        # bot-detection cost, so a full since-based pagination each run is cheap and safe
        # (same reasoning as BazaarvoiceScraper).
        page_id = product["external_id"]
        url = _REVIEWS_URL_TMPL.format(merchant_id=_MERCHANT_ID, page_id=page_id)
        offset = 0
        total = None

        while True:
            params = {
                "apikey": _APIKEY,
                "_noconfig": "true",
                "paging.size": _REVIEWS_PAGE_SIZE,
                "paging.from": offset,
                "sort": "Newest",
            }
            resp = self._get(url, params=params)
            data = resp.json()
            result = (data.get("results") or [{}])[0]
            if total is None:
                total = (data.get("paging") or {}).get("total_results", 0)
            reviews = result.get("reviews") or []
            if not reviews:
                break

            stop = False
            for raw in reviews:
                review = ReviewNormalizer.from_marionnaud(raw)
                if self._past_cutoff(review.review_date, since):
                    stop = True
                    break
                yield review
            if stop:
                break

            offset += len(reviews)
            if offset >= total:
                break
            self._polite_delay()
