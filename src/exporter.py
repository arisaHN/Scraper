import csv
import json
from datetime import datetime
from typing import Optional

from .database import get_session
from .models import Brand, Product, Review

# Maps granular category labels → broad group. Derived at export time so no
# extra DB column or migration is needed. Add new labels here when new
# retailer category maps are added in src/scrapers/__init__.py.
_CATEGORY_GROUP: dict[str, str] = {
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
}


def _category_group(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    return _CATEGORY_GROUP.get(category)


def _normalize_whitespace(s: str) -> str:
    import unicodedata
    return " ".join(unicodedata.normalize("NFKC", s).split())


def export_brand(
    brand_name: str,
    fmt: str = "csv",
    output_path: str = None,
    product_filter: str = None,
    product_id_filter: int = None,
) -> str:
    with get_session() as session:
        brand = session.query(Brand).filter_by(name=brand_name).first()
        if not brand:
            raise ValueError(f"Brand '{brand_name}' not found.")
        query = (
            session.query(Review, Product.id, Product.name, Product.source_url, Product.retailer, Product.category)
            .join(Product, Review.product_id == Product.id)
            .filter(Product.brand_id == brand.id)
        )
        if product_id_filter is not None:
            query = query.filter(Product.id == product_id_filter)
        elif product_filter:
            normalized = _normalize_whitespace(product_filter)
            query = query.filter(Product.name.ilike(f"%{normalized}%"))
        rows = query.order_by(Review.review_date.desc().nullslast()).all()
        data = [
            {
                "id": r.id,
                "source_site": r.source_site,
                "retailer": retailer,
                "product_id": product_id,
                "product_name": product_name,
                "product_url": product_url,
                "product_category": product_category,
                "product_category_group": _category_group(product_category),
                "external_review_id": r.external_review_id,
                "author": r.author,
                "rating": r.rating,
                "title": r.title,
                "text": r.text,
                "review_date": r.review_date.isoformat() if r.review_date else None,
                "helpful_count": r.helpful_count,
                "verified": r.verified,
                "scraped_at": r.scraped_at.isoformat(),
            }
            for r, product_id, product_name, product_url, retailer, product_category in rows
        ]

    if not output_path:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = f"{brand_name.lower().replace(' ', '_')}_{ts}.{fmt}"

    if fmt == "csv":
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            if data:
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    return output_path
