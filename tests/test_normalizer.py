"""
Unit tests for ReviewNormalizer.from_bazaarvoice — pure parsing logic, no network access,
so these run without BV_PASSKEY_DOUGLAS or any live credentials.

Run with:
    .venv/bin/python -m pytest tests/test_normalizer.py -v
"""
from datetime import datetime, timezone

from src.normalizer import ReviewNormalizer

# Trimmed copy of a real Bazaarvoice review object (irrelevant fields omitted).
RAW_REVIEW = {
    "Id": "1221886851",
    "UserNickname": "Gire",
    "Rating": 5,
    "Title": "Il migliore da tutti i giorni",
    "ReviewText": "Ottimo profumo da uomo per ogni occasione, persistente",
    "SubmissionTime": "2026-06-12T21:23:13.000Z",
    "TotalPositiveFeedbackCount": 3,
    "BadgesOrder": ["VerifiedPurchaser"],
}


def test_from_bazaarvoice_parses_all_fields():
    review = ReviewNormalizer.from_bazaarvoice(RAW_REVIEW)

    assert review.external_review_id == "1221886851"
    assert review.source_site == "bazaarvoice"
    assert review.author == "Gire"
    assert review.rating == 5.0
    assert review.title == "Il migliore da tutti i giorni"
    assert review.text == "Ottimo profumo da uomo per ogni occasione, persistente"
    assert review.review_date == datetime(2026, 6, 12, 21, 23, 13, tzinfo=timezone.utc)
    assert review.helpful_count == 3
    assert review.verified is True


def test_from_bazaarvoice_defaults_for_missing_optional_fields():
    raw = {
        "Id": "999",
        "SubmissionTime": "2026-01-01T00:00:00.000Z",
    }
    review = ReviewNormalizer.from_bazaarvoice(raw)

    assert review.author == "Anonymous"
    assert review.rating is None
    assert review.title is None
    assert review.text is None
    assert review.helpful_count == 0
    assert review.verified is False
