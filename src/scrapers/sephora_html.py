import json
import os
import re
from datetime import datetime
from html import unescape
from typing import Iterator, Optional
from urllib.parse import quote, unquote

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper, CamoufoxBrowserMixin

BRAND_HUB_URL     = "https://www.sephora.it/{brand_lower}/{brand_upper}-HubPage.html"
BRAND_CATALOG_URL = "https://www.sephora.it/marche/dalla-a-alla-z/{brand_lower}-{brand_lower}/"

REVIEWS_LIMIT = 22
REVIEWS_SORT_DESC = "SubmissionTime:desc"
REVIEWS_SORT_ASC = "SubmissionTime:asc"
LOCALE = "it-IT"

# Hash of the "getReviews" server function. Stable across requests but changes
# whenever Sephora redeploys the page bundle — override via env if requests start
# coming back as HTML instead of the expected RSC stream.
NEXT_ACTION_ID = os.environ.get(
    "SEPHORA_NEXT_ACTION_ID", "7c17146d57784229b528b62e1d5fbd1c918c08fee3"
)

# Runs inside the page itself so the request carries the page's real cookies, TLS
# fingerprint, and browser-set headers (sec-fetch-*, sec-ch-ua, etc.) — those can't
# be replicated from a plain Python HTTP client and get blocked by Akamai's edge WAF
# (a generic "Access Denied" page) if missing.
_FETCH_JS = """
async ({url, nextAction, routerStateTree, body}) => {
    const resp = await fetch(url, {
        method: 'POST',
        headers: {
            'accept': 'text/x-component',
            'content-type': 'text/plain;charset=UTF-8',
            'next-action': nextAction,
            'next-router-state-tree': routerStateTree,
        },
        body,
    });
    return { status: resp.status, text: await resp.text() };
}
"""


def _router_state_tree(slug: str) -> str:
    """Build the `next-router-state-tree` header value Next.js expects for a /p/{slug} page."""
    tree = [
        "",
        {
            "children": [
                ["locale", LOCALE, "d", None],
                {
                    "children": [
                        "(product-detail)",
                        {
                            "children": [
                                "p",
                                {
                                    "children": [
                                        ["slug", slug, "d", None],
                                        {"children": ["__PAGE__", {}, None, None, 0]},
                                        None, None, 0,
                                    ]
                                },
                                None, None, 0,
                            ]
                        },
                        None, None, 0,
                    ]
                },
                None, None, 16,
            ]
        },
        None, None, 0,
    ]
    return quote(json.dumps(tree, separators=(",", ":")))


_CHUNK_REF_RE = re.compile(r"\$(\d+)$")


def _parse_rsc_chunks(text: str) -> dict:
    """Split a Next.js Flight (RSC) stream into {chunk_id: resolved_value}.

    Each chunk is `<id>:<payload>`. Most payloads are inline JSON terminated by a
    literal newline, but long strings (e.g. review text) are sent as `T<hexLen>,<bytes>`
    — exactly `hexLen` UTF-8 bytes, which may themselves contain embedded newlines, so
    they can't be split on '\\n' like the JSON chunks. JSON chunks reference such string
    chunks via `"$<id>"` placeholders, resolved here by substitution once all chunks are
    parsed.
    """
    raw = text.encode("utf-8")
    n = len(raw)
    pos = 0
    chunks: dict[str, object] = {}
    while pos < n:
        while pos < n and raw[pos : pos + 1] in (b"\n", b"\r"):
            pos += 1
        if pos >= n:
            break
        m = re.match(rb"(\d+):", raw[pos:])
        if not m:
            break
        chunk_id = m.group(1).decode()
        pos += m.end()
        if raw[pos : pos + 1] == b"T":
            m2 = re.match(rb"T([0-9a-fA-F]+),", raw[pos:])
            if not m2:
                break
            length = int(m2.group(1), 16)
            start = pos + m2.end()
            chunks[chunk_id] = raw[start : start + length].decode("utf-8", errors="replace")
            pos = start + length
        else:
            end = raw.find(b"\n", pos)
            if end == -1:
                end = n
            payload = raw[pos:end].decode("utf-8", errors="replace")
            try:
                chunks[chunk_id] = json.loads(payload)
            except (ValueError, TypeError):
                chunks[chunk_id] = payload
            pos = end

    def resolve(value, _seen=frozenset()):
        if isinstance(value, str):
            m = _CHUNK_REF_RE.match(value)
            if m and m.group(1) in chunks and m.group(1) not in _seen:
                return resolve(chunks[m.group(1)], _seen | {m.group(1)})
            return value
        if isinstance(value, dict):
            return {k: resolve(v, _seen) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v, _seen) for v in value]
        return value

    return {cid: resolve(val) for cid, val in chunks.items()}


