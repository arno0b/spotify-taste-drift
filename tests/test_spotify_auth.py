import pytest

from tools.spotify_auth import SpotifyAuthError, get_access_token


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Records the last POST so tests can assert on how the request was built."""

    def __init__(self, response):
        self._response = response
        self.last_call = None

    def post(self, url, data=None, headers=None, timeout=None):
        self.last_call = {"url": url, "data": data, "headers": headers, "timeout": timeout}
        return self._response


def test_returns_access_token_on_success():
    session = FakeSession(FakeResponse(200, {"access_token": "tok-abc", "expires_in": 3600}))

    token = get_access_token("cid", "csecret", "refresh-xyz", session=session)

    assert token == "tok-abc"


def test_sends_basic_auth_and_refresh_grant():
    session = FakeSession(FakeResponse(200, {"access_token": "tok-abc"}))

    get_access_token("cid", "csecret", "refresh-xyz", session=session)

    # base64("cid:csecret")
    assert session.last_call["headers"]["Authorization"] == "Basic Y2lkOmNzZWNyZXQ="
    assert session.last_call["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-xyz",
    }


def test_raises_with_actionable_message_when_token_revoked():
    session = FakeSession(FakeResponse(400, {}, text='{"error":"invalid_grant"}'))

    with pytest.raises(SpotifyAuthError) as excinfo:
        get_access_token("cid", "csecret", "refresh-xyz", session=session)

    message = str(excinfo.value)
    assert "400" in message
    assert "mint_refresh_token" in message


def test_raises_when_response_has_no_access_token():
    session = FakeSession(FakeResponse(200, {"scope": "user-top-read"}))

    with pytest.raises(SpotifyAuthError):
        get_access_token("cid", "csecret", "refresh-xyz", session=session)
