"""Tests for the fetch fallback chain, cache and HTTP API."""
import pathlib

import pytest
from fastapi.testclient import TestClient

from scholar2api import service
from scholar2api.client import ScholarClient
from scholar2api.fetchers.base import FetchResult, ScholarBlocked, ScholarHTTPError

FIX = pathlib.Path(__file__).parent / "fixtures"
UID = "vgcxKEcAAAAJ"
PROFILE = (FIX / "profile.html").read_text(encoding="utf-8")


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


def test_fallback_and_cache():
    http, browser = FakeFetcher(block="captcha_form"), FakeFetcher(html=PROFILE)
    client = ScholarClient(fetcher=http, browser_fetcher=browser, cache_ttl=60)

    assert client.profile(UID).name == "Jiaming Han"
    assert (http.calls, browser.calls) == (1, 1)
    assert client.profile(UID).name == "Jiaming Han"  # from cache
    assert (http.calls, browser.calls) == (1, 1)


def test_papers_cursor():
    page2 = (FIX / "page2.html").read_text(encoding="utf-8")
    client = ScholarClient(fetcher=FakeFetcher(html=page2), cache_ttl=60)
    batch, cursor = client.papers(UID, cstart=20, pagesize=100)
    assert (len(batch), cursor) == (9, 29)


def test_invalid_user_id():
    with pytest.raises(ValueError):
        ScholarClient(fetcher=FakeFetcher()).profile("https://example.com/x")


@pytest.fixture()
def api():
    service.client = ScholarClient(fetcher=FakeFetcher(html=PROFILE), cache_ttl=0)
    return TestClient(service.app)


def test_author_endpoint(api):
    data = api.get(f"/author/{UID}").json()
    assert data["name"] == "Jiaming Han"
    assert data["stats"]["citations"]["all"] == 7209


def test_author_by_url_endpoint(api):
    url = "https://scholar.google.com.hk/citations?user=vgcxKEcAAAAJ&hl=zh-CN"
    data = api.get("/author", params={"url": url}).json()
    assert data["name"] == "Jiaming Han"


def test_bad_id_is_400(api):
    assert api.get("/author/abc").status_code == 400


def test_missing_paper_is_404():
    class NotFound:
        def fetch(self, url, params=None):
            raise ScholarHTTPError(404, url)

        def close(self):
            pass

    service.client = ScholarClient(fetcher=NotFound(), cache_ttl=0)
    with TestClient(service.app) as c:
        assert c.get(f"/author/{UID}/paper/nope").status_code == 404


def test_blocked_is_503():
    service.client = ScholarClient(fetcher=FakeFetcher(block="captcha_form"),
                                   browser_fetcher=FakeFetcher(block="captcha_failed"))
    with TestClient(service.app) as c:
        assert c.get(f"/author/{UID}").status_code == 503


def test_block_cooldown_fails_fast():
    http = FakeFetcher(block="sorry")
    browser = FakeFetcher(block="sorry_unsolved")
    client = ScholarClient(fetcher=http, browser_fetcher=browser, cache_ttl=0)
    with pytest.raises(ScholarBlocked):
        client.profile(UID)
    with pytest.raises(ScholarBlocked) as err:
        client.profile(UID)
    assert err.value.kind == "cooldown"
    assert (http.calls, browser.calls) == (1, 1)  # no extra upstream work
