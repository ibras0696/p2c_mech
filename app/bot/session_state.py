from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

ACCESS_TOKEN_RE = re.compile(r"access_token=([^;\s'\\]+)")
CF_BM_RE = re.compile(r"__cf_bm=([^;\s'\\]+)")
COOKIE_HEADER_RE = re.compile(r"(?:-b|--cookie)\s+['\"]([^'\"]+)['\"]")


@dataclass(frozen=True)
class PlatformSession:
    access_token: str
    cf_bm: str
    updated_at: datetime

    @property
    def cookie_header(self) -> str:
        cookies: list[str] = []
        if self.access_token:
            cookies.append(f"access_token={self.access_token}")
        if self.cf_bm:
            cookies.append(f"__cf_bm={self.cf_bm}")
        return "; ".join(cookies)


class InMemoryPlatformSessionStore:
    def __init__(self) -> None:
        self._session: PlatformSession | None = None

    def update_from_text(self, text: str) -> PlatformSession:
        session = parse_platform_session_from_text(text)
        self._session = session
        return session

    def current(self) -> PlatformSession | None:
        return self._session


def extract_cookie_text(text: str) -> str:
    match = COOKIE_HEADER_RE.search(text)
    if match:
        return match.group(1)
    return text


def parse_platform_session_from_text(text: str) -> PlatformSession:
    cookie_text = extract_cookie_text(text)
    session = PlatformSession(
        access_token=extract_first(ACCESS_TOKEN_RE, cookie_text),
        cf_bm=extract_first(CF_BM_RE, cookie_text),
        updated_at=datetime.now(UTC),
    )
    if not session.access_token and not session.cf_bm:
        raise ValueError("No supported cookies found")
    return session


def extract_first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if match:
        return match.group(1)
    return ""


platform_session_store = InMemoryPlatformSessionStore()
