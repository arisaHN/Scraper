from typing import Optional

# Maps granular category labels → broad group.
# Update this when new retailer category maps are added in src/scrapers/__init__.py.
CATEGORY_GROUP: dict[str, str] = {
    # Fragrance
    "Women's Fragrance": "Fragrance",
    "Men's Fragrance": "Fragrance",
    "Fragrance Gift Set": "Fragrance",
    "Hair Fragrance": "Fragrance",
    # Makeup
    "Foundation & Concealer": "Makeup",
    "Lipstick": "Makeup",
    "Eye Makeup & Mascara": "Makeup",
    "Nail Polish": "Makeup",
    "Eyebrow": "Makeup",
    "Eye Palette": "Makeup",
    "Bronzer & Highlighter": "Makeup",
    "Makeup Brushes": "Makeup",
    "Makeup Accessories": "Makeup",
    "Lip Care": "Makeup",
    # Skincare
    "Moisturizer": "Skincare",
    "Serum": "Skincare",
    "Cleanser": "Skincare",
    "Face Mask": "Skincare",
    "Eye & Lip Skincare": "Skincare",
    "Self-Tanner": "Skincare",
    "Skincare": "Skincare",
    "Skincare Gift Set": "Skincare",
    "Men's Skincare": "Skincare",
    # Body Care
    "Shower Gel": "Body Care",
    "Body Lotion & Oil": "Body Care",
    "Hand Cream": "Body Care",
    "Shaving": "Body Care",
    "Sunscreen": "Body Care",
    "Suncare": "Body Care",
    "Body Fragrance Mist": "Body Care",
    # Haircare
    "Haircare": "Haircare",
    # ditano.com (Shopify product_type, Italian) — store's own top-level categories
    "Fragranze": "Fragrance",
    "Fragranze di nicchia": "Fragrance",
    "Makeup": "Makeup",
    "Hair": "Haircare",
    "Solari": "Body Care",
    # sephora.it (hub-page category tab labels, Italian) — verified against Dior's tabs;
    # other brands may surface additional tabs not yet seen, which fall back to None here
    # until added.
    "Make-up": "Makeup",
    "Trattamenti Viso": "Skincare",
    "Profumi": "Fragrance",
    "Capelli": "Haircare",
    "Corpo & Bagno": "Body Care",
}


def category_group(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    return CATEGORY_GROUP.get(category)
