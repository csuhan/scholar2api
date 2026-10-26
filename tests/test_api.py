"""Tests for the fetch chain, SQLite cache and HTTP API."""
import pathlib
import time

import pytest
from fastapi.testclient import TestClient

from scholar2api import service
from scholar2api.client import ScholarClient
from scholar2api.fetchers.base import FetchResult, ScholarBlocked, ScholarHTTPError
from scholar2api.store import SqliteCache

FIX = pathlib.Path(__file__).parent / "fixtures"
UID = "vgcxKEcAAAAJ"
PROFILE = (FIX / "profile.html").read_text(encoding="utf-8")
PAGE2 = (FIX / "page2.html").read_text(encoding="utf-8")


class FakeFetcher:
    def __init__(self, html="", block=None):
        self.html, self.block, self.calls = html, block, 0

    def fetch(self, url, params=None):
        self.calls += 1
        if self.block:
            raise ScholarBlocked(self.block)
        return FetchResult(url, 200, self.html, {"NID": "x"})

    def close(self):
        pass


def make_client(tmp_path, http=None, browser=None, ttl=60):
    return ScholarClient(fetcher=http or FakeFetcher(html=PROFILE),
                         browser_fetcher=browser, cache_ttl=ttl,
                         db_path=str(tmp_path / "cache.db"))


def test_fallback_and_cache(tmp_path):
    http, browser = FakeFetcher(block="captcha_form"), FakeFetcher(html=PROFILE)
    client = make_client(tmp_path, http, browser)

    assert client.profile(UID).name == "Jiaming Han"
    assert (http.calls, browser.calls) == (1, 1)
    assert client.profile(UID).name == "Jiaming Han"  # from cache
    assert (http.calls, browser.calls) == (1, 1)


def test_cache_survives_client_restart(tmp_path):
    first = make_client(tmp_path)
    first.profile(UID)
    first.close()

    second_http = FakeFetcher(html=PROFILE)
    second = make_client(tmp_path, second_http)
    assert second.profile(UID).name == "Jiaming Han"
    assert second_http.calls == 0  # read from SQLite, no upstream call


def test_refresh_bypasses_cache(tmp_path):
    http = FakeFetcher(html=PROFILE)
    client = make_client(tmp_path, http)
    client.profile(UID)
    client.profile(UID, refresh=True)
    assert http.calls == 2


def test_cache_expiry(tmp_path):
    http = FakeFetcher(html=PROFILE)
    client = make_client(tmp_path, http, ttl=0.05)
    client.profile(UID)
    time.sleep(0.1)
    client.profile(UID)
    assert http.calls == 2


def test_store_stats_and_clear(tmp_path):
    store = SqliteCache(str(tmp_path / "s.db"), ttl=60)
    store.set("k", "hello")
    assert store.get("k") == "hello"
    assert store.stats()["entries"] == 1
    store.clear()
    assert store.stats()["entries"] == 0


def test_papers_cursor(tmp_path):
    client = make_client(tmp_path, FakeFetcher(html=PAGE2))
    batch, cursor = client.papers(UID, cstart=20, pagesize=100)
    assert (len(batch), cursor) == (9, 29)


def test_invalid_user_id(tmp_path):
    with pytest.raises(ValueError):
        make_client(tmp_path).profile("https://example.com/x")


def test_block_cooldown_fails_fast(tmp_path):
    http, browser = FakeFetcher(block="sorry"), FakeFetcher(block="sorry_unsolved")
    client = make_client(tmp_path, http, browser)
    with pytest.raises(ScholarBlocked):
        client.profile(UID)
    with pytest.raises(ScholarBlocked) as err:
        client.profile(UID)
    assert err.value.kind == "cooldown"
    assert (http.calls, browser.calls) == (1, 1)


@pytest.fixture()
def api(tmp_path):
    service.client = make_client(tmp_path)
    return TestClient(service.app)


def test_author_endpoint(api):
    data = api.get(f"/author/{UID}").json()
    assert data["name"] == "Jiaming Han"
    assert data["stats"]["citations"]["all"] == 7209


def test_author_by_url_endpoint(api):
    url = "https://scholar.google.com.hk/citations?user=vgcxKEcAAAAJ&hl=zh-CN"
    assert api.get("/author", params={"url": url}).json()["name"] == "Jiaming Han"


def test_cache_stats_endpoint(api):
    api.get(f"/author/{UID}")
    stats = api.get("/cache/stats").json()
    assert stats["entries"] >= 1 and stats["ttl_seconds"] == 60


def test_bad_id_is_400(api):
    assert api.get("/author/abc").status_code == 400


def test_missing_paper_is_404(tmp_path):
    class NotFound:
        def fetch(self, url, params=None):
            raise ScholarHTTPError(404, url)

        def close(self):
            pass

    service.client = make_client(tmp_path, NotFound())
    with TestClient(service.app) as c:
        assert c.get(f"/author/{UID}/paper/nope").status_code == 404


def test_blocked_is_503(tmp_path):
    service.client = make_client(tmp_path, FakeFetcher(block="captcha_form"),
                                 FakeFetcher(block="captcha_failed"))
    with TestClient(service.app) as c:
        assert c.get(f"/author/{UID}").status_code == 503
