"""One-time local script to mint a Spotify refresh token.

Run this once on your own machine:

    python tools/mint_refresh_token.py

It opens a browser for consent, catches the redirect on a local server, and
prints the refresh token to stdout. Paste that into GitHub repo secrets as
SPOTIFY_REFRESH_TOKEN and into your local .env.

This script NEVER writes the token to a file. This is a public repo; a token
written to disk is one careless `git add -A` away from being disclosed.
"""

import base64
import http.server
import os
import pathlib
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

# Make `python tools/mint_refresh_token.py` work from the repo root, per the
# project convention in CLAUDE.md. Running a script directly puts only tools/
# on sys.path, so the repo root has to be added before the package import.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools.spotify_auth import TOKEN_URL, SpotifyAuthError  # noqa: E402

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "user-top-read"


def build_authorize_url(client_id, state):
    """Build the consent URL the user's browser is sent to."""
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(client_id, client_secret, code, *, session=None):
    """Trade the one-time authorization code for a long-lived refresh token."""
    session = session or requests
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    response = session.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )

    if response.status_code != 200:
        raise SpotifyAuthError(
            f"Code exchange failed ({response.status_code}): {response.text}"
        )

    refresh_token = response.json().get("refresh_token")
    if not refresh_token:
        raise SpotifyAuthError(
            f"Code exchange returned 200 but no refresh_token: {response.text}"
        )
    return refresh_token


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result = {}
    done = threading.Event()

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.result = {
            "code": query.get("code", [None])[0],
            "state": query.get("state", [None])[0],
            "error": query.get("error", [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>Done.</h1><p>You can close this tab.</p>")
        _CallbackHandler.done.set()

    def log_message(self, *args):
        pass  # keep the console clean


def main():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET first.\n"
            "PowerShell:  $env:SPOTIFY_CLIENT_ID='...'; $env:SPOTIFY_CLIENT_SECRET='...'",
            file=sys.stderr,
        )
        return 1

    state = secrets.token_urlsafe(16)
    server = http.server.HTTPServer(("127.0.0.1", 8888), _CallbackHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = build_authorize_url(client_id, state)
    print(f"Opening browser for consent.\nIf it does not open, visit:\n{url}\n")
    webbrowser.open(url)

    if not _CallbackHandler.done.wait(timeout=300):
        print("Timed out waiting for consent after 5 minutes.", file=sys.stderr)
        return 1

    result = _CallbackHandler.result
    if result.get("error"):
        print(f"Consent denied: {result['error']}", file=sys.stderr)
        return 1
    if result.get("state") != state:
        print("State mismatch — aborting.", file=sys.stderr)
        return 1

    refresh_token = exchange_code(client_id, client_secret, result["code"])
    print("\nRefresh token (store as SPOTIFY_REFRESH_TOKEN, do not commit):\n")
    print(refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
