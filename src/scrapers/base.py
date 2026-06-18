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
    # Set True by scrapers whose scrape_reviews() honors backfill_offset/max_backfill_pages
    # to gradually paginate through deep history across capped, resumable runs (currently
    # only SephoraHTMLScraper — needed because each request risks tripping Akamai's
    # request-volume-based blocking; see SephoraHTMLScraper for details).
    supports_backfill: bool = False

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

    def close(self):
        """Release any held resources (browser process, sockets, ...). No-op by default."""

    @staticmethod
    def _past_cutoff(review_date: Optional[datetime], since: Optional[datetime]) -> bool:
        """True if review_date is strictly older than since (tz-naive comparison)."""
        if not since or not review_date:
            return False
        review_dt = review_date.replace(tzinfo=None) if review_date.tzinfo else review_date
        since_dt = since.replace(tzinfo=None) if since.tzinfo else since
        return review_dt < since_dt

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
    def scrape_reviews(
        self,
        product: dict,
        since: Optional[datetime] = None,
        backfill_offset: Optional[int] = None,
        max_backfill_pages: Optional[int] = None,
    ) -> Iterator[NormalizedReview]:
        """Yield NormalizedReview objects for a given product. If since is set, stop at
        reviews older than that datetime. backfill_offset/max_backfill_pages are only
        meaningful when supports_backfill is True; other scrapers can ignore them."""


class CamoufoxBrowserMixin:
    """Shared Camoufox (anti-bot Firefox) browser lifecycle for scrapers that need a real
    browser — currently SephoraHTMLScraper and NotinoScraper. Mixed in alongside
    BaseScraper rather than folded into it directly since BazaarvoiceScraper (plain REST)
    has no use for a browser at all."""

    def __init__(self):
        super().__init__()
        self._camoufox = None
        self._browser = None

    def _open_browser(self):
        if self._camoufox is None:
            from camoufox.sync_api import Camoufox
            self._camoufox = Camoufox(headless=True, geoip=True)
            self._browser = self._camoufox.__enter__()

    def _close_browser(self):
        if self._camoufox is not None:
            try:
                self._camoufox.__exit__(None, None, None)
            except Exception:
                pass
            self._camoufox = None
            self._browser = None

    def _refresh_browser(self):
        """Close and reopen Camoufox to get a fresh browser fingerprint."""
        self._close_browser()
        self._open_browser()

    def _new_page(self):
        self._open_browser()
        page = self._browser.new_page()
        page.on("pageerror", lambda _: None)
        return page

    def _dismiss_consent(self, page):
        """Click any visible cookie/consent button. Shared by all browser-based scrapers."""
        for selector in [
            "button:has-text('Accetta')", "button:has-text('Accetto')",
            "button:has-text('Accept')", "button[id*='accept']",
            "[data-testid*='accept']",
        ]:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=5_000)
                    break
            except Exception:
                pass

    def close(self):
        self._close_browser()

    def __del__(self):
        self.close()
