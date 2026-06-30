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
        """Parse a review object from sephora.it's Next.js Server Action ("getReviews")."""
        # Field values the RSC payload couldn't serialize (e.g. user opted not to share
        # gender) come through as the literal string "$undefined" rather than being omitted.
        def clean(value):
            return None if value in (None, "$undefined") else value

        created_at = clean(raw.get("createdAt"))
        if isinstance(created_at, str) and created_at.startswith("$D"):
            created_at = created_at[2:]  # RSC date-type marker prefix

        vote = raw.get("vote") or {}
        return NormalizedReview(
            external_review_id=str(raw["id"]),
            source_site="sephora",
            author=clean(raw.get("userName")) or "Anonymous",
            rating=float(raw["rating"]) if raw.get("rating") is not None else None,
            title=clean(raw.get("title")),
            text=clean(raw.get("content")),
            review_date=_parse_dt(created_at),
            helpful_count=vote.get("like", 0) or 0,
            verified=raw.get("purchaserType") == "BUYER",
        )

    @staticmethod
    def from_marionnaud(raw: dict) -> NormalizedReview:
        """Parse a review object from marionnaud.it's PowerReviews display API."""
        details = raw.get("details") or {}
        metrics = raw.get("metrics") or {}
        badges = raw.get("badges") or {}
        created_date = details.get("created_date")
        return NormalizedReview(
            external_review_id=str(raw["review_id"]),
            source_site="marionnaud",
            author=details.get("nickname") or "Anonymous",
            rating=float(metrics["rating"]) if metrics.get("rating") is not None else None,
            title=details.get("headline"),
            text=details.get("comments"),
            review_date=datetime.utcfromtimestamp(created_date / 1000) if created_date else None,
            helpful_count=metrics.get("helpful_votes", 0) or 0,
            verified=bool(badges.get("is_verified_buyer")),
        )

    @staticmethod
    def from_sensation(raw: dict) -> NormalizedReview:
        """Parse a review object from sensationprofumerie.it's /api/products/{id}/reviews.

        Reviews are syndicated from third-party aggregators (``provider`` is "trustpilot"
        or "feedaty"); there's no title field and no per-review verified-purchase flag, so
        ``verified`` is left False.
        """
        return NormalizedReview(
            external_review_id=str(raw["reviewId"]),
            source_site="sensation",
            author=raw.get("authorName") or "Anonymous",
            rating=float(raw["rating"]) if raw.get("rating") is not None else None,
            title=None,
            text=raw.get("text"),
            review_date=_parse_dt(raw.get("creationDate")),
            helpful_count=0,
            verified=False,
        )

    @staticmethod
    def from_judgeme(raw: dict, source_site: str) -> NormalizedReview:
        """Parse a review dict extracted from a Judge.me review-widget HTML element.

        Shared by every Shopify+Judge.me store (ditano, pinalli, …); the caller passes its
        own ``source_site`` since the same widget format backs all of them.
        """
        return NormalizedReview(
            external_review_id=str(raw["review_id"]),
            source_site=source_site,
            author=raw.get("author") or "Anonymous",
            rating=float(raw["score"]) if raw.get("score") not in (None, "") else None,
            title=raw.get("title") or None,
            text=raw.get("body") or None,
            review_date=_parse_dt(raw.get("timestamp")),
            helpful_count=int(raw.get("thumb_up") or 0),
            verified=bool(raw.get("verified")),
        )

    @staticmethod
    def from_ditano(raw: dict) -> NormalizedReview:
        """Parse a review dict from ditano.com's Judge.me widget (see from_judgeme)."""
        return ReviewNormalizer.from_judgeme(raw, "ditano")

    @staticmethod
    def from_notino(raw: dict) -> NormalizedReview:
        """Parse a review object from notino.it's getReviews GraphQL response."""
        return NormalizedReview(
            external_review_id=str(raw["id"]),
            source_site="notino",
            author=raw.get("userName") or "Anonymous",
            rating=float(raw["score"]) if raw.get("score") is not None else None,
            title=raw.get("title"),
            text=raw.get("text"),
            review_date=_parse_dt(raw.get("createdDate")),
            helpful_count=raw.get("like", 0) or 0,
            verified=raw.get("authorType") == "Verified",
        )

