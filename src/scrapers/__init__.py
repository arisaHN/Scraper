import os

from dotenv import load_dotenv

from .bazaarvoice import BazaarvoiceScraper

load_dotenv()

SCRAPER_REGISTRY: dict[str, dict] = {}

for _key, _passkey in os.environ.items():
    if _key.startswith("BV_PASSKEY_") and _passkey:
        _retailer = _key[len("BV_PASSKEY_"):].lower()
        _locale = os.environ.get(f"BV_LOCALE_{_retailer.upper()}", os.environ.get("BV_LOCALE", "en_US"))
        SCRAPER_REGISTRY[f"bazaarvoice_{_retailer}"] = {
            "class": BazaarvoiceScraper,
            "source_site": "bazaarvoice",
            "kwargs": {"passkey": _passkey, "locale": _locale},
            "retailer": _retailer,
        }

if os.environ.get("SEPHORA_ENABLED", "").lower() in ("1", "true"):
    from .sephora_html import SephoraHTMLScraper
    SCRAPER_REGISTRY["sephora"] = {
        "class": SephoraHTMLScraper,
        "source_site": "sephora",
        "kwargs": {},
        "retailer": "sephora",
    }
