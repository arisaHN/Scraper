import json
import re
from datetime import datetime
from typing import Iterator, Optional

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

_SITE_BASE = "https://it.primor.eu"
_SITEMAP_URL = f"{_SITE_BASE}/media/sitemap_it_product_product.xml"
_REVIEWS_BASE = "https://reviews.primor.eu"

# Product page slugs end in "-<digits>.html" (e.g. dior-siero-...-14353.html).
_SLUG_ID_RE = re.compile(r"-(\d+)\.html$")

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL
)
_REVIEWS_JSON_RE = re.compile(
    r'<script type="applications/json" id="pr-reviews-json">\s*(\{.*?\})\s*</script>',
    re.DOTALL,
)


def _norm(name: str) -> str:
    """Casefold + drop non-alphanumerics, for tolerant brand-name matching."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


_VARIANT_ID_RE = re.compile(r"#variant-(.+)$")


def _extract_product_jsonld(html: str) -> Optional[dict]:
    """Find the plain schema.org Product JSON-LD block (data-company="mageworx"), which
    has clean sku/brand.name/category fields — distinct from a second, GS1-vocabulary
    "gs1:Product" block also present on the page that nests brand/name differently and
    has no plain sku field."""
    for raw in _JSONLD_RE.findall(html):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("@type") == "Product":
            return data
    return None


def _extract_variant_skus(html: str) -> list[str]:
    """Return child-variant SKUs from the gs1:Product JSON-LD block's gs1:hasVariant list.

    Configurable (multi-size) products' plain Product.sku is a shared parent/master SKU
    (observed with an "M-" prefix, e.g. "M-4AM03121") that has no reviews page of its own —
    fetching it returns HTTP 200 with an empty body. Each real, purchasable variant (e.g.
    60ML/100ML/150ML) has its own SKU, embedded in this block as the "#variant-<sku>"
    fragment of its @id, and each has an independent reviews page. Simple (single-SKU,
    non-configurable) products have no gs1:hasVariant at all, so this returns [] for them
    and the caller falls back to the plain Product.sku.
    """
    for raw in _JSONLD_RE.findall(html):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get("@type") != "gs1:Product":
            continue
        variants = data.get("gs1:hasVariant") or []
        if isinstance(variants, dict):
            variants = [variants]
        skus = []
        for v in variants:
            m = _VARIANT_ID_RE.search(v.get("@id") or "")
            if m:
                skus.append(m.group(1))
        return skus
    return []


class PrimorScraper(BaseScraper):
    """Scraper for it.primor.eu — a Magento 2 (Hyva theme) store with no bot protection
    anywhere in the chain (main site, sitemap, or reviews subdomain). Pure ``requests``,
    no browser needed.

    Discovery: filters the full-catalog product sitemap by a tolerant substring match on
    the brand name (not a strict prefix — some brands' slugs are inconsistent, e.g. Armani
    appears both as "giorgio-armani-giorgio-armani-..." and "armani-..."), then confirms
    each candidate by fetching its product page and checking the embedded ``gs1:Product``
    JSON-LD block's ``brand.name`` field. That same JSON-LD gives ``name``/``sku``/
    ``category`` for free, so confirmation and metadata extraction are a single fetch.

    Reviews: despite Trusted Shops domains appearing in the site's CSP, review data is not
    scraped HTML — it's a clean JSON blob embedded in the reviews page
    (``<script type="applications/json" id="pr-reviews-json">``), fetched from a static,
    unauthenticated CloudFront/S3 URL derived from a SKU by splitting its first six
    characters into path segments (e.g. SKU "0TF14305" ->
    ".../it/0/T/F/1/4/3/0TF14305_reviews.html"). **Configurable (multi-size) products**
    don't use the plain Product block's ``sku`` for this — that's a shared parent/master
    SKU (observed with an "M-" prefix) whose reviews URL returns HTTP 200 with an empty
    body. Instead, ``scrape_reviews()`` reads each real child-variant SKU from the
    ``gs1:Product`` block's ``gs1:hasVariant`` list (``_extract_variant_skus()``) and
    aggregates reviews across every variant's own reviews page; simple (single-SKU)
    products have no ``gs1:hasVariant`` and fall back to the plain ``sku`` as before. No
    native per-review ID exists in the payload, so ``external_review_id`` is a sha1 hash of
    stable fields (sku/author/date/text) — stable across re-fetches, but a re-rendered/
    edited review would be treated as new. No verified-purchase flag or review title exists
    either. Review order isn't confirmed sorted, so each review is filtered against
    ``since`` individually rather than early-stopping.
    """

    site_name = "primor"
    supports_backfill = False  # single JSON payload per product, no per-request bot cost

    # Product pages intermittently render without the Product JSON-LD block (~30-40% of
    # fetches observed, seemingly a CDN/cache variant rather than a bot-block — the HTTP
    # status is always 200 and page size only differs by a few KB), so a plain single fetch
    # isn't reliable. Retry a few times before treating a product as having no SKU/brand data.
    _JSONLD_FETCH_ATTEMPTS = 4

    def _fetch_verified_html(self, url: str) -> Optional[str]:
        """Fetch a product page, retrying until the Product JSON-LD block is present."""
        for _ in range(self._JSONLD_FETCH_ATTEMPTS):
            html = self._get(url).text
            if _extract_product_jsonld(html):
                return html
        return None

    # ── product discovery ─────────────────────────────────────────────────────────

    def _sitemap_candidates(self, brand_name: str) -> list[str]:
        """Return sitemap product URLs whose slug contains the brand name (tolerant,
        substring not prefix — required to catch inconsistent slug forms like Armani's)."""
        xml = self._get(_SITEMAP_URL).text
        target = _norm(brand_name)
        out = []
        for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
            slug = loc.rsplit("/", 1)[-1]
            if target in _norm(slug):
                out.append(loc)
        return out

    def discover_products(self, brand_name: str) -> list[dict]:
        """Discover all of a brand's products from the sitemap, confirming each candidate
        by its product page's gs1:Product JSON-LD brand field."""
        target = _norm(brand_name)
        products: dict[str, dict] = {}

        for url in self._sitemap_candidates(brand_name):
            m = _SLUG_ID_RE.search(url)
            if not m:
                continue
            external_id = m.group(1)
            if external_id in products:
                continue
            try:
                html = self._fetch_verified_html(url)
            except Exception as exc:
                print(f"  [primor] skip {url} — page fetch failed: {exc}", flush=True)
                continue
            if not html:
                continue
            data = _extract_product_jsonld(html)
            brand_field = data.get("brand") or {}
            brand_field_name = brand_field.get("name") if isinstance(brand_field, dict) else brand_field
            if _norm(brand_field_name or "") != target:
                continue  # slug-substring false positive

            products[external_id] = {
                "external_id": external_id,
                "name": data.get("name") or url.rsplit("/", 1)[-1],
                "source_url": url,
                "category": data.get("category"),
            }

        return list(products.values())

    # ── review scraping ───────────────────────────────────────────────────────────

    @staticmethod
    def _reviews_url(sku: str) -> str:
        segments = "/".join(sku[:6])
        return f"{_REVIEWS_BASE}/it/{segments}/{sku}_reviews.html"

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        html = self._fetch_verified_html(product["source_url"])
        if not html:
            print(f"  [primor] no product data found for {product['source_url']}", flush=True)
            return

        skus = _extract_variant_skus(html)
        if not skus:
            data = _extract_product_jsonld(html)
            sku = (data.get("sku") or data.get("productID")) if data else None
            skus = [sku] if sku else []
        if not skus:
            print(f"  [primor] no SKU found for {product['source_url']}", flush=True)
            return

        for sku in skus:
            resp = self._get(self._reviews_url(sku))
            m = _REVIEWS_JSON_RE.search(resp.text)
            if not m:
                continue  # this variant has no reviews yet — not fatal, check the next one
            try:
                payload = json.loads(m.group(1))
            except Exception:
                continue

            for raw in payload.get("reviews") or []:
                review = ReviewNormalizer.from_primor(raw, sku)
                if self._past_cutoff(review.review_date, since):
                    continue
                yield review
