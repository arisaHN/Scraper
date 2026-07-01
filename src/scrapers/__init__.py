import os

from dotenv import load_dotenv

from .bazaarvoice import BazaarvoiceScraper

load_dotenv()

# Douglas-specific 4-digit CategoryId prefix → human-readable product category.
# Codes come from Douglas's Bazaarvoice catalog; the 4-digit prefix identifies the
# subcategory, and the last 2 digits are shade/variant-level specificity.
_DOUGLAS_CATEGORY_MAP: dict[str, str] = {
    # Fragrance
    "0101": "Women's Fragrance",
    "0102": "Men's Fragrance",
    "0103": "Fragrance Gift Set",
    "2402": "Hair Fragrance",
    # Makeup — face
    "0301": "Foundation & Concealer",
    "0320": "Bronzer & Highlighter",
    "0307": "Makeup Brushes",
    "0308": "Makeup Accessories",
    # Makeup — lips
    "0302": "Lipstick",
    "1208": "Lip Care",
    # Makeup — eyes
    "0303": "Eye Makeup & Mascara",
    "0309": "Eyebrow",
    "0312": "Eye Palette",
    # Makeup — nails
    "0304": "Nail Polish",
    # Skincare — face
    "0201": "Moisturizer",
    "0215": "Skincare Gift Set",
    "1201": "Cleanser",
    "1202": "Serum",
    "1203": "Face Mask",
    "1204": "Eye & Lip Skincare",
    "1205": "Moisturizer",
    "1209": "Self-Tanner",
    "1212": "Skincare",
    "1213": "Skincare Gift Set",
    "6501": "Skincare Gift Set",
    # Skincare — men's
    "0205": "Men's Skincare",
    "1210": "Men's Skincare",
    # Body care
    "0202": "Body Fragrance Mist",
    "0204": "Suncare",
    "1301": "Shower Gel",
    "1302": "Body Lotion & Oil",
    "1303": "Shaving",
    "1305": "Hand Cream",
    "1308": "Sunscreen",
    # Hair
    "1401": "Haircare",
}

# Per-retailer category maps — add new retailers here when their BV catalog codes
# are known. Retailers with no entry get no category mapping (category stays as
# the raw CategoryId string, or NULL if BV didn't return one).
_BV_CATEGORY_MAPS: dict[str, dict] = {
    "douglas": _DOUGLAS_CATEGORY_MAP,
}

SCRAPER_REGISTRY: dict[str, dict] = {}

for _key, _passkey in os.environ.items():
    if _key.startswith("BV_PASSKEY_") and _passkey:
        _retailer = _key[len("BV_PASSKEY_"):].lower()
        _locale = os.environ.get(f"BV_LOCALE_{_retailer.upper()}", os.environ.get("BV_LOCALE", "en_US"))
        _cat_map = _BV_CATEGORY_MAPS.get(_retailer, {})
        SCRAPER_REGISTRY[f"bazaarvoice_{_retailer}"] = {
            "class": BazaarvoiceScraper,
            "source_site": "bazaarvoice",
            "kwargs": {"passkey": _passkey, "locale": _locale, "category_map": _cat_map},
            "retailer": _retailer,
        }

if os.environ.get("SEPHORA_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .sephora_html import SephoraHTMLScraper
    SCRAPER_REGISTRY["sephora"] = {
        "class": SephoraHTMLScraper,
        "source_site": "sephora",
        "kwargs": {},
        "retailer": "sephora",
    }

if os.environ.get("NOTINO_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .notino import NotinoScraper
    SCRAPER_REGISTRY["notino"] = {
        "class": NotinoScraper,
        "source_site": "notino",
        "kwargs": {},
        "retailer": "notino",
    }

if os.environ.get("MARIONNAUD_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .marionnaud import MarionnaudScraper
    SCRAPER_REGISTRY["marionnaud"] = {
        "class": MarionnaudScraper,
        "source_site": "marionnaud",
        "kwargs": {},
        "retailer": "marionnaud",
    }

if os.environ.get("SENSATION_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .sensation import SensationScraper
    SCRAPER_REGISTRY["sensation"] = {
        "class": SensationScraper,
        "source_site": "sensation",
        "kwargs": {},
        "retailer": "sensation",
    }

if os.environ.get("DITANO_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .ditano import DitanoScraper
    SCRAPER_REGISTRY["ditano"] = {
        "class": DitanoScraper,
        "source_site": "ditano",
        "kwargs": {},
        "retailer": "ditano",
    }

if os.environ.get("PINALLI_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .pinalli import PinalliScraper
    SCRAPER_REGISTRY["pinalli"] = {
        "class": PinalliScraper,
        "source_site": "pinalli",
        "kwargs": {},
        "retailer": "pinalli",
    }

if os.environ.get("PRIMOR_ENABLED", "").lower() in ("1", "true", "yes", "on"):
    from .primor import PrimorScraper
    SCRAPER_REGISTRY["primor"] = {
        "class": PrimorScraper,
        "source_site": "primor",
        "kwargs": {},
        "retailer": "primor",
    }
