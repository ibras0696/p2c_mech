from app.bot.session_state import InMemoryPlatformSessionStore


def test_session_store_extracts_socket_curl_cookies() -> None:
    store = InMemoryPlatformSessionStore()

    session = store.update_from_text(
        "curl 'wss://example/ws' -b 'access_token=abc.def; __cf_bm=cloudflare-token'"
    )

    assert session.access_token == "abc.def"
    assert session.cf_bm == "cloudflare-token"
    assert session.cookie_header == "access_token=abc.def; __cf_bm=cloudflare-token"


def test_session_store_accepts_cf_only_cookie() -> None:
    store = InMemoryPlatformSessionStore()

    session = store.update_from_text("curl 'https://example' -b '__cf_bm=cf-only'")

    assert session.access_token == ""
    assert session.cf_bm == "cf-only"
