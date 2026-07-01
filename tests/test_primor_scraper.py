"""
Tests for PrimorScraper (it.primor.eu).

The unit tests for review-URL construction and the normalizer run with no network. The
live integration tests hit the real it.primor.eu site and reviews.primor.eu CDN, and are
skipped unless PRIMOR_ENABLED is set.

Run the live tests with:
    PRIMOR_ENABLED=1 .venv/bin/python -m pytest tests/test_primor_scraper.py -v
"""
import os

import pytest

PRIMOR_ENABLED = os.environ.get("PRIMOR_ENABLED", "").lower() in ("1", "true", "yes", "on")

# Dior Capture Totale Hyalushot correttore — has several reviews, needed for a
# meaningful since-cutoff test (most primor.eu products have 0-1 reviews).
PRODUCT = {
    "external_id": "92601",
    "name": "Correttore Antirughe Capture Totale Hyalushot (Primor)",
    "source_url": "https://it.primor.eu/it_it/dior-capture-totale-hyalushot-correttore-per-le-rughe-esistenti-e-i-primi-segni-delle-rughe-92601.html",
    "category": "Cosmetici di lusso > Antirughe e Antietà di lusso",
}


# ── pure unit tests (no network) ──────────────────────────────────────────────────


def test_reviews_url_construction():
    from src.scrapers.primor import PrimorScraper

    assert (
        PrimorScraper._reviews_url("0TF14305")
        == "https://reviews.primor.eu/it/0/T/F/1/4/3/0TF14305_reviews.html"
    )


def test_from_primor_parses_fields():
    from src.normalizer import ReviewNormalizer

    raw = {
        "rating": "4",
        "nombre": "Beatriz R",
        "comentario": "Ottimo prodotto",
        "fecha": "2026-03-26",
        "origin": "1",
        "country": "es",
    }
    r = ReviewNormalizer.from_primor(raw, "0TF14305")
    assert r.source_site == "primor"
    assert r.author == "Beatriz R"
    assert r.rating == 4.0
    assert r.title is None
    assert r.text == "Ottimo prodotto"
    assert r.review_date is not None and r.review_date.year == 2026
    assert r.verified is False
    assert r.external_review_id  # non-empty synthesized hash


def test_from_primor_id_is_stable_and_distinguishes_reviews():
    from src.normalizer import ReviewNormalizer

    raw = {"rating": "5", "nombre": "Mario", "comentario": "Ottimo", "fecha": "2026-01-01"}
    r1 = ReviewNormalizer.from_primor(raw, "0TF14305")
    r2 = ReviewNormalizer.from_primor(raw, "0TF14305")
    assert r1.external_review_id == r2.external_review_id

    other = ReviewNormalizer.from_primor(
        {"rating": "5", "nombre": "Luigi", "comentario": "Ottimo", "fecha": "2026-01-01"},
        "0TF14305",
    )
    assert other.external_review_id != r1.external_review_id


def test_from_primor_handles_empty_comment():
    from src.normalizer import ReviewNormalizer

    r = ReviewNormalizer.from_primor(
        {"rating": "4", "nombre": "", "comentario": "", "fecha": None}, "0TF14305"
    )
    assert r.author == "Anonymous"
    assert r.text is None
    assert r.review_date is None


# ── live integration tests ────────────────────────────────────────────────────────


@pytest.mark.skipif(not PRIMOR_ENABLED, reason="PRIMOR_ENABLED not set")
class TestPrimorLive:

    def test_discover_products(self):
        from src.scrapers.primor import PrimorScraper

        scraper = PrimorScraper()
        products = scraper.discover_products("Dior")
        assert len(products) >= 20, f"Expected many Dior products, got {len(products)}"
        assert all(p["external_id"].isdigit() for p in products)
        assert all(p["source_url"].startswith("https://it.primor.eu/") for p in products)

    def test_discover_products_handles_inconsistent_slugs(self):
        """Armani slugs come in two forms on this site ('giorgio-armani-giorgio-armani-...'
        and 'armani-...'), so discovery must use substring matching, not prefix matching."""
        from src.scrapers.primor import PrimorScraper

        scraper = PrimorScraper()
        products = scraper.discover_products("Armani")
        assert len(products) >= 5, f"Expected several Armani products, got {len(products)}"

    def test_scrape_reviews_returns_reviews(self):
        from src.scrapers.primor import PrimorScraper

        scraper = PrimorScraper()
        reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(reviews) >= 1, f"Expected at least 1 review, got {len(reviews)}"
        assert all(r.source_site == "primor" for r in reviews)
        assert all(r.external_review_id for r in reviews)

    def test_since_cutoff(self):
        """Self-deriving cutoff: pick the midpoint review date, re-fetch with since=,
        assert the returned set matches the expected recent subset."""
        from src.scrapers.primor import PrimorScraper

        scraper = PrimorScraper()
        all_reviews = list(scraper.scrape_reviews(PRODUCT, since=None))
        assert len(all_reviews) > 1, "Need at least 2 reviews to pick a meaningful cutoff."

        cutoff = all_reviews[len(all_reviews) // 2].review_date
        expected = [r for r in all_reviews if r.review_date >= cutoff]
        assert expected, "Cutoff produced no expected reviews — pick a different index."

        recent = list(scraper.scrape_reviews(PRODUCT, since=cutoff))
        assert {r.external_review_id for r in recent} == {
            r.external_review_id for r in expected
        }