def _parse_rsc(text: str) -> Optional[dict]:
    """Extract the `data` payload (`{reviewCount, reviews}`) from an RSC stream response.

    Returns None if no chunk has that shape (e.g. Akamai served an HTML challenge
    page instead of the RSC stream).
    """
    for value in _parse_rsc_chunks(text).values():
        if not (isinstance(value, dict) and isinstance(value.get("data"), dict)):
            continue
        data = value["data"]
        # A product with zero reviews may omit the "reviews" key entirely rather than
        # sending an empty list — treat "reviewCount" alone as enough to recognize a
        # valid (if empty) payload, so it isn't mistaken for an Akamai block.
        if "reviews" in data or "reviewCount" in data:
            data.setdefault("reviews", [])
            return data
    return None


def _safe_from_sephora(raw: dict) -> Optional[NormalizedReview]:
    """Wraps ReviewNormalizer.from_sephora so one malformed review (e.g. a moderated
    review missing an expected field) is skipped instead of aborting the whole product."""
    try:
        return ReviewNormalizer.from_sephora(raw)
    except Exception as exc:
        print(f"    [sephora] skipping malformed review {raw.get('id')!r}: {exc}")
        return None


class SephoraHTMLScraper(CamoufoxBrowserMixin, BaseScraper):
    site_name = "sephora"
    supports_backfill = True

    def __init__(self):
        super().__init__()
        # Updated during scrape_reviews(); runner.py reads these after the generator is
        # exhausted to persist backfill progress for the next run.
        self.backfill_offset: Optional[int] = None
        self.backfill_completed: bool = False
        self.backfill_total: Optional[int] = None

    def _wait_and_consent(self, page, url: str):
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_function("document.body && document.body.innerHTML.length > 10000", timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass  # analytics beacons (e.g. Dynatrace) can prevent networkidle — page is still usable
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

    # ── product discovery (unchanged: still needs a real browser to render links) ──

    def discover_products(self, brand_name: str) -> list[dict]:
        brand_lower = brand_name.lower()
        brand_upper = brand_name.upper()
        hub_url = BRAND_HUB_URL.format(brand_lower=brand_lower, brand_upper=brand_upper)

        page = self._new_page()
        try:
            self._wait_and_consent(page, hub_url)

            # Collect category IDs (scgid=C*) from hub page — used for per-category SFCC lookups
            scgid_hrefs = page.evaluate(f"""() =>
                [...new Set(
                    [...document.querySelectorAll('a[href]')]
                        .map(a => a.getAttribute('href'))
                        .filter(h => h && h.includes('{brand_lower}-{brand_lower}') && h.includes('scgid=C'))
                        .map(h => h.split('#')[0])
                )]
            """)
            cgids = list({re.search(r'scgid=(C\w+)', h).group(1) for h in scgid_hrefs if re.search(r'scgid=(C\w+)', h)})

            # Use SFCC's own Search-Show AJAX endpoint, called via fetch() inside the live
            # browser tab so it carries the Akamai-validated session cookies. Direct
            # page.goto() to the catalog URL is blocked; this in-page fetch is not.
            # Query by brand name first, then by each category to maximise coverage.
            unique: dict[str, str] = {}

            def _fetch_sfcc(params: str) -> None:
                html = page.evaluate(f"""async () => {{
                    const resp = await fetch(
                        "/on/demandware.store/Sites-Sephora_IT-Site/it_IT/Search-Show?{params}&sz=500&format=ajax",
                        {{headers: {{"X-Requested-With": "XMLHttpRequest"}}}}
                    );
                    return await resp.text();
                }}""")
                decoded = unescape(html)
                for url in re.findall(r'https://www\.sephora\.it/p/[^\s"&]+\.html', decoded):
                    m = re.search(r"-(P\d+)\.html", url)
                    if m:
                        pid = m.group(1)
                        if pid not in unique:
                            unique[pid] = url

            try:
                _fetch_sfcc(f"q={brand_name}")
            except Exception:
                pass
            self._polite_delay()
            for cgid in cgids:
                try:
                    _fetch_sfcc(f"cgid={cgid}")
                except Exception:
                    pass
                self._polite_delay()

        finally:
            page.close()

        results = []
        for pid, url in unique.items():
            raw_slug = url.rsplit("/", 1)[-1].replace(f"-{pid}.html", "")
            name = unquote(raw_slug).replace("-", " ").title()
            results.append({"name": name, "source_url": url, "external_id": pid})
        return results

    # ── review fetching: the open product-page tab issues the POST itself via fetch() ──

    def _fetch_reviews_page(
        self, page, product_url: str, slug: str, external_id: str, offset: int, sort: str
    ) -> dict:
        body = json.dumps([external_id, offset, REVIEWS_LIMIT, [], sort], separators=(",", ":"))
        return page.evaluate(
            _FETCH_JS,
            {
                "url": product_url,
                "nextAction": NEXT_ACTION_ID,
                "routerStateTree": _router_state_tree(slug),
                "body": body,
            },
        )

    def _fetch_with_retry(
        self, page, product_url: str, slug: str, external_id: str, offset: int, sort: str
    ) -> dict:
        attempt = 0
        while True:
            result = self._fetch_reviews_page(page, product_url, slug, external_id, offset, sort)
            data = _parse_rsc(result["text"]) if result["status"] == 200 else None
            if data is not None:
                return data

            attempt += 1
            # The very first fetch() issued right after a page load sometimes gets
            # intercepted (Next's router prefetch/cache) and returns the full page
            # navigation payload instead of the action result — retrying the exact
            # same call (no reload) on attempt 1 reliably fixes it. A reload is only
            # tried as a deeper recovery if that retry also fails (e.g. real block).
            if attempt == 1:
                continue
            if attempt == 2:
                self._wait_and_consent(page, product_url)
                continue
            raise RuntimeError(
                f"Sephora review API blocked for product {external_id} even after "
                f"retrying and reloading the page (status={result['status']})."
            )

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        # Reset before any exception can be raised below — runner.py reads these via
        # getattr() in a `finally` block even if this call fails before fetching anything,
        # so they must reflect "no progress made this call" rather than stale state left
        # over from a previous product (this scraper instance is reused across a brand's
        # whole product list) or from __init__'s defaults.
        self.backfill_offset = backfill_offset
        self.backfill_completed = False
        self.backfill_total = None

        product_url = product.get("source_url")
        if not product_url:
            raise ValueError(
                f"No source_url for product {product.get('external_id')!r} — run a full "
                f"scrape (without --product-id) at least once so it can be discovered first."
            )
        external_id = product["external_id"]
        slug = product_url.rstrip("/").rsplit("/", 1)[-1]

        self._refresh_browser()
        page = self._new_page()
        try:
            self._wait_and_consent(page, product_url)

            # Watermark pass: newest-first, stops as soon as we hit a review older than
            # `since`. Only runs once a prior successful run exists — on a brand-new
            # product (since=None) there's no cutoff to stop pagination, which would
            # otherwise walk the *entire* review history before the backfill cap below
            # even applies. New products start straight into the capped backfill pass
            # instead, which will naturally reach the newest reviews once it completes.
            if since is not None:
                offset = 0
                while True:
                    data = self._fetch_with_retry(page, product_url, slug, external_id, offset, REVIEWS_SORT_DESC)
                    reviews_raw = data.get("reviews") or []
                    if self.backfill_total is None:
                        self.backfill_total = data.get("reviewCount")
                    if not reviews_raw:
                        break
                    stop = False
                    for raw in reviews_raw:
                        review = _safe_from_sephora(raw)
                        if review is None:
                            continue
                        if self._past_cutoff(review.review_date, since):
                            stop = True
                            break
                        yield review
                    if stop:
                        break
                    offset += len(reviews_raw)
                    if offset >= data.get("reviewCount", 0):
                        break
                    self._polite_delay()

            # Backfill pass: oldest-first, capped at max_backfill_pages requests this run.
            # Ascending order keeps the persisted offset stable across runs — new reviews
            # append at the end of an ascending list rather than shifting earlier offsets,
            # unlike descending order where every new review would invalidate the cursor.
            if backfill_offset is not None and max_backfill_pages and not self.backfill_completed:
                offset = backfill_offset
                for _ in range(max_backfill_pages):
                    data = self._fetch_with_retry(page, product_url, slug, external_id, offset, REVIEWS_SORT_ASC)
                    reviews_raw = data.get("reviews") or []
                    if self.backfill_total is None:
                        self.backfill_total = data.get("reviewCount")
                    for raw in reviews_raw:
                        review = _safe_from_sephora(raw)
                        if review is not None:
                            yield review
                    offset += len(reviews_raw)
                    self.backfill_offset = offset
                    if not reviews_raw or offset >= data.get("reviewCount", 0):
                        self.backfill_completed = True
                        break
                    self._polite_delay()
        finally:
            page.close()

    def inspect(self, url: str) -> None:
        """Print discovery + review-API diagnostics — use to verify the scraper still works."""
        page = self._new_page()
        self._wait_and_consent(page, url)

        html = page.content()
        print(f"\nTitle     : {page.title()}")
        print(f"URL       : {page.url}")
        print(f"HTML len  : {len(html)}")

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

        m = re.search(r"-(P\d+)\.html", url)
        if not m:
            print("\n(URL has no Pxxxxx product id — skipping review-API check)")
            page.close()
            return
        external_id = m.group(1)
        slug = url.rstrip("/").rsplit("/", 1)[-1]

        print(f"\n--- Review API check for {external_id} ---")
        result = self._fetch_reviews_page(page, url, slug, external_id, offset=0, sort=REVIEWS_SORT_DESC)
        page.close()
        print(f"status: {result['status']}")
        data = _parse_rsc(result["text"]) if result["status"] == 200 else None
        if data is None:
            print(f"Could not parse RSC payload — first 500 chars of response:\n{result['text'][:500]}")
            return
        print(f"reviewCount: {data.get('reviewCount')}")
        for raw in (data.get("reviews") or [])[:3]:
            print(f"\n[review]: {raw}")
            print(f"normalized: {ReviewNormalizer.from_sephora(raw)}")
