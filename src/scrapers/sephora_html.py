import hashlib
import re
from datetime import datetime
from typing import Iterator, Optional

from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

BRAND_HUB_URL     = "https://www.sephora.it/{brand_lower}/{brand_upper}-HubPage.html"
BRAND_CATALOG_URL = "https://www.sephora.it/marche/dalla-a-alla-z/{brand_lower}-{brand_lower}/"

_SEL_REVIEW_LIST = "#product-detail-reviews li"
_SEL_LOAD_MORE   = "[data-testid='load-more-button']"
_SEL_REVIEW_SEC  = "[data-testid='product-reviews__sec']"

# Italian month abbreviations → English (for dateutil parsing)
_IT_MONTHS = {
    "gen": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr",
    "mag": "May", "giu": "Jun", "lug": "Jul", "ago": "Aug",
    "set": "Sep", "ott": "Oct", "nov": "Nov", "dic": "Dec",
}

# JS that extracts all fields from a single review <li>
_EXTRACT_JS = """li => {
    const stars = [...li.querySelectorAll('span[style*="--fillRatio"]')];
    const rawRating = Math.round(stars.reduce((sum, s) => {
        const m = (s.getAttribute('style') || '').match(/--fillRatio:\\s*([\\d.]+)/);
        return sum + (m ? parseFloat(m[1]) : 0);
    }, 0));
    const rating = Math.max(0, Math.min(5, rawRating));

    const topRow = li.querySelector(':scope > div:first-child');
    const dateEl = topRow ? topRow.querySelector(':scope > p') : null;

    // Only non-empty bold paragraphs are real author/title text, not icon-only badges
    const boldPs = [...li.querySelectorAll('p')]
        .filter(p => p.className.includes('font-bold'))
        .map(p => p.textContent.trim())
        .filter(t => t.length > 0);
    const author = boldPs[0] || null;
    const title  = boldPs[1] || null;

    const textEl = li.querySelector('p[class*="overflow-wrap"]');
    let text = null;
    if (textEl) {
        const clone = textEl.cloneNode(true);
        clone.querySelectorAll('button').forEach(b => b.remove());
        text = clone.textContent.trim() || null;
    }

    // Primary signal is the verified-badge icon color; fall back to the Italian/English
    // "verified" text in case Sephora restyles the badge without changing its copy.
    const verified = !!li.querySelector('svg rect[fill="#005B00"]')
        || /verificat|verified/i.test(li.textContent || '');

    return {
        author,
        date: dateEl ? dateEl.textContent.trim() : null,
        title,
        text,
        rating,
        verified,
    };
}"""


def _parse_it_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        from dateutil import parser as du
        # Translate Italian abbreviation: "11 giu 2026" → "11 Jun 2026"
        normalized = re.sub(
            r"\b(" + "|".join(_IT_MONTHS) + r")\b",
            lambda m: _IT_MONTHS[m.group(1)],
            date_str.lower(),
        )
        return du.parse(normalized, dayfirst=True)
    except Exception:
        return None


