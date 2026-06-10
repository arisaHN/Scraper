from .bazaarvoice import BazaarvoiceScraper
from .trustpilot import TrustpilotScraper
from .amazon import AmazonScraper
from .google_reviews import GoogleReviewsScraper

SCRAPER_REGISTRY = {
    "bazaarvoice": BazaarvoiceScraper,
    "trustpilot": TrustpilotScraper,
    "amazon": AmazonScraper,
    "google": GoogleReviewsScraper,
}
