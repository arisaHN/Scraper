import os

from .shopify_judgeme import ShopifyJudgemeScraper

# Public Algolia search credentials (search-only key, shipped in the storefront JS).
# Override via env if Pinalli rotates them. The index keys products by Shopify `id`
# (== products.json id == Judge.me product_id), so discovery and review-fetch line up.
_ALGOLIA_APP = os.environ.get("PINALLI_ALGOLIA_APP", "VN9XEZ6ACP")
_ALGOLIA_KEY = os.environ.get("PINALLI_ALGOLIA_KEY", "b6a4d7f7613954e5f8addb8033055e4b")
_ALGOLIA_INDEX = os.environ.get("PINALLI_ALGOLIA_INDEX", "headless_products")
# Largest vendor on the store is well under Algolia's default 1000-result pagination cap,
# so one page fetches an entire brand.
_ALGOLIA_HITS_PER_PAGE = 1000


class PinalliScraper(ShopifyJudgemeScraper):
    """pinalli.it — a headless Shopify store (Next.js frontend) + Judge.me reviews.

    **Reviews** are identical to ditano (Judge.me widget, inherited from the base), keyed by
    the backend myshopify domain `pinalli-headless-prod.myshopify.com`.

    **Discovery unions two sources**, because neither alone is complete (for DIOR:
    products.json finds 702, Algolia 342, overlapping by only ~186 → union ~858; both include
    out-of-stock products):
      - products.json (inherited) reaches at most 25k products (Shopify's 100-page cap), so it
        misses this ~38k store's tail, but catches many products the storefront search index
        doesn't list.
      - Algolia (the index powering the storefront's brand pages) has no page cap and filters
        server-side by `vendor`, catching products beyond products.json's 25k window.
    The two are merged by Shopify product id (Algolia `id` == products.json id == Judge.me
    product_id). `product_type` is SKU/barcode junk here, so `category` is left null.
    """

    site_name = "pinalli"
    products_base = os.environ.get(
        "PINALLI_PRODUCTS_BASE", "https://pinalli-headless-prod.myshopify.com"
    )
    storefront_base = "https://www.pinalli.it"
    shop_domain = os.environ.get("PINALLI_SHOP_DOMAIN", "pinalli-headless-prod.myshopify.com")

    def discover_products(self, brand_name: str) -> list[dict]:
        """Union of products.json discovery (inherited) and Algolia discovery, deduped by
        Shopify product id. products.json records win on collision (richer data)."""
        products = self._discover_via_products_json(brand_name)
        for hit in self._algolia_vendor_hits(brand_name):
            pid = hit.get("id")
            if pid is None or str(pid) in products:
                continue
            handle = hit.get("handle")
            products[str(pid)] = {
                "external_id": str(pid),
                "name": hit.get("title") or str(pid),
                "source_url": f"{self.storefront_base}/products/{handle}" if handle else self.storefront_base,
                "category": None,
            }
        return list(products.values())

    def _algolia_vendor_hits(self, brand_name: str) -> list[dict]:
        """Fetch all Algolia hits for a vendor (case-insensitive facet filter), paged by
        Algolia's reported nbPages (one page suffices for every vendor on this store)."""
        url = f"https://{_ALGOLIA_APP}-dsn.algolia.net/1/indexes/{_ALGOLIA_INDEX}/query"
        headers = {
            "X-Algolia-API-Key": _ALGOLIA_KEY,
            "X-Algolia-Application-Id": _ALGOLIA_APP,
            "Content-Type": "application/json",
        }
        hits: list[dict] = []
        page = 0
        while True:
            body = {
                "params": f"query=&hitsPerPage={_ALGOLIA_HITS_PER_PAGE}&page={page}",
                "facetFilters": [[f"vendor:{brand_name}"]],
            }
            resp = self.session.post(url, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            hits.extend(data.get("hits") or [])
            if page + 1 >= (data.get("nbPages") or 1):
                break
            page += 1
            self._polite_delay()
        return hits