class SephoraHTMLScraper(BaseScraper):
    site_name = "sephora"

    def __init__(self):
        super().__init__()
        self._camoufox = Camoufox(headless=True, geoip=True)
        self._browser = self._camoufox.__enter__()

    def close(self):
        try:
            self._camoufox.__exit__(None, None, None)
        except Exception:
            pass

    def __del__(self):
        self.close()

    def _new_page(self):
        page = self._browser.new_page()
        page.on("pageerror", lambda _: None)
        return page

    def _refresh_browser(self):
        """Close and reopen Camoufox to get a fresh browser fingerprint."""
        self.close()
        self._camoufox = Camoufox(headless=True, geoip=True)
        self._browser = self._camoufox.__enter__()

    def _wait_and_consent(self, page, url: str):
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_function("document.body && document.body.innerHTML.length > 10000", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=20_000)
        # Wait for React to hydrate — at least one data-testid must appear
        try:
            page.wait_for_function("document.querySelectorAll('[data-testid]').length > 0", timeout=15_000)
        except Exception:
            pass  # proceed even if no data-testid appear (some pages may not have them)
        for selector in [
            "button:has-text('Accetta')", "button:has-text('Accetto')",
            "button:has-text('Accept')", "button[id*='accept']",
            "[data-testid*='accept']",
        ]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=5_000)
                    break
            except Exception:
                pass

    def discover_products(self, brand_name: str) -> list[dict]:
        brand_lower = brand_name.lower()
        brand_upper = brand_name.upper()
        hub_url = BRAND_HUB_URL.format(brand_lower=brand_lower, brand_upper=brand_upper)
        catalog_base = BRAND_CATALOG_URL.format(brand_lower=brand_lower)

        # Step 1: collect all brand-specific scgid category URLs from the hub page
        page = self._new_page()
        try:
            self._wait_and_consent(page, hub_url)
            scgid_urls = page.evaluate(f"""() =>
                [...new Set(
                    [...document.querySelectorAll('a[href]')]
                        .map(a => a.getAttribute('href'))
                        .filter(h => h && h.includes('{brand_lower}-{brand_lower}') && h.includes('scgid=C'))
                        .map(h => h.split('#')[0])  // strip #topbreadcrumb fragments
                )]
            """)
        finally:
            page.close()

        if not scgid_urls:
            scgid_urls = [catalog_base]  # fallback: try base catalog URL

        # Step 2: visit each category tab with a fresh browser to avoid bot detection
        seen = set()
        results = []
        for cat_url in scgid_urls:
            if not cat_url.startswith("http"):
                cat_url = f"https://www.sephora.it{cat_url}"
            self._refresh_browser()
            page = self._new_page()
            try:
                self._wait_and_consent(page, cat_url)
                hrefs = page.evaluate("""() =>
                    [...new Set(
                        [...document.querySelectorAll('a[href]')]
                            .map(a => a.getAttribute('href'))
                            .filter(h => h && /-(P\\d+)\\.html/.test(h))
                    )]
                """)
                for href in hrefs:
                    m = re.search(r"-(P\d+)\.html", href)
                    if not m:
                        continue
                    external_id = m.group(1)
                    if external_id in seen:
                        continue
                    seen.add(external_id)
                    source_url = href if href.startswith("http") else f"https://www.sephora.it{href}"
                    slug = href.rsplit("/", 1)[-1].replace(f"-{external_id}.html", "").replace("-", " ").title()
                    results.append({"name": slug, "source_url": source_url, "external_id": external_id})
            except Exception:
                pass
            finally:
                page.close()
            self._polite_delay()

        return results

    def scrape_reviews(self, product: dict, since: Optional[datetime] = None) -> Iterator[NormalizedReview]:
        if not product.get("source_url"):
            raise ValueError(
                f"No source_url for product {product.get('external_id')!r} — run a full "
                f"scrape (without --product-id) at least once so it can be discovered first."
            )
        self._refresh_browser()
        page = self._new_page()
        try:
            self._wait_and_consent(page, product["source_url"])
            # Wait specifically for review items — the general wait only guarantees header/nav loaded
            try:
                page.wait_for_selector(_SEL_REVIEW_LIST, timeout=20_000)
            except PlaywrightTimeout:
                print(f"    [sephora] no reviews found on {product['source_url']}")
                return
            processed = 0
            while True:
                stop = False
                items = page.query_selector_all(_SEL_REVIEW_LIST)
                for li in items[processed:]:  # only the newly loaded ones since last pass
                    raw = _extract_review_fields(li)
                    if not raw:
                        continue
                    review = ReviewNormalizer.from_sephora(raw)
                    if self._past_cutoff(review.review_date, since):
                        stop = True
                        break
                    yield review
                processed = len(items)
                if stop:
                    break
                load_more = page.query_selector(_SEL_LOAD_MORE)
                if not load_more:
                    break
                try:
                    if not load_more.is_enabled():
                        break
                    load_more.click()
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    self._polite_delay()
                except PlaywrightTimeout:
                    break
        finally:
            page.close()

    def inspect(self, url: str) -> None:
        """Print DOM structure — use to verify selectors against the live page."""
        page = self._new_page()
        self._wait_and_consent(page, url)

        html = page.content()
        print(f"\nTitle     : {page.title()}")
        print(f"URL       : {page.url}")
        print(f"HTML len  : {len(html)}")
        print(f"HTML head : {html[:1000]}\n")

        els = page.query_selector_all("[data-testid]")
        testids = sorted({el.get_attribute("data-testid") for el in els if el.get_attribute("data-testid")})
        print(f"\n{len(testids)} data-testid values:\n")
        for t in testids:
            print(f"  {t}")

        print("\n--- Scrolling to load all products (infinite scroll) ---")
        prev_count = 0
        for scroll_i in range(30):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            current_count = page.evaluate("document.querySelectorAll('a[href]').length")
            print(f"  scroll {scroll_i + 1}: {current_count} links")
            if current_count == prev_count:
                break
            prev_count = current_count

        print("\n--- Product links (-(P\\d+).html pattern) ---")
        hrefs = page.evaluate("""() =>
            [...new Set(
                [...document.querySelectorAll('a[href]')]
                    .map(a => a.getAttribute('href'))
                    .filter(h => h && /-(P\\d+)\\.html/.test(h))
            )]
        """)
        print(f"Found {len(hrefs)} product links")
        for h in hrefs[:10]:
            print(f"  {h}")

        print("\n--- Review list (product pages only) ---")
        items = page.query_selector_all(_SEL_REVIEW_LIST)
        print(f"Total <li> count: {len(items)}")
        for i, li in enumerate(items[:3]):
            raw = _extract_review_fields(li)
            print(f"\n[review {i}]: {raw}")

        page.close()


def _extract_review_fields(el) -> Optional[dict]:
    try:
        data = el.evaluate(_EXTRACT_JS)
        date_str = data.get("date")
        # Full text + rating included (not just a text prefix) to minimize collisions
        # between distinct anonymous reviews posted on the same day.
        id_src = "|".join([
            "sephora",
            str(data.get("author")),
            str(date_str),
            str(data.get("rating")),
            data.get("text") or "",
        ])
        data["id"] = hashlib.sha256(id_src.encode()).hexdigest()[:32]
        data["parsed_date"] = _parse_it_date(date_str)
        return data
    except Exception:
        return None
