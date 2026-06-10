import asyncio
from typing import Iterator

from ..config import settings
from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

PLACES_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"


class GoogleReviewsScraper(BaseScraper):
    site_name = "google"

    def discover_products(self, brand_name: str) -> list[dict]:
        if settings.GOOGLE_PLACES_KEY:
            return self._discover_via_api(brand_name)
        return asyncio.run(self._async_discover_playwright(brand_name))

    def scrape_reviews(self, product: dict) -> Iterator[NormalizedReview]:
        if settings.GOOGLE_PLACES_KEY:
            yield from self._scrape_via_api(product)
        else:
            yield from asyncio.run(self._async_scrape_playwright(product))

    # ── API mode ──────────────────────────────────────────────────────────

    def _discover_via_api(self, brand_name: str) -> list[dict]:
        resp = self._get(
            PLACES_TEXT_SEARCH,
            params={"query": brand_name, "key": settings.GOOGLE_PLACES_KEY},
        )
        places = resp.json().get("results", [])
        return [
            {
                "name": p.get("name", ""),
                "source_url": f"https://maps.google.com/?q=place_id:{p['place_id']}",
                "external_id": p["place_id"],
            }
            for p in places
            if "place_id" in p
        ]

    def _scrape_via_api(self, product: dict) -> Iterator[NormalizedReview]:
        # Note: free tier returns max 5 reviews per place.
        resp = self._get(
            PLACES_DETAILS,
            params={
                "place_id": product["external_id"],
                "fields": "reviews",
                "key": settings.GOOGLE_PLACES_KEY,
            },
        )
        for raw in resp.json().get("result", {}).get("reviews", []):
            yield ReviewNormalizer.from_google(raw)

    # ── Playwright fallback ───────────────────────────────────────────────

    async def _async_discover_playwright(self, brand_name: str) -> list[dict]:
        from playwright.async_api import async_playwright

        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(
                    f"https://www.google.com/maps/search/{brand_name}",
                    wait_until="networkidle",
                    timeout=30000,
                )
                cards = await page.query_selector_all('[role="article"]')
                for card in cards:
                    link_el = await card.query_selector("a")
                    href = (await link_el.get_attribute("href")) if link_el else ""
                    name_el = await card.query_selector('[class*="fontHeadlineSmall"]')
                    name = (await name_el.inner_text()).strip() if name_el else ""
                    if name:
                        results.append(
                            {
                                "name": name,
                                "source_url": href or "",
                                "external_id": href or name,
                            }
                        )
            finally:
                await browser.close()
        return results

    async def _async_scrape_playwright(self, product: dict) -> list[NormalizedReview]:
        from playwright.async_api import async_playwright

        reviews = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(
                    product["source_url"], wait_until="networkidle", timeout=30000
                )
                try:
                    await page.click('[aria-label*="reviews" i]', timeout=5000)
                except Exception:
                    pass
                for _ in range(20):
                    await page.keyboard.press("End")
                    await asyncio.sleep(0.8)
                cards = await page.query_selector_all("[data-review-id]")
                for card in cards:
                    rid = await card.get_attribute("data-review-id") or ""
                    name_el = await card.query_selector('[class*="d4r55"]')
                    name = (await name_el.inner_text()).strip() if name_el else None
                    star_els = await card.query_selector_all('[aria-label*="star" i]')
                    stars = len(star_els)
                    body_el = await card.query_selector('[class*="wiI7pd"]')
                    body = (await body_el.inner_text()).strip() if body_el else None
                    raw = {
                        "time": rid,
                        "author_name": name,
                        "rating": stars,
                        "text": body,
                    }
                    reviews.append(ReviewNormalizer.from_google(raw))
            finally:
                await browser.close()
        return reviews
