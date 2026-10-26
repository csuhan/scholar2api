"""Scholar client: endpoint URLs, fetch fallback chain, TTL cache.

Per request: cache -> HttpFetcher -> BrowserFetcher (+ VLM captcha solver).
Cookies obtained by the browser are injected back into the HTTP client so the
cheap path works again after a block.
"""
import os
import threading
import time
from typing import List, Optional, Tuple

import httpx

from .fetchers.base import BaseFetcher, ScholarBlocked
from .fetchers.http import HttpFetcher
from .models import AuthorProfile, Paper, PaperDetail
from .parsers import extract_user_id, parse_paper_detail, parse_paper_list, parse_profile
from .store import SqliteCache

BASE = "https://scholar.google.com"


class ScholarClient:
    def __init__(self, fetcher: Optional[BaseFetcher] = None,
                 browser_fetcher: Optional[BaseFetcher] = None,
                 cache_ttl: Optional[float] = None,
                 db_path: Optional[str] = None):
        self.http = fetcher or HttpFetcher(proxy=os.getenv("SCHOLAR2API_PROXY"))
        self._browser = browser_fetcher
        self.cache_ttl = (cache_ttl if cache_ttl is not None
                          else float(os.getenv("SCHOLAR2API_CACHE_TTL", "86400")))
        self.cache = SqliteCache(
            db_path or os.getenv("SCHOLAR2API_DB_PATH", "scholar2api.db"),
            self.cache_ttl)
        self._browser_lock = threading.Lock()
        # after a hard IP block, fail fast instead of retrying the browser
        self.block_cooldown = float(os.getenv("SCHOLAR2API_BLOCK_COOLDOWN", "120"))
        self._blocked_until = 0.0

    # --------------------------------------------------------------- api ---
    def profile(self, id_or_url: str, pagesize: int = 100,
                refresh: bool = False) -> AuthorProfile:
        uid = extract_user_id(id_or_url)
        html = self._get(f"{BASE}/citations", {
            "user": uid, "hl": "en", "cstart": 0,
            "pagesize": min(pagesize, 100)}, refresh)
        return parse_profile(html, uid)

    def papers(self, id_or_url: str, cstart: int = 0, pagesize: int = 100,
               sortby: str = "cited",
               refresh: bool = False) -> Tuple[List[Paper], int]:
        uid = extract_user_id(id_or_url)
        params = {"view_op": "list_works", "hl": "en", "user": uid,
                  "cstart": cstart, "pagesize": min(pagesize, 100)}
        if sortby == "pubdate":
            params["sortby"] = "pubdate"
        batch = parse_paper_list(self._get(f"{BASE}/citations", params, refresh))
        return batch, cstart + len(batch)  # cursor by actual count

    def all_papers(self, id_or_url: str, sortby: str = "cited",
                   max_pages: int = 20) -> List[Paper]:
        papers, cstart, page = [], 0, 0
        while page < max_pages:
            batch, cstart = self.papers(id_or_url, cstart, 100, sortby)
            papers.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return papers

    def paper_detail(self, id_or_url: str, cluster: str,
                     refresh: bool = False) -> PaperDetail:
        uid = extract_user_id(id_or_url)
        cfv = cluster if ":" in cluster else f"{uid}:{cluster}"
        html = self._get(f"{BASE}/citations", {
            "view_op": "view_citation", "hl": "en", "user": uid,
            "citation_for_view": cfv}, refresh)
        return parse_paper_detail(html, cfv)

    def close(self) -> None:
        self.http.close()
        self.cache.close()
        if self._browser:
            self._browser.close()

    # ------------------------------------------------------------ private ---
    def _get(self, url: str, params: Optional[dict] = None,
             refresh: bool = False) -> str:
        key = url + "|" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        if not refresh and (cached := self.cache.get(key)):
            return cached

        if time.time() < self._blocked_until:
            raise ScholarBlocked("cooldown")

        try:
            try:
                result = self.http.fetch(url, params)
            except httpx.TransportError:
                result = self.http.fetch(url, params)  # one retry
        except ScholarBlocked:
            try:
                result = self._browser_fetcher().fetch(url, params)
            except ScholarBlocked:
                # IP-level block: further requests would just burn 25s each
                self._blocked_until = time.time() + self.block_cooldown
                raise
            if result.cookies and isinstance(self.http, HttpFetcher):
                self.http.set_cookies(result.cookies)

        self.cache.set(key, result.html)
        return result.html

    def _browser_fetcher(self) -> BaseFetcher:
        if self._browser is None:
            with self._browser_lock:
                if self._browser is None:
                    from .fetchers.browser import BrowserFetcher
                    from .solvers.vlm import VlmSolver
                    self._browser = BrowserFetcher(
                        solver=VlmSolver(), proxy=os.getenv("SCHOLAR2API_PROXY"))
        return self._browser
