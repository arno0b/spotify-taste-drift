"""Exchange a Spotify refresh token for a short-lived access token.

Uses the classic Authorization Code flow with a client secret, NOT PKCE.
PKCE rotates the refresh token on every refresh, which cannot work in a
stateless GitHub Actions runner — the new token would have to be written
back to repo secrets after every run.
"""

import base64
import os

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"


class SpotifyAuthError(RuntimeError):
    """Raised when a token refresh fails for any reason."""


def get_access_token(client_id, client_secret, refresh_token, *, session=None):
    """Return a one-hour access token. Raises SpotifyAuthError on any failure."""
    session = session or requests
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    response = session.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise SpotifyAuthError(
            f"Token refresh failed ({response.status_code}): {response.text}\n"
            "If this reports invalid_grant, the refresh token was revoked — "
            "re-run: python tools/mint_refresh_token.py"
        )

    token = response.json().get("access_token")
    if not token:
        raise SpotifyAuthError(
            f"Token refresh returned 200 but no access_token: {response.text}"
        )
    return token


def load_credentials():
    """Read the three credentials from the environment, failing loudly if absent."""
    names = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")
    values = [os.environ.get(name) for name in names]
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        raise SpotifyAuthError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Locally: copy .env.example to .env and fill it in. "
            "In CI: check the repo's Actions secrets."
        )
    return tuple(values)
