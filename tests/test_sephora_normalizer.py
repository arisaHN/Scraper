"""
Unit tests for Sephora's parsing logic — pure functions, no network access, runs
without any credentials.

Run with:
    .venv/bin/python -m pytest tests/test_sephora_normalizer.py -v
"""
import json
from datetime import datetime
from urllib.parse import unquote

from src.normalizer import ReviewNormalizer
from src.scrapers.sephora_html import _parse_rsc, _router_state_tree, _safe_from_sephora

RAW_REVIEW = {
    "id": 555,
    "userName": "Giulia",
    "rating": 4,
    "title": "Bellissimo",
    "content": "Profumo molto duraturo, lo consiglio.",
    "createdAt": "$D2026-05-01T10:00:00.000Z",
    "purchaserType": "BUYER",
    "vote": {"like": 7},
}


def test_from_sephora_parses_all_fields():
    review = ReviewNormalizer.from_sephora(RAW_REVIEW)

    assert review.external_review_id == "555"
    assert review.source_site == "sephora"
    assert review.author == "Giulia"
    assert review.rating == 4.0
    assert review.title == "Bellissimo"
    assert review.text == "Profumo molto duraturo, lo consiglio."
    assert review.review_date == datetime(2026, 5, 1, 10, 0, 0, tzinfo=review.review_date.tzinfo)
    assert review.helpful_count == 7
    assert review.verified is True


def test_from_sephora_treats_undefined_sentinel_as_none():
    raw = dict(RAW_REVIEW, userName="$undefined", title="$undefined")
    review = ReviewNormalizer.from_sephora(raw)

    assert review.author == "Anonymous"
    assert review.title is None


def test_from_sephora_non_buyer_is_not_verified():
    raw = dict(RAW_REVIEW, purchaserType="GUEST")
    review = ReviewNormalizer.from_sephora(raw)

    assert review.verified is False


def test_safe_from_sephora_skips_malformed_review():
    assert _safe_from_sephora({"userName": "missing id field"}) is None


def test_safe_from_sephora_returns_normalized_review_when_valid():
    review = _safe_from_sephora(RAW_REVIEW)
    assert review is not None
    assert review.external_review_id == "555"


def test_router_state_tree_contains_slug_and_locale():
    encoded = _router_state_tree("some-product-P1234567")
    tree = json.loads(unquote(encoded))

    # ["", {"children": [["locale", "it-IT", "d", None], {...}], ...}, None, None, 0]
    assert tree[1]["children"][0] == ["locale", "it-IT", "d", None]
    serialized = json.dumps(tree)
    assert "some-product-P1234567" in serialized


def test_parse_rsc_extracts_reviews_payload():
    text = '0:{"data":{"reviewCount":2,"reviews":[{"id":1},{"id":2}]}}\n'
    data = _parse_rsc(text)

    assert data is not None
    assert data["reviewCount"] == 2
    assert len(data["reviews"]) == 2


def test_parse_rsc_handles_zero_review_product_without_reviews_key():
    text = '0:{"data":{"reviewCount":0}}\n'
    data = _parse_rsc(text)

    assert data is not None
    assert data["reviewCount"] == 0
    assert data["reviews"] == []


def test_parse_rsc_returns_none_for_unrecognized_payload():
    text = "<html><body>Access Denied</body></html>"
    assert _parse_rsc(text) is None
