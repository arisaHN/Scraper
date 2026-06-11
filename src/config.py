import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    DATABASE_URL: str = ""
    SCRAPE_DELAY_MIN: float = 0.5
    SCRAPE_DELAY_MAX: float = 2.0

    def __post_init__(self):
        self.DATABASE_URL = os.environ.get("DATABASE_URL", "")
        self.SCRAPE_DELAY_MIN = float(os.environ.get("SCRAPE_DELAY_MIN", "0.5"))
        self.SCRAPE_DELAY_MAX = float(os.environ.get("SCRAPE_DELAY_MAX", "2.0"))


settings = Settings()
