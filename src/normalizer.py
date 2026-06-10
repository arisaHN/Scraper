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
    def from_trustpilot(raw: dict) -> NormalizedReview:
        rating = raw.get("rating")
        verified = (
            raw.get("labels", {})
            .get("verification", {})
            .get("isVerified", False)
        )
        return NormalizedReview(
            external_review_id=raw.get("id", ""),
            source_site="trustpilot",
            author=raw.get("consumer", {}).get("displayName"),
            rating=float(rating) if rating is not None else None,
            title=raw.get("title"),
            text=raw.get("text"),
            review_date=_parse_dt(raw.get("dates", {}).get("publishedDate")),
            helpful_count=raw.get("likes", 0),
            verified=bool(verified),
        )

    @staticmethod
    def from_amazon(raw: dict) -> NormalizedReview:
        return NormalizedReview(
            external_review_id=raw.get("review_id", ""),
            source_site="amazon",
            author=raw.get("author"),
            rating=raw.get("rating"),
            title=raw.get("title"),
            text=raw.get("text"),
            review_date=_parse_dt(raw.get("date")),
            helpful_count=raw.get("helpful_count", 0),
            verified=raw.get("verified_purchase", False),
        )

    @staticmethod
    def from_google(raw: dict) -> NormalizedReview:
        ts = raw.get("time")
        review_date = datetime.utcfromtimestamp(ts) if ts and str(ts).isdigit() else _parse_dt(str(ts) if ts else None)
        return NormalizedReview(
            external_review_id=str(raw.get("time", "")),
            source_site="google",
            author=raw.get("author_name"),
            rating=float(raw.get("rating", 0)) if raw.get("rating") else None,
            title=None,
            text=raw.get("text"),
            review_date=review_date,
            helpful_count=0,
            verified=False,
        )
