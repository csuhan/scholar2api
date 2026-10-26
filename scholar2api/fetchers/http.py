"""Fast path: plain HTTP with browser-like headers and polite pacing."""
import random
import time
from typing import Optional

import httpx

from .base import BaseFetcher, FetchResult, ScholarBlocked, ScholarHTTPError, detect_block

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class HttpFetcher(BaseFetcher):
    name = "http"

    def __init__(self, proxy: Optional[str] = None, min_interval: float = 1.0,
                 jitter: float = 2.0, timeout: float = 30.0):
        self._client = httpx.Client(proxy=proxy, timeout=timeout,
                                    follow_redirects=True,
                                    headers={"User-Agent": UA,
                                             "Accept-Language": "en-US,en;q=0.9"})
        self.min_interval, self.jitter = min_interval, jitter
        self._last = 0.0

    def set_cookies(self, cookies: dict) -> None:
        for key, value in cookies.items():
            self._client.cookies.set(key, value, domain=".google.com")

    def fetch(self, url: str, params: Optional[dict] = None) -> FetchResult:
        wait = self.min_interval + random.uniform(0, self.jitter) \
            - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

        r = self._client.get(url, params=params)
        if kind := detect_block(r.status_code, str(r.url), r.text):
            raise ScholarBlocked(kind, r.text, str(r.url))
        if r.status_code >= 400:
            raise ScholarHTTPError(r.status_code, str(r.url))
        return FetchResult(str(r.url), r.status_code, r.text, dict(r.cookies))

    def close(self) -> None:
        self._client.close()
