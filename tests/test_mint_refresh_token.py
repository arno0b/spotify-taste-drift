from urllib.parse import parse_qs, urlparse

import pytest

from tools.mint_refresh_token import REDIRECT_URI, build_authorize_url, exchange_code
from tools.spotify_auth import SpotifyAuthError


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.last_call = None

    def post(self, url, data=None, headers=None, timeout=None):
        self.last_call = {"url": url, "data": data, "headers": headers}
        return self._response


def test_redirect_uri_uses_loopback_ip_not_localhost():
    # Spotify rejects "localhost" and requires the loopback IP literal.
    assert REDIRECT_URI == "http://127.0.0.1:8888/callback"


def test_authorize_url_requests_only_user_top_read():
    url = build_authorize_url("cid", "state-123")

    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["user-top-read"]
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["state"] == ["state-123"]


def test_exchange_code_returns_refresh_token():
    session = FakeSession(
        FakeResponse(200, {"access_token": "tok", "refresh_token": "refresh-xyz"})
    )

    assert exchange_code("cid", "csecret", "auth-code", session=session) == "refresh-xyz"


def test_exchange_code_sends_authorization_code_grant():
    session = FakeSession(FakeResponse(200, {"refresh_token": "refresh-xyz"}))

    exchange_code("cid", "csecret", "auth-code", session=session)

    assert session.last_call["data"] == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": REDIRECT_URI,
    }


def test_exchange_code_raises_when_no_refresh_token_returned():
    session = FakeSession(FakeResponse(200, {"access_token": "tok"}))

    with pytest.raises(SpotifyAuthError):
        exchange_code("cid", "csecret", "auth-code", session=session)
