"""Fetcher interface and anti-bot detection."""
from dataclasses import dataclass, field
from typing import Dict, Optional


class ScholarBlocked(Exception):
    """Google served an anti-bot page instead of content.

    kind: captcha_form | sorry | rate_limited | js_challenge
          (plus *_unsolved / *_after_solve from the solver path)
    """

    def __init__(self, kind: str, html: str = "", url: str = ""):
        super().__init__(f"scholar blocked: {kind} ({url})")
        self.kind, self.html, self.url = kind, html, url


class ScholarHTTPError(Exception):
    """Unexpected non-2xx status (e.g. 404 for an unknown citation id)."""

    def __init__(self, status: int, url: str = ""):
        super().__init__(f"scholar HTTP {status} ({url})")
        self.status, self.url = status, url


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    cookies: Dict[str, str] = field(default_factory=dict)


def detect_block(status: int, url: str, html: str) -> Optional[str]:
    if status == 429:
        return "rate_limited"
    if "/sorry/index" in url or "unusual traffic" in html:
        return "sorry"
    if "gs_captcha_f" in html:
        return "captcha_form"
    low = html.lower()
    if "not a robot" in low or "recaptcha/api.js" in low:
        return "js_challenge"
    return None


class BaseFetcher:
    name = "base"

    def fetch(self, url: str, params: Optional[dict] = None) -> FetchResult:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass
