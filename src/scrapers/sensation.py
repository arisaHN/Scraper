import re
from datetime import datetime
from typing import Iterator, Optional

from ..normalizer import NormalizedReview, ReviewNormalizer
from .base import BaseScraper

_API_BASE = "https://api.sensationprofumerie.it/api"
_SITEMAP_URL = "https://www.sensationprofumerie.it/sitemap/prodotti_it.xml"
_SITE_BASE = "https://www.sensationprofumerie.it"

# Product page slugs end in "-P<digits>" (e.g. dior-sauvage-elixir-P135324).
_SLUG_ID_RE = re.compile(r"-P(\d+)$")


def _norm(name: str) -> str:
    """Casefold + drop non-alphanumerics, for tolerant brand-name matching."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


class SensationScraper(BaseScraper):
    """Scraper for sensationprofumerie.it — a custom Angular SPA backed by a plain
    Express/JSON API on api.sensationprofumerie.it.

    No browser needed: unlike the www frontend (Cloudflare), the api.* subdomain has no
    bot gating, so both discovery and review fetching use plain ``requests`` like
    ``BazaarvoiceScraper``.

    The reviews endpoint aggregates a product's reviews across its sibling/variant
    productIds (the reviews returned for one productId carry their own, differing
    productIds). Cross-variant overlap is harmless: the DB's
    ``UNIQUE(source_site, external_review_id)`` + ``ON CONFLICT DO NOTHING`` dedup keeps
    each review once, attached to whichever variant was scraped first.
    """

    site_name = "sensation"
    supports_backfill = False  # full single-call fetch each run; no per-request bot cost

    # ── brand resolution ──────────────────────────────────────────────────────────

    def _resolve_brand(self, brand_name: str) -> Optional[dict]:
        """Return the canonical brand record ({brandId, name, slug, ...}) from /api/brands.

        Matches by tolerant (alnum-only, casefolded) comparison on name or slug, so
        "dior"/"DIOR"/"Dior" all resolve to the same brand. Returns None if no match.
        """
        resp = self._get(f"{_API_BASE}/brands")
        target = _norm(brand_name)
        for brand in resp.json():
            if _norm(brand.get("name") or "") == target or _norm(brand.get("slug") or "") == target:
                return brand
        return None

    # ── product discovery ─────────────────────────────────────────────────────────
    #
    # Discovery uses the product SITEMAP, not /api/indexing/search. The search endpoint
    # is a curated autocomplete index capped at ~264 hits/brand and misses the bulk of a
    # brand's catalog (e.g. DIOR: 264 via search vs ~950 real products). The sitemap lists
    # every product page; product slugs are prefixed with the brand slug
    # (dior-sauvage-elixir-P135324, and fused forms like diorshow-…-P139306), so we filter
    # by that prefix and then confirm each candidate's brandId against the product detail
    # endpoint — cheap because the API has no bot gating.

    def _sitemap_candidate_ids(self, brand_slug: str) -> list[tuple[str, str]]:
        """Return (product_id, slug) for every sitemap product slug starting with the
        brand slug. A loose prefix (no trailing '-') is deliberate so fused brand-name
        slugs like 'diorshow-…' are included; false positives are dropped later by the
        brandId confirmation step."""
        xml = self._get(_SITEMAP_URL).text
        prefix = brand_slug.lower()
        out: list[tuple[str, str]] = []
        for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
            slug = loc.rsplit("/", 1)[-1]
            if not slug.lower().startswith(prefix):
                continue
            m = _SLUG_ID_RE.search(slug)
            if m:
                out.append((m.group(1), slug))
        return out

    def discover_products(self, brand_name: str) -> list[dict]:
        """Discover all of a brand's products from the sitemap, confirming each by brandId.

        Falls back to an empty list if the brand can't be resolved from /api/brands.
        """
        brand = self._resolve_brand(brand_name)
        if not brand:
            print(f"  [sensation] WARNING: brand {brand_name!r} not found in /api/brands", flush=True)
            return []

        brand_id = str(brand.get("brandId"))
        candidates = self._sitemap_candidate_ids(brand.get("slug") or _norm(brand_name))

        products: dict[str, dict] = {}
        for pid, slug in candidates:
            if pid in products:
                continue
            try:
                detail = self._get(f"{_API_BASE}/products/{pid}").json()
            except Exception as exc:
                # Stale sitemap entry (discontinued product) → skip, don't abort discovery.
                print(f"  [sensation] skip {pid} — detail fetch failed: {exc}", flush=True)
                continue
            # Confirm the candidate really belongs to this brand (drops slug-prefix
            # collisions and any unrelated product that happens to share the prefix).
            if str(detail.get("brandId")) != brand_id:
                continue
            products[pid] = {
                "external_id": pid,
                "name": detail.get("title") or detail.get("erpTitle") or slug,
                "source_url": f"{_SITE_BASE}/{slug}",
            }

        return list(products.values())

    # ── review scraping ───────────────────────────────────────────────────────────

    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        """Fetch a product's reviews. The endpoint returns the entire list in one call
        (no pagination), newest-first, so we early-stop once a review predates ``since``.
        """
        pid = product["external_id"]
        resp = self._get(f"{_API_BASE}/products/{pid}/reviews")
        reviews = (resp.json() or {}).get("reviews") or []

        for raw in reviews:
            review = ReviewNormalizer.from_sensation(raw)
            if self._past_cutoff(review.review_date, since):
                break
            yield review
