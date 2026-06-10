import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    DATABASE_URL: str = ""
    BV_PASSKEY_DOUGLAS: str = ""
    GOOGLE_PLACES_KEY: str = ""
    SCRAPE_DELAY_MIN: float = 0.5
    SCRAPE_DELAY_MAX: float = 2.0

    def __post_init__(self):
        self.DATABASE_URL = os.environ.get("DATABASE_URL", "")
        self.BV_PASSKEY_DOUGLAS = os.environ.get(
            "BV_PASSKEY_DOUGLAS", "caStwhUd1zHx4z11vgXT7I53nTyXTngmVpOJ95WGztsKI"
        )
        self.BV_LOCALE = os.environ.get("BV_LOCALE", "en_US")
        self.BV_RETAILER_DOUGLAS = os.environ.get("BV_RETAILER_DOUGLAS", "douglas")
        self.GOOGLE_PLACES_KEY = os.environ.get("GOOGLE_PLACES_KEY", "")
        self.SCRAPE_DELAY_MIN = float(os.environ.get("SCRAPE_DELAY_MIN", "0.5"))
        self.SCRAPE_DELAY_MAX = float(os.environ.get("SCRAPE_DELAY_MAX", "2.0"))


settings = Settings()
