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
    unauthenticated CloudFront/S3 URL derived from the product's SKU by splitting its first
    six characters into path segments (e.g. SKU "0TF14305" ->
    ".../it/0/T/F/1/4/3/0TF14305_reviews.html"). No native per-review ID exists in that
    payload, so ``external_review_id`` is a sha1 hash of stable fields (sku/author/date/
    text) — stable across re-fetches, but a re-rendered/edited review would be treated as
    new. No verified-purchase flag or review title exists either. Review order isn't
    confirmed sorted, so each review is filtered against ``since`` individually rather than
    early-stopping.
    """

    site_name = "primor"
    supports_backfill = False  # single JSON payload per product, no per-request bot cost

    # Product pages intermittently render without the Product JSON-LD block (~30-40% of
    # fetches observed, seemingly a CDN/cache variant rather than a bot-block — the HTTP
    # status is always 200 and page size only differs by a few KB), so a plain single fetch
    # isn't reliable. Retry a few times before treating a product as having no SKU/brand data.
    _JSONLD_FETCH_ATTEMPTS = 4

    def _fetch_product_jsonld(self, url: str) -> Optional[dict]:
        for _ in range(self._JSONLD_FETCH_ATTEMPTS):
            html = self._get(url).text
            data = _extract_product_jsonld(html)
            if data:
                return data
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
                data = self._fetch_product_jsonld(url)
            except Exception as exc:
                print(f"  [primor] skip {url} — page fetch failed: {exc}", flush=True)
                continue
            if not data:
                continue
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
        data = self._fetch_product_jsonld(product["source_url"])
        sku = (data.get("sku") or data.get("productID")) if data else None
        if not sku:
            print(f"  [primor] no SKU found for {product['source_url']}", flush=True)
            return

        resp = self._get(self._reviews_url(sku))
        m = _REVIEWS_JSON_RE.search(resp.text)
        if not m:
            return
        try:
            payload = json.loads(m.group(1))
        except Exception:
            return

        for raw in payload.get("reviews") or []:
            review = ReviewNormalizer.from_primor(raw, sku)
            if self._past_cutoff(review.review_date, since):
                continue
            yield review
