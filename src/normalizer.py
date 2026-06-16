from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class NormalizedReview:
    external_review_id: str
    source_site: str
    author: Optional[str]
    rating: Optional[float]
    title: Optional[str]
    text: Optional[str]
    review_date: Optional[datetime]
    helpful_count: int = 0
    verified: bool = False


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    from dateutil import parser as dtparser
    try:
        return dtparser.parse(value)
    except Exception:
        return None


class ReviewNormalizer:
    @staticmethod
    def from_bazaarvoice(raw: dict) -> NormalizedReview:
        return NormalizedReview(
            external_review_id=raw.get("Id", ""),
            source_site="bazaarvoice",
            author=raw.get("UserNickname") or "Anonymous",
            rating=float(raw["Rating"]) if raw.get("Rating") is not None else None,
            title=raw.get("Title"),
            text=raw.get("ReviewText"),
            review_date=_parse_dt(raw.get("SubmissionTime")),
            helpful_count=raw.get("TotalPositiveFeedbackCount", 0),
            verified=bool(raw.get("BadgesOrder")),
        )

    @staticmethod
    def from_sephora(raw: dict) -> NormalizedReview:
        return NormalizedReview(
            external_review_id=raw["id"],
            source_site="sephora",
            author=raw.get("author"),
            rating=float(raw["rating"]) if raw.get("rating") is not None else None,
            title=raw.get("title"),
            text=raw.get("text"),
            review_date=raw.get("parsed_date") or _parse_dt(raw.get("date")),
            verified=raw.get("verified", False),
        )

