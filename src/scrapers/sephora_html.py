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

# Distinct product-detail links (/p/...-P<digits>.html) currently rendered in the DOM.
_PRODUCT_HREFS_JS = (
    "() => [...new Set([...document.querySelectorAll('a[href]')]"
    ".map(a => a.getAttribute('href'))"
    ".filter(h => h && /-P\\d+\\.html/.test(h)))]"
)

# Read a product's true brand off its rendered page: the first line of the H1 is the brand
# name (e.g. "DIOR", "ARMANI"), and the brand link next to it points to
# /marche/dalla-a-alla-z/<brand-slug>/. Used to detect products mislabeled under the wrong
# brand by the old discovery bug.
_BRAND_JS = """() => {
  const h1 = document.querySelector('h1');
  let href = null, line = null;
  if (h1) {
    line = (h1.innerText || '').split('\\n')[0].trim();
    let el = h1;
    for (let i = 0; i < 4 && el; i++) { el = el.parentElement; if (!el) break;
      const a = el.querySelector('a[href*="/marche/dalla-a-alla-z/"]');
      if (a) { href = a.getAttribute('href'); break; } }
  }
  return { href, line };
}"""


def _brand_slug_from_href(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    m = re.search(r"/dalla-a-alla-z/([^/]+)/", href)
    return m.group(1) if m else None

# How long to wait for a product page to render before giving up. Kept short so Akamai
# "Access Denied" pages (which never reach the content threshold) fail fast instead of
# burning ~30s each; a genuinely rendering page is well under this.
_PAGE_WAIT_MS = int(os.environ.get("SEPHORA_PAGE_WAIT_MS", "9000"))

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
        # A blocked "Access Denied" page never grows past this length threshold, so it would
        # otherwise burn the full timeout. Keep it short (env-overridable) so blocked pages
        # fail fast — a real page renders well under it — instead of ~30s each before the run
        # aborts on consecutive failures.
        page.wait_for_function(
            "document.body && document.body.innerHTML.length > 10000",
            timeout=_PAGE_WAIT_MS,
        )
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass  # analytics beacons (e.g. Dynatrace) can prevent networkidle — page is still usable
        # Wait for React to hydrate — at least one data-testid must appear
        try:
            page.wait_for_function("document.querySelectorAll('[data-testid]').length > 0", timeout=15_000)
        except Exception:
            pass  # proceed even if no data-testid appear (some pages may not have them)
        self._dismiss_consent(page)

    # ── product discovery (unchanged: still needs a real browser to render links) ──

    def discover_products(self, brand_name: str) -> list[dict]:
        brand_lower = brand_name.lower()
        brand_upper = brand_name.upper()
        hub_url = BRAND_HUB_URL.format(brand_lower=brand_lower, brand_upper=brand_upper)

        page = self._new_page()
        try:
            self._wait_and_consent(page, hub_url)

            # Diagnostics: a silent "0 products" is almost always an Akamai IP block (the hub
            # page comes back as an "Access Denied" shell) rather than a genuinely empty brand.
            # Surface enough state to tell those apart in the run log.
            diag = page.evaluate("""() => ({
                title: document.title,
                bodyLen: document.body ? document.body.innerHTML.length : 0,
                totalLinks: document.querySelectorAll('a[href]').length,
                blocked: /access denied|reference\\s*#?\\d|akamai|errors\\.edgesuite/i.test(
                    (document.body && document.body.innerText || '').slice(0, 4000)
                ),
            })""")
            if diag.get("blocked") or diag.get("totalLinks", 0) == 0:
                print(
                    f"  [sephora] WARNING: hub page looks blocked/empty — "
                    f"title={diag.get('title')!r} bodyLen={diag.get('bodyLen')} "
                    f"links={diag.get('totalLinks')} blocked={diag.get('blocked')}. "
                    f"This is typically an Akamai IP block, not an empty brand.",
                    flush=True,
                )

            # Collect category IDs (scgid=C*) from hub page — used for per-category SFCC lookups.
            # The hub page reuses the same scgid= URLs in several unrelated nav sections: a
            # brand mega-menu (link text is just the brand name), promotional banners (link
            # text is a generic "SCOPRI"/"Discover" CTA), and the actual category tab list —
            # the only one rendered as a plain `<li><a>Label</a></li>` with no class anywhere.
            # Only that shape gives real labels (e.g. "Profumi", "Make-up", "Capelli"),
            # captured here for free since we already visit each category tab below.
            scgid_links = page.evaluate(f"""() => {{
                const seen = new Map();
                for (const a of document.querySelectorAll('li > a[href]')) {{
                    if (a.className || (a.parentElement && a.parentElement.className)) continue;
                    const href = a.getAttribute('href');
                    if (!href || !href.includes('{brand_lower}-{brand_lower}') || !href.includes('scgid=C')) continue;
                    const key = href.split('#')[0];
                    if (!seen.has(key)) seen.set(key, a.textContent.trim());
                }}
                return [...seen.entries()];
            }}""")
            cgid_labels: dict[str, str] = {}
            for href, text in scgid_links:
                m = re.search(r'scgid=(C\w+)', href)
                if m and text:
                    cgid_labels.setdefault(m.group(1), text)
            cgids = list(cgid_labels)
            print(f"  [sephora] hub page: {len(scgid_links)} category links → {len(cgids)} category ids", flush=True)

            # Visit each brand category tab and read the rendered product grid from the DOM.
            #
            # Sephora moved the category grid to client-side rendering: the old
            # Search-Show?format=ajax fetch (and the prefn1=brand&prefv1= refinement) now
            # return an empty page shell — no product tiles. The grid is brand-scoped by the
            # /{brand}-{brand}/ catalog path itself, so no separate brand filter is needed.
            # Product URLs are unchanged (/p/...-P<digits>.html), so we just scrape the links
            # the grid renders, scrolling to trigger its lazy loading.
            unique: dict[str, str] = {}
            categories: dict[str, str] = {}

            def _extract_category(cgid: str) -> int:
                # sz=300 lifts the grid's default 24-item page; the rest still lazy-loads on
                # scroll, so we scroll until the rendered product count stops growing.
                cat_url = f"{BRAND_CATALOG_URL.format(brand_lower=brand_lower)}?scgid={cgid}&sz=300"
                # Camoufox→Sephora occasionally throws a transient HTTP/3 (QUIC) error
                # (NS_ERROR_NET_HTTP3_PROTOCOL_ERROR); retry the navigation a few times.
                for attempt in range(3):
                    try:
                        page.goto(cat_url, wait_until="domcontentloaded", timeout=60_000)
                        break
                    except Exception as exc:
                        if attempt == 2:
                            raise
                        print(f"  [sephora] category {cgid}: retry navigation ({exc.__class__.__name__})", flush=True)
                        page.wait_for_timeout(2000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                # Lazy grid — scroll until the product-link count stops growing. Allow a few
                # extra no-growth passes since the grid can stall briefly mid-load.
                prev = -1
                stalls = 0
                for _ in range(40):
                    hrefs = page.evaluate(_PRODUCT_HREFS_JS)
                    if len(hrefs) <= prev:
                        stalls += 1
                        if stalls >= 3:
                            break
                    else:
                        stalls = 0
                    prev = len(hrefs)
                    page.mouse.wheel(0, 6000)
                    page.wait_for_timeout(1200)
                found = 0
                for url in page.evaluate(_PRODUCT_HREFS_JS):
                    m = re.search(r"-(P\d+)\.html", url)
                    if m:
                        found += 1
                        full = url if url.startswith("http") else f"https://www.sephora.it{url}"
                        unique.setdefault(m.group(1), full)
                        categories.setdefault(m.group(1), cgid_labels.get(cgid))
                return found

            for cgid in cgids:
                try:
                    n = _extract_category(cgid)
                    print(f"  [sephora] category {cgid}: {n} product links", flush=True)
                except Exception as exc:
                    print(f"  [sephora] category {cgid}: error — {exc}", flush=True)
                self._polite_delay()

        finally:
            page.close()

        results = []
        for pid, url in unique.items():
            raw_slug = url.rsplit("/", 1)[-1].replace(f"-{pid}.html", "")
            name = unquote(raw_slug).replace("-", " ").title()
            results.append({"name": name, "source_url": url, "external_id": pid, "category": categories.get(pid)})
        return results

    def fetch_brand(self, product: dict) -> Optional[str]:
        """Return a product's true brand slug (e.g. ``dior-dior``) from its rendered page.

        Used to detect products the old discovery bug filed under the wrong brand. Raises on
        an Akamai "Access Denied" page so a *block* is never mistaken for "different brand"
        (which would wrongly delete a genuine product). Returns None only when the page loaded
        but no brand element was found (ambiguous — caller should not delete).
        """
        url = product.get("source_url")
        if not url:
            return None
        page = self._new_page()
        try:
            self._wait_and_consent(page, url)
            info = page.evaluate(_BRAND_JS)
            line = (info.get("line") or "").strip()
            if not info or line.lower() == "access denied":
                raise RuntimeError(f"Akamai block reading brand for {product.get('external_id')}")
            return _brand_slug_from_href(info.get("href"))
        finally:
            page.close()

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
