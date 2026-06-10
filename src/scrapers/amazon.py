import asyncio
import re
from typing import Iterator

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

SEARCH_URL = "https://www.amazon.com/s?k={brand}"
REVIEWS_URL = "https://www.amazon.com/product-reviews/{asin}?pageNumber={page}"


class AmazonScraper(BaseScraper):
    site_name = "amazon"

    def discover_products(self, brand_name: str) -> list[dict]:
        return asyncio.run(self._async_discover(brand_name))

    def scrape_reviews(self, product: dict) -> Iterator[NormalizedReview]:
        yield from asyncio.run(self._async_scrape(product))

    async def _async_discover(self, brand_name: str) -> list[dict]:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth

        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await Stealth().apply_stealth_async(page)
            try:
                await page.goto(
                    SEARCH_URL.format(brand=brand_name),
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                cards = await page.query_selector_all("[data-asin]")
                seen = set()
                for card in cards:
                    asin = await card.get_attribute("data-asin")
                    if not asin or len(asin) != 10 or asin in seen:
                        continue
                    seen.add(asin)
                    title_el = await card.query_selector("h2 span")
                    name = (await title_el.inner_text()).strip() if title_el else asin
                    results.append(
                        {
                            "name": name,
                            "source_url": f"https://www.amazon.com/dp/{asin}",
                            "external_id": asin,
                        }
                    )
            finally:
                await browser.close()
        return results

    async def _async_scrape(self, product: dict) -> list[NormalizedReview]:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth

        reviews = []
        asin = product["external_id"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await Stealth().apply_stealth_async(page)
            try:
                page_num = 1
                while True:
                    url = REVIEWS_URL.format(asin=asin, page=page_num)
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    items = await page.query_selector_all('[data-hook="review"]')
                    if not items:
                        break
                    for item in items:
                        review_id = await item.get_attribute("id") or ""
                        author_el = await item.query_selector('[class*="profile-name"]')
                        author = (await author_el.inner_text()).strip() if author_el else None
                        rating_el = await item.query_selector(
                            '[data-hook="review-star-rating"] span'
                        )
                        rating_txt = (await rating_el.inner_text()) if rating_el else "0"
                        rating = float(rating_txt.split(" ")[0]) if rating_txt else None
                        title_el = await item.query_selector(
                            '[data-hook="review-title"] span:last-child'
                        )
                        title = (await title_el.inner_text()).strip() if title_el else None
                        body_el = await item.query_selector('[data-hook="review-body"] span')
                        text = (await body_el.inner_text()).strip() if body_el else None
                        date_el = await item.query_selector('[data-hook="review-date"]')
                        date_txt = (await date_el.inner_text()) if date_el else None
                        helpful_el = await item.query_selector(
                            '[data-hook="helpful-vote-statement"]'
                        )
                        helpful_txt = (await helpful_el.inner_text()) if helpful_el else "0"
                        verified_el = await item.query_selector('[data-hook="avp-badge"]')
                        raw = {
                            "review_id": review_id,
                            "author": author,
                            "rating": rating,
                            "title": title,
                            "text": text,
                            "date": date_txt,
                            "helpful_count": _parse_helpful(helpful_txt),
                            "verified_purchase": verified_el is not None,
                        }
                        reviews.append(ReviewNormalizer.from_amazon(raw))
                    next_btn = await page.query_selector(
                        '[data-hook="pagination-bar"] .a-last:not(.a-disabled)'
                    )
                    if not next_btn:
                        break
                    page_num += 1
                    await asyncio.sleep(1.5)
            finally:
                await browser.close()
        return reviews


def _parse_helpful(text: str) -> int:
    m = re.search(r"(\d[\d,]*)", text)
    return int(m.group(1).replace(",", "")) if m else 0
