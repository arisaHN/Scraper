import asyncio
import json
from typing import Iterator

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

SEARCH_URL = "https://www.trustpilot.com/search"


class TrustpilotScraper(BaseScraper):
    site_name = "trustpilot"

    def discover_products(self, brand_name: str) -> list[dict]:
        # Trustpilot blocks plain requests — use Playwright throughout.
        return asyncio.run(self._async_discover(brand_name))

    def scrape_reviews(self, product: dict) -> Iterator[NormalizedReview]:
        yield from asyncio.run(self._async_scrape_reviews(product))

    # ── Playwright helpers ────────────────────────────────────────────────────

    async def _async_discover(self, brand_name: str) -> list[dict]:
        from playwright.async_api import async_playwright

        results, seen = [], set()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(
                    f"{SEARCH_URL}?query={brand_name}",
                    wait_until="networkidle",
                    timeout=30000,
                )
                # Wait for review links to be in DOM (they live in a dropdown, not CSS-visible)
                await page.wait_for_selector(
                    'a[href^="/review/"]', timeout=10000, state="attached"
                )
                links = await page.query_selector_all('a[href^="/review/"]')
                for link in links:
                    href = await link.get_attribute("href") or ""
                    slug = href.replace("/review/", "").strip("/")
                    if not slug or slug in seen or "/" in slug:
                        continue
                    seen.add(slug)
                    name = (await link.inner_text()).strip() or slug
                    results.append(
                        {
                            "name": name,
                            "source_url": f"https://www.trustpilot.com/review/{slug}",
                            "external_id": slug,
                        }
                    )
            except Exception:
                pass
            finally:
                await browser.close()
        return results

    async def _async_scrape_reviews(self, product: dict) -> list[NormalizedReview]:
        from playwright.async_api import async_playwright

        reviews = []
        slug = product["external_id"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                page_num = 1
                while True:
                    url = f"https://www.trustpilot.com/review/{slug}?page={page_num}"
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    # Extract __NEXT_DATA__ from the rendered page
                    script_content = await page.evaluate(
                        "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null; }"
                    )
                    if not script_content:
                        break
                    try:
                        payload = json.loads(script_content)
                    except (json.JSONDecodeError, TypeError):
                        break
                    page_props = payload.get("props", {}).get("pageProps", {})
                    batch = page_props.get("reviews", [])
                    if not batch:
                        break
                    for raw in batch:
                        reviews.append(ReviewNormalizer.from_trustpilot(raw))
                    pagination = page_props.get("filters", {}).get("pagination", {})
                    if page_num >= pagination.get("totalPages", 1):
                        break
                    page_num += 1
                    await asyncio.sleep(1.0)
            finally:
                await browser.close()
        return reviews
