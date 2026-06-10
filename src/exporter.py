import csv
import json
from datetime import datetime

from .database import get_session
from .models import Brand, Product, Review


def export_brand(brand_name: str, fmt: str = "csv", output_path: str = None) -> str:
    with get_session() as session:
        brand = session.query(Brand).filter_by(name=brand_name).first()
        if not brand:
            raise ValueError(f"Brand '{brand_name}' not found.")
        rows = (
            session.query(Review, Product.name, Product.source_url)
            .join(Product, Review.product_id == Product.id)
            .filter(Product.brand_id == brand.id)
            .order_by(Review.review_date.desc().nullslast())
            .all()
        )
        data = [
            {
                "id": r.id,
                "source_site": r.source_site,
                "product_name": product_name,
                "product_url": product_url,
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
            for r, product_name, product_url in rows
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
