"""
Unit tests for BazaarvoiceScraper._dedupe_by_family — pure logic, no network access.

Run with:
    .venv/bin/python -m pytest tests/test_bazaarvoice_dedupe.py -v
"""
from src.scrapers.bazaarvoice import BazaarvoiceScraper


def _row(external_id, family_id, review_count, source_url=""):
    return {
        "name": external_id,
        "source_url": source_url,
        "external_id": external_id,
        "category": None,
        "_review_count": review_count,
        "_family_id": family_id,
    }


def test_collapses_family_to_single_representative():
    rows = [
        _row("SHADE_A", "FAM1", 5),
        _row("SHADE_B", "FAM1", 3),
        _row("SHADE_C", "FAM1", 1200, source_url="https://example.com/c"),
    ]
    result = BazaarvoiceScraper._dedupe_by_family(rows)
    assert [r["external_id"] for r in result] == ["SHADE_C"]


def test_prefers_real_url_over_higher_review_count_without_one():
    rows = [
        _row("SHADE_A", "FAM1", 900),
        _row("SHADE_B", "FAM1", 10, source_url="https://example.com/b"),
    ]
    result = BazaarvoiceScraper._dedupe_by_family(rows)
    assert [r["external_id"] for r in result] == ["SHADE_B"]


def test_products_without_family_id_never_collide():
    rows = [
        _row("STANDALONE_A", None, 5),
        _row("STANDALONE_B", None, 5),
    ]
    result = BazaarvoiceScraper._dedupe_by_family(rows)
    assert {r["external_id"] for r in result} == {"STANDALONE_A", "STANDALONE_B"}


def test_distinct_families_are_both_kept():
    rows = [
        _row("EDP", "FAM_EDP", 2076, source_url="https://example.com/edp"),
        _row("EDT", "FAM_EDT", 4206, source_url="https://example.com/edt"),
    ]
    result = BazaarvoiceScraper._dedupe_by_family(rows)
    assert {r["external_id"] for r in result} == {"EDP", "EDT"}


def test_discover_products_strips_internal_keys():
    from unittest.mock import MagicMock

    scraper = BazaarvoiceScraper(passkey="fake", dedupe_family_variants=True)
    page1 = {
        "TotalResults": 2,
        "Results": [
            {
                "Id": "SHADE_A",
                "Name": "Lipstick A",
                "ProductPageUrl": "",
                "CategoryId": "",
                "FamilyIds": ["FAM1"],
                "ReviewStatistics": {"TotalReviewCount": 5},
            },
            {
                "Id": "SHADE_B",
                "Name": "Lipstick B",
                "ProductPageUrl": "https://example.com/b",
                "CategoryId": "",
                "FamilyIds": ["FAM1"],
                "ReviewStatistics": {"TotalReviewCount": 900},
            },
        ],
    }
    scraper._get = MagicMock(return_value=MagicMock(json=lambda: page1))

    results = scraper.discover_products("Dior")
    assert len(results) == 1
    assert results[0]["external_id"] == "SHADE_B"
    assert set(results[0].keys()) == {"name", "source_url", "external_id", "category"}
