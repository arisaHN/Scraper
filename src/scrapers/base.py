import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator, Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from ..normalizer import NormalizedReview

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


class BaseScraper(ABC):
    site_name: str = ""

    def __init__(self):
        self.session = requests.Session()
        self._rotate_ua()

    def _rotate_ua(self):
        self.session.headers.update(
            {
                "User-Agent": random.choice(_USER_AGENTS),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def _polite_delay(self):
        time.sleep(random.uniform(settings.SCRAPE_DELAY_MIN, settings.SCRAPE_DELAY_MAX))

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=3, max=60),
        retry=retry_if_exception_type(
            (requests.HTTPError, requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError)
        ),
        reraise=True,
    )
    def _get(self, url: str, **kwargs) -> requests.Response:
        resp = self.session.get(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    @abstractmethod
    def discover_products(self, brand_name: str) -> list[dict]:
        """Return list of dicts with keys: name, source_url, external_id."""

    @abstractmethod
    def scrape_reviews(self, product: dict, since: Optional[datetime] = None) -> Iterator[NormalizedReview]:
        """Yield NormalizedReview objects for a given product. If since is set, stop at reviews older than that datetime."""
