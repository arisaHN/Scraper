import os

# Re-exported so existing imports (`from src.scrapers.ditano import _parse_review_widget`)
# keep working after the shared logic moved to shopify_judgeme.
from .shopify_judgeme import ShopifyJudgemeScraper, _parse_review_widget  # noqa: F401


class DitanoScraper(ShopifyJudgemeScraper):
    """ditano.com — Shopify storefront + Judge.me reviews. products.json is served from the
    storefront itself, and Shopify ``product_type`` holds real category labels."""

    site_name = "ditano"
    products_base = "https://ditano.com"
    storefront_base = "https://ditano.com"
    shop_domain = os.environ.get("DITANO_SHOP_DOMAIN", "ditano.myshopify.com")
    category_from_product_type = True
