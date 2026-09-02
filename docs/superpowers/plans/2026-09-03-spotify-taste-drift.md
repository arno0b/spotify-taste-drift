# Spotify Taste Drift Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture Spotify top artists and tracks daily into an immutable store, and publish a static dashboard showing how taste moves over time.

**Architecture:** Three layers, each a pure function of the one above it — `data/raw/` (immutable API responses) → `data/derived/` (tidy CSVs) → `site/data.json` (dashboard metrics). A GitHub Actions cron captures daily, rebuilds everything downstream from raw, commits, and deploys Pages. Full rebuild rather than incremental update, so an improved metric replays across all history.

**Tech Stack:** Python 3.12, `requests`, `pytest`. Vanilla JS plus Observable Plot from CDN. GitHub Actions. No database, no bundler, no frontend framework.

**Spec:** [`docs/superpowers/specs/2026-09-03-spotify-taste-drift-design.md`](../specs/2026-09-03-spotify-taste-drift-design.md)

## Global Constraints

- **Python 3.12.** Same version locally and in the Action.
- **Runtime dependencies: `requests` only.** Dev adds `pytest`. Nothing else without a decision.
- **No live Spotify API calls in tests or CI.** Every test uses a fake session or fixture files.
- **This is a PUBLIC repo.** Secrets live only in GitHub Actions secrets and a gitignored `.env`. `mint_refresh_token.py` writes to stdout only, never to a tracked file.
- **All timestamps UTC.** `snapshot_date` is `YYYY-MM-DD`; `captured_at` is ISO 8601 with a `Z` suffix.
- **Deterministic output ordering.** CSV rows sort by `(snapshot_date, time_range, kind, rank)`; JSON keys are written sorted. Non-deterministic ordering produces noise diffs in a repo where the diff *is* the product.
- **A silent failure is a permanent data gap.** Capture failures exit non-zero. Never swallow an exception to keep the job green.
- **Time range values:** `short_term`, `medium_term`, `long_term`. **Kind values:** `artist`, `track` (singular) in all derived data. Note the Spotify API path uses the plural `artists` / `tracks`; convert at the boundary in Task 4.
- **Gating thresholds:** survival curve needs ≥ 8 weeks of history; rotation half-life needs ≥ 10 completed spells.
- **Genre weighting:** `w = 1/rank`, split equally across an artist's genres. Defined once as `RANK_WEIGHT` in `tools/build_metrics.py`.

---

### Task 1: Scaffolding and a green test run

Establishes the package layout and test harness so every later task has somewhere to put code and a command that proves it works.

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `tools/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing
- Produces: `pytest` runnable from the repo root with `tools/` importable as `tools.<module>`

- [ ] **Step 1: Write the failing test**

Create `tests/test_smoke.py`:

```python
def test_tools_package_is_importable():
    import tools

    assert tools.__name__ == "tools"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Create the package and config files**

Create `tools/__init__.py` and `tests/__init__.py` as empty files.

Create `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`pythonpath = ["."]` is what makes `import tools` resolve from the repo root without installing the package.

Create `requirements.txt`:

```
requests==2.32.3
```

Create `.env.example`:

```
# Copy to .env and fill in. .env is gitignored — never commit it.
# Obtain these by registering an app at https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
# Produced by: python tools/mint_refresh_token.py
SPOTIFY_REFRESH_TOKEN=
```

- [ ] **Step 4: Install dependencies and run the test**

Run:
```bash
pip install -r requirements.txt pytest
python -m pytest tests/test_smoke.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt .env.example tools/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: scaffold python package and pytest harness"
```

---

### Task 2: Access token refresh

Exchanges the long-lived refresh token for a one-hour access token. Every other API call depends on this.

**Files:**
- Create: `tools/spotify_auth.py`
- Test: `tests/test_spotify_auth.py`

**Interfaces:**
- Consumes: Task 1's package layout
- Produces:
  - `SpotifyAuthError(RuntimeError)`
  - `get_access_token(client_id: str, client_secret: str, refresh_token: str, *, session=None) -> str`
  - `load_credentials() -> tuple[str, str, str]` returning `(client_id, client_secret, refresh_token)` from the environment
  - `TOKEN_URL: str`

The `session` keyword takes anything with a `.post()` matching `requests.post`. That is the seam every test uses — there is no HTTP mocking library in this project.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_spotify_auth.py`:

```python
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
```

The last test matters: a 200 with a malformed body would otherwise raise a bare `KeyError` at the call site, which tells you nothing about what actually went wrong.

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_spotify_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.spotify_auth'`

- [ ] **Step 3: Write the implementation**

Create `tools/spotify_auth.py`:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_spotify_auth.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add tools/spotify_auth.py tests/test_spotify_auth.py
git commit -m "feat: exchange refresh token for spotify access token"
```

---

### Task 3: Mint the refresh token (one-time, local)

A local script that walks through browser consent once and prints a refresh token. Run by a human, never in CI.

**Files:**
- Create: `tools/mint_refresh_token.py`
- Test: `tests/test_mint_refresh_token.py`

**Interfaces:**
- Consumes: `tools.spotify_auth.TOKEN_URL`, `SpotifyAuthError`
- Produces:
  - `REDIRECT_URI: str` = `"http://127.0.0.1:8888/callback"`
  - `build_authorize_url(client_id: str, state: str) -> str`
  - `exchange_code(client_id: str, client_secret: str, code: str, *, session=None) -> str` returning the refresh token

The local HTTP server loop is deliberately untested — it needs a real browser. The two pure functions around it carry the tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mint_refresh_token.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_mint_refresh_token.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.mint_refresh_token'`

- [ ] **Step 3: Write the implementation**

Create `tools/mint_refresh_token.py`:

```python
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
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

from tools.spotify_auth import TOKEN_URL, SpotifyAuthError

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
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_mint_refresh_token.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Register the Spotify app and mint the real token**

This step is manual and cannot be automated.

1. Go to https://developer.spotify.com/dashboard and create an app.
2. Add redirect URI exactly: `http://127.0.0.1:8888/callback`
3. Copy the Client ID and Client Secret.
4. Run:
   ```bash
   SPOTIFY_CLIENT_ID=xxx SPOTIFY_CLIENT_SECRET=yyy python tools/mint_refresh_token.py
   ```
   On PowerShell:
   ```powershell
   $env:SPOTIFY_CLIENT_ID='xxx'; $env:SPOTIFY_CLIENT_SECRET='yyy'; python tools/mint_refresh_token.py
   ```
5. Copy `.env.example` to `.env` and fill in all three values.

Expected: a refresh token printed to the terminal. Confirm `.env` is ignored with `git status --short` — it must not appear.

- [ ] **Step 6: Commit**

```bash
git add tools/mint_refresh_token.py tests/test_mint_refresh_token.py
git commit -m "feat: add one-time refresh token minting script"
```

---

### Task 4: Daily capture into the raw layer

The heart of milestone 1. Writes the six API responses verbatim plus a manifest, idempotently and atomically.

**Files:**
- Create: `tools/snapshot_top_items.py`
- Test: `tests/test_snapshot_top_items.py`

**Interfaces:**
- Consumes: `tools.spotify_auth.get_access_token`, `load_credentials`
- Produces:
  - `KINDS = ("artists", "tracks")` — plural, matching the API path
  - `TIME_RANGES = ("short_term", "medium_term", "long_term")`
  - `SCRIPT_VERSION: str`
  - `capture(fetch, raw_root: Path, snapshot_date: str, *, now_iso: str) -> dict` returning `{"skipped": bool, "partial": bool, "calls": dict, "path": str}`
  - `make_fetcher(token, *, session=None, sleep=None) -> Callable[[str, str], tuple[int, dict | None]]` — `sleep` defaults to `time.sleep`; tests pass a recorder

`capture` takes `fetch` as a parameter rather than making requests itself. That is what makes the whole thing testable without touching the network.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_snapshot_top_items.py`:

```python
import json

import pytest

from tools.snapshot_top_items import capture, make_fetcher

NOW = "2026-09-03T06:00:12Z"


def ok_fetch(kind, time_range):
    return 200, {"items": [{"id": f"{kind}-{time_range}-1", "name": "Thing"}]}


def test_writes_six_response_files_and_a_manifest(tmp_path):
    result = capture(ok_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    day_dir = tmp_path / "2026-09-03"
    written = sorted(p.name for p in day_dir.iterdir())
    assert written == sorted(
        [
            "meta.json",
            "top_artists_short_term.json",
            "top_artists_medium_term.json",
            "top_artists_long_term.json",
            "top_tracks_short_term.json",
            "top_tracks_medium_term.json",
            "top_tracks_long_term.json",
        ]
    )
    assert result["skipped"] is False
    assert result["partial"] is False


def test_writes_payload_verbatim(tmp_path):
    capture(ok_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    payload = json.loads(
        (tmp_path / "2026-09-03" / "top_artists_short_term.json").read_text()
    )
    assert payload == {"items": [{"id": "artists-short_term-1", "name": "Thing"}]}


def test_manifest_records_status_and_count_per_call(tmp_path):
    capture(ok_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    meta = json.loads((tmp_path / "2026-09-03" / "meta.json").read_text())
    assert meta["captured_at"] == NOW
    assert meta["partial"] is False
    assert meta["calls"]["top_artists_short_term"] == {"status": 200, "count": 1}
    assert len(meta["calls"]) == 6


def test_partial_capture_writes_what_succeeded_and_flags_itself(tmp_path):
    def flaky_fetch(kind, time_range):
        if kind == "tracks" and time_range == "long_term":
            return 500, None
        return ok_fetch(kind, time_range)

    result = capture(flaky_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    day_dir = tmp_path / "2026-09-03"
    assert result["partial"] is True
    assert not (day_dir / "top_tracks_long_term.json").exists()
    assert (day_dir / "top_artists_short_term.json").exists()

    meta = json.loads((day_dir / "meta.json").read_text())
    assert meta["partial"] is True
    assert meta["calls"]["top_tracks_long_term"] == {"status": 500, "count": 0}


def test_second_run_on_same_date_is_a_no_op(tmp_path):
    capture(ok_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    def exploding_fetch(kind, time_range):
        raise AssertionError("must not re-fetch an already-captured day")

    result = capture(exploding_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    assert result["skipped"] is True


def test_empty_top_list_is_valid_data_not_an_error(tmp_path):
    result = capture(lambda kind, tr: (200, {"items": []}), tmp_path, "2026-09-03", now_iso=NOW)

    assert result["partial"] is False
    meta = json.loads((tmp_path / "2026-09-03" / "meta.json").read_text())
    assert meta["calls"]["top_artists_short_term"]["count"] == 0


def test_crash_midway_leaves_no_directory_to_block_a_retry(tmp_path):
    calls = {"n": 0}

    def crashing_fetch(kind, time_range):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("network died")
        return ok_fetch(kind, time_range)

    with pytest.raises(RuntimeError):
        capture(crashing_fetch, tmp_path, "2026-09-03", now_iso=NOW)

    # The idempotency check keys on the day directory existing. If a crashed
    # run left a half-written one behind, that day could never be captured.
    assert not (tmp_path / "2026-09-03").exists()


def test_fetcher_retries_on_429_then_succeeds():
    responses = [
        FakeResponse(429, headers={"Retry-After": "1"}),
        FakeResponse(200, {"items": []}),
    ]

    class Session:
        def __init__(self):
            self.calls = 0

        def get(self, url, headers=None, params=None, timeout=None):
            response = responses[self.calls]
            self.calls += 1
            return response

    slept = []
    fetch = make_fetcher("tok", session=Session(), sleep=slept.append)

    status, payload = fetch("artists", "short_term")

    assert status == 200
    assert payload == {"items": []}
    assert slept == [1.0]


def test_fetcher_gives_up_after_three_retries():
    class Session:
        def get(self, url, headers=None, params=None, timeout=None):
            return FakeResponse(429, headers={"Retry-After": "1"})

    fetch = make_fetcher("tok", session=Session(), sleep=lambda _: None)

    status, payload = fetch("artists", "short_term")

    assert status == 429
    assert payload is None


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_snapshot_top_items.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.snapshot_top_items'`

- [ ] **Step 3: Write the implementation**

Create `tools/snapshot_top_items.py`:

```python
"""Capture today's Spotify top artists and tracks into the immutable raw layer.

Spotify's top-items endpoints have no history and cannot be backfilled, so a
missed capture is permanent data loss. Everything here is built around not
losing a day: partial results are kept, writes are atomic, and reruns are safe.
"""

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import requests

from tools.spotify_auth import get_access_token, load_credentials

KINDS = ("artists", "tracks")  # plural: matches the API path
TIME_RANGES = ("short_term", "medium_term", "long_term")
SCRIPT_VERSION = "1.0.0"
API_URL = "https://api.spotify.com/v1/me/top/{kind}"
LIMIT = 50
MAX_RETRIES = 3
RAW_ROOT = Path("data/raw")


def make_fetcher(token, *, session=None, sleep=None):
    """Return fetch(kind, time_range) -> (status, payload|None), with 429 retries."""
    import time

    session = session or requests
    sleep = sleep or time.sleep

    def fetch(kind, time_range):
        for attempt in range(MAX_RETRIES + 1):
            response = session.get(
                API_URL.format(kind=kind),
                headers={"Authorization": f"Bearer {token}"},
                params={"time_range": time_range, "limit": LIMIT},
                timeout=30,
            )
            if response.status_code == 200:
                return 200, response.json()
            if response.status_code == 429 and attempt < MAX_RETRIES:
                # Respect Retry-After; fall back to exponential backoff.
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 2.0 ** attempt
                sleep(delay)
                continue
            return response.status_code, None
        return 429, None

    return fetch


def capture(fetch, raw_root, snapshot_date, *, now_iso):
    """Capture all six top-item responses for one day. Idempotent and atomic."""
    raw_root = Path(raw_root)
    day_dir = raw_root / snapshot_date
    if day_dir.exists():
        return {"skipped": True, "partial": False, "calls": {}, "path": str(day_dir)}

    # Build in a staging directory and rename at the end, so a crash mid-run
    # cannot leave a half-written day that the check above would treat as done.
    staging = raw_root / f".{snapshot_date}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    calls = {}
    try:
        for kind in KINDS:
            for time_range in TIME_RANGES:
                name = f"top_{kind}_{time_range}"
                status, payload = fetch(kind, time_range)
                succeeded = status == 200 and payload is not None
                calls[name] = {
                    "status": status,
                    "count": len(payload.get("items", [])) if succeeded else 0,
                }
                if succeeded:
                    _write_json(staging / f"{name}.json", payload)

        partial = any(call["status"] != 200 for call in calls.values())
        _write_json(
            staging / "meta.json",
            {
                "captured_at": now_iso,
                "script_version": SCRIPT_VERSION,
                "calls": calls,
                "partial": partial,
            },
        )
        staging.rename(day_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {"skipped": False, "partial": partial, "calls": calls, "path": str(day_dir)}


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    client_id, client_secret, refresh_token = load_credentials()
    token = get_access_token(client_id, client_secret, refresh_token)

    now = dt.datetime.now(dt.timezone.utc)
    result = capture(
        make_fetcher(token),
        RAW_ROOT,
        now.strftime("%Y-%m-%d"),
        now_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    if result["skipped"]:
        print(f"Already captured: {result['path']}")
        return 0

    print(f"Captured {result['path']}")
    for name, call in sorted(result["calls"].items()):
        print(f"  {name}: status={call['status']} count={call['count']}")

    if result["partial"]:
        # Data was still written. Failing loudly is deliberate: a silent
        # partial capture is a gap nobody notices until the charts look wrong.
        print("PARTIAL CAPTURE — some calls failed. See meta.json.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_snapshot_top_items.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Run a real capture locally**

With `.env` filled in from Task 3:

```bash
set -a && . ./.env && set +a && python tools/snapshot_top_items.py
```

On PowerShell:
```powershell
Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
python tools/snapshot_top_items.py
```

Expected: exit 0, and `data/raw/<today>/` containing 7 files. Inspect `meta.json` and confirm `"partial": false` with six `status: 200` entries.

If this returns empty `items` for `short_term`, that is valid — it means Spotify has not computed recent top items for the account yet.

- [ ] **Step 6: Commit**

```bash
git add tools/snapshot_top_items.py tests/test_snapshot_top_items.py data/raw
git commit -m "feat: capture daily top artists and tracks into raw layer"
```

---

### Task 5: Daily GitHub Action — milestone 1 complete

Gets capture running unattended. **This is the urgent task.** Every day before this lands is a data point that can never be recovered.

**Files:**
- Create: `.github/workflows/snapshot.yml`
- Create: `workflows/capture_snapshot.md`

**Interfaces:**
- Consumes: `tools/snapshot_top_items.py` entry point, the three repo secrets
- Produces: a daily commit to `main` containing `data/raw/<date>/`

- [ ] **Step 1: Add the three repo secrets**

```bash
gh secret set SPOTIFY_CLIENT_ID
gh secret set SPOTIFY_CLIENT_SECRET
gh secret set SPOTIFY_REFRESH_TOKEN
```

Each prompts for the value. Verify with `gh secret list` — expect exactly three.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/snapshot.yml`:

```yaml
name: Daily snapshot

on:
  schedule:
    # 06:00 UTC daily. GitHub schedules are best-effort and can be delayed or
    # dropped under load; daily cadence is what makes a dropped run harmless.
    - cron: "0 6 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: snapshot
  cancel-in-progress: false

jobs:
  snapshot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - name: Capture
        id: capture
        env:
          SPOTIFY_CLIENT_ID: ${{ secrets.SPOTIFY_CLIENT_ID }}
          SPOTIFY_CLIENT_SECRET: ${{ secrets.SPOTIFY_CLIENT_SECRET }}
          SPOTIFY_REFRESH_TOKEN: ${{ secrets.SPOTIFY_REFRESH_TOKEN }}
        run: python tools/snapshot_top_items.py

      - name: Commit whatever was captured
        # always(): a partial capture still wrote real data worth keeping, and
        # the step still reports failure so the notification email goes out.
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A data
          git diff --staged --quiet || git commit -m "data: snapshot $(date -u +%F)"
          git push
```

- [ ] **Step 3: Trigger it manually and confirm it works**

```bash
git add .github/workflows/snapshot.yml
git commit -m "ci: add daily snapshot workflow"
git push
gh workflow run "Daily snapshot"
sleep 45
gh run list --workflow="Daily snapshot" --limit 1
```

Expected: the run completes. Because Task 4 already captured today locally, the capture step should print `Already captured` and the commit step should find nothing to commit. That is the correct idempotent result and proves auth works in CI.

To prove a real capture end to end, delete today's directory and re-run:

```bash
git rm -r --cached "data/raw/$(date -u +%F)" -q && rm -rf "data/raw/$(date -u +%F)"
git commit -m "test: force a fresh capture in CI" && git push
gh workflow run "Daily snapshot"
```

Expected after it finishes: `git pull` brings down a `data: snapshot <date>` commit authored by `github-actions[bot]` containing 7 files.

- [ ] **Step 4: Write the workflow SOP**

Create `workflows/capture_snapshot.md`:

```markdown
# Workflow: Capture daily snapshot

## Objective
Record today's Spotify top artists and tracks into `data/raw/<date>/` before
the data is gone. Spotify has no history endpoint — a missed day is permanent.

## Trigger
`.github/workflows/snapshot.yml`, daily at 06:00 UTC. Also `gh workflow run
"Daily snapshot"` on demand.

## Required inputs
Repo secrets `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SPOTIFY_REFRESH_TOKEN`. Locally, the same three in `.env`.

## Tool
`python tools/snapshot_top_items.py`

## Expected output
`data/raw/<YYYY-MM-DD>/` with six response files plus `meta.json`, committed
to `main`. Exit 0 on a clean capture, exit 1 on a partial one.

## Edge cases and what they mean

- **`Already captured`** — the day directory exists. Correct, not an error.
  Reruns are safe by design.
- **Exit 1 with `PARTIAL CAPTURE`** — some calls failed. The data that did
  arrive was still written and committed. Check `meta.json` for which call
  failed and its status code. No action needed unless it repeats.
- **`invalid_grant` on token refresh** — the refresh token was revoked, usually
  by a Spotify password change or removing the app's authorisation. Re-run
  `python tools/mint_refresh_token.py` and update the repo secret.
- **Empty `items`** — valid data. Spotify has not computed top items for that
  time range yet. Record it, do not treat it as failure.
- **Workflow silently stops running** — GitHub disables scheduled workflows
  after 60 days of repo inactivity, and `GITHUB_TOKEN` pushes are unreliable as
  "activity". Re-enable in the Actions tab. If it recurs, switch the push to a
  personal access token so the commits count as user activity.
```

- [ ] **Step 5: Commit**

```bash
git add workflows/capture_snapshot.md
git commit -m "docs: add capture snapshot workflow SOP"
git push
```

**Milestone 1 complete.** Data is now accumulating daily. Everything after this can be built at leisure against data that is already being collected.

---

### Task 6: Derived layer

Rebuilds tidy CSVs from the whole raw tree on every run.

**Files:**
- Create: `tools/build_derived.py`
- Test: `tests/test_build_derived.py`

**Interfaces:**
- Consumes: `data/raw/` produced by Task 4
- Produces:
  - `SNAPSHOT_COLUMNS: list[str]`
  - `GENRE_COLUMNS: list[str]`
  - `build_rows(raw_root: Path) -> tuple[list[dict], list[dict], list[dict]]` → `(snapshot_rows, genre_rows, skipped)`
  - `write_csvs(snapshot_rows, genre_rows, derived_root: Path) -> None`

`skipped` entries are `{"path": str, "reason": str}` and travel forward into `data.json` in Task 8 so the dashboard can show a data-quality banner.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_derived.py`:

```python
import csv
import json

from tools.build_derived import build_rows, write_csvs


def write_day(root, date, *, artists=None, tracks=None, partial=False, malformed=None):
    """Create a raw day directory. Only the files given are written."""
    day = root / date
    day.mkdir(parents=True)
    if artists is not None:
        (day / "top_artists_short_term.json").write_text(json.dumps({"items": artists}))
    if tracks is not None:
        (day / "top_tracks_short_term.json").write_text(json.dumps({"items": tracks}))
    if malformed:
        (day / malformed).write_text("{not json at all")
    (day / "meta.json").write_text(
        json.dumps({"captured_at": f"{date}T06:00:00Z", "partial": partial, "calls": {}})
    )
    return day


def artist(id_, name, popularity=50, genres=()):
    return {"id": id_, "name": name, "popularity": popularity, "genres": list(genres)}


def track(id_, name, popularity=50, artist_id="a1", album_id="al1", duration_ms=1000):
    return {
        "id": id_,
        "name": name,
        "popularity": popularity,
        "duration_ms": duration_ms,
        "artists": [{"id": artist_id, "name": "Someone"}],
        "album": {"id": album_id},
    }


def test_flattens_artists_with_rank_starting_at_one(tmp_path):
    write_day(tmp_path, "2026-09-03", artists=[artist("a1", "First"), artist("a2", "Second")])

    snapshot_rows, _, _ = build_rows(tmp_path)

    assert [(r["rank"], r["name"]) for r in snapshot_rows] == [(1, "First"), (2, "Second")]
    assert snapshot_rows[0]["kind"] == "artist"  # singular in derived data
    assert snapshot_rows[0]["time_range"] == "short_term"
    assert snapshot_rows[0]["snapshot_date"] == "2026-09-03"
    assert snapshot_rows[0]["captured_at"] == "2026-09-03T06:00:00Z"


def test_track_rows_carry_primary_artist_album_and_duration(tmp_path):
    write_day(tmp_path, "2026-09-03", tracks=[track("t1", "Song")])

    snapshot_rows, _, _ = build_rows(tmp_path)

    row = snapshot_rows[0]
    assert row["kind"] == "track"
    assert row["primary_artist_id"] == "a1"
    assert row["album_id"] == "al1"
    assert row["duration_ms"] == 1000


def test_artist_rows_leave_track_only_columns_empty(tmp_path):
    write_day(tmp_path, "2026-09-03", artists=[artist("a1", "First")])

    row = build_rows(tmp_path)[0][0]

    assert row["primary_artist_id"] == ""
    assert row["album_id"] == ""
    assert row["duration_ms"] == ""


def test_emits_one_genre_row_per_artist_genre(tmp_path):
    write_day(
        tmp_path, "2026-09-03", artists=[artist("a1", "First", genres=["shoegaze", "dream pop"])]
    )

    _, genre_rows, _ = build_rows(tmp_path)

    assert sorted(r["genre"] for r in genre_rows) == ["dream pop", "shoegaze"]
    assert genre_rows[0]["artist_id"] == "a1"
    assert genre_rows[0]["snapshot_date"] == "2026-09-03"


def test_artist_with_no_genres_emits_no_genre_rows(tmp_path):
    write_day(tmp_path, "2026-09-03", artists=[artist("a1", "First", genres=[])])

    _, genre_rows, _ = build_rows(tmp_path)

    assert genre_rows == []


def test_partial_day_contributes_the_files_that_exist(tmp_path):
    write_day(tmp_path, "2026-09-03", artists=[artist("a1", "First")], partial=True)

    snapshot_rows, _, skipped = build_rows(tmp_path)

    assert len(snapshot_rows) == 1
    assert skipped == []  # a missing file is expected, not a data-quality fault


def test_malformed_json_is_skipped_and_reported_without_killing_the_build(tmp_path):
    write_day(
        tmp_path,
        "2026-09-03",
        artists=[artist("a1", "First")],
        malformed="top_tracks_short_term.json",
    )

    snapshot_rows, _, skipped = build_rows(tmp_path)

    assert len(snapshot_rows) == 1  # the good file still made it through
    assert len(skipped) == 1
    assert "top_tracks_short_term.json" in skipped[0]["path"]


def test_multiple_days_are_all_included_and_sorted(tmp_path):
    write_day(tmp_path, "2026-09-05", artists=[artist("a1", "First")])
    write_day(tmp_path, "2026-09-03", artists=[artist("a1", "First")])

    snapshot_rows, _, _ = build_rows(tmp_path)

    assert [r["snapshot_date"] for r in snapshot_rows] == ["2026-09-03", "2026-09-05"]


def test_staging_directories_are_ignored(tmp_path):
    write_day(tmp_path, "2026-09-03", artists=[artist("a1", "First")])
    (tmp_path / ".2026-09-04.staging").mkdir()

    snapshot_rows, _, _ = build_rows(tmp_path)

    assert {r["snapshot_date"] for r in snapshot_rows} == {"2026-09-03"}


def test_equal_popularity_does_not_disturb_rank_order(tmp_path):
    # Rank comes from Spotify's array order, never from popularity. Two items
    # with identical popularity must keep their positions, and must land in the
    # same order on every rebuild — otherwise every run produces a noise diff.
    write_day(
        tmp_path,
        "2026-09-03",
        artists=[artist("a1", "First", popularity=42), artist("a2", "Second", popularity=42)],
    )

    first_pass, _, _ = build_rows(tmp_path)
    second_pass, _, _ = build_rows(tmp_path)

    assert [(r["rank"], r["spotify_id"]) for r in first_pass] == [(1, "a1"), (2, "a2")]
    assert first_pass == second_pass


def test_write_csvs_produces_stable_sorted_output(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_day(raw, "2026-09-03", artists=[artist("a2", "Second"), artist("a1", "First")])
    snapshot_rows, genre_rows, _ = build_rows(raw)

    derived = tmp_path / "derived"
    write_csvs(snapshot_rows, genre_rows, derived)

    with (derived / "snapshots.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["rank"] for r in rows] == ["1", "2"]
    assert (derived / "artist_genres.csv").exists()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_build_derived.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.build_derived'`

- [ ] **Step 3: Write the implementation**

Create `tools/build_derived.py`:

```python
"""Rebuild the tidy derived tables from the entire raw tree.

Full rebuild, never incremental. It costs a fraction of a second for years of
small files, and it means a fix to this parser applies retroactively to every
day ever captured rather than only to days captured after the fix.
"""

import csv
import json
from pathlib import Path

RAW_ROOT = Path("data/raw")
DERIVED_ROOT = Path("data/derived")

TIME_RANGES = ("short_term", "medium_term", "long_term")
API_KIND_TO_KIND = {"artists": "artist", "tracks": "track"}

SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "captured_at",
    "time_range",
    "kind",
    "rank",
    "spotify_id",
    "name",
    "popularity",
    "primary_artist_id",
    "album_id",
    "duration_ms",
]
GENRE_COLUMNS = ["snapshot_date", "time_range", "artist_id", "genre"]


def build_rows(raw_root):
    """Walk every captured day. Returns (snapshot_rows, genre_rows, skipped)."""
    raw_root = Path(raw_root)
    snapshot_rows, genre_rows, skipped = [], [], []

    day_dirs = sorted(
        path
        for path in raw_root.iterdir()
        # Skip staging dirs from a crashed capture and any stray files.
        if path.is_dir() and not path.name.startswith(".")
    )

    for day_dir in day_dirs:
        snapshot_date = day_dir.name
        captured_at = _read_captured_at(day_dir, snapshot_date)

        for api_kind, kind in API_KIND_TO_KIND.items():
            for time_range in TIME_RANGES:
                path = day_dir / f"top_{api_kind}_{time_range}.json"
                if not path.exists():
                    continue  # partial capture: expected, not a fault
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    # One corrupt file must not block every future build, but
                    # it must be visible — this surfaces in the dashboard.
                    skipped.append({"path": str(path), "reason": str(error)})
                    continue

                for index, item in enumerate(payload.get("items", []), start=1):
                    snapshot_rows.append(
                        _item_row(item, index, kind, time_range, snapshot_date, captured_at)
                    )
                    if kind == "artist":
                        for genre in item.get("genres", []):
                            genre_rows.append(
                                {
                                    "snapshot_date": snapshot_date,
                                    "time_range": time_range,
                                    "artist_id": item.get("id", ""),
                                    "genre": genre,
                                }
                            )

    snapshot_rows.sort(key=lambda r: (r["snapshot_date"], r["time_range"], r["kind"], r["rank"]))
    genre_rows.sort(key=lambda r: (r["snapshot_date"], r["time_range"], r["artist_id"], r["genre"]))
    return snapshot_rows, genre_rows, skipped


def _read_captured_at(day_dir, snapshot_date):
    meta_path = day_dir / "meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("captured_at", "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return f"{snapshot_date}T00:00:00Z"


def _item_row(item, rank, kind, time_range, snapshot_date, captured_at):
    row = {
        "snapshot_date": snapshot_date,
        "captured_at": captured_at,
        "time_range": time_range,
        "kind": kind,
        "rank": rank,
        "spotify_id": item.get("id", ""),
        "name": item.get("name", ""),
        "popularity": item.get("popularity", ""),
        "primary_artist_id": "",
        "album_id": "",
        "duration_ms": "",
    }
    if kind == "track":
        artists = item.get("artists") or [{}]
        row["primary_artist_id"] = artists[0].get("id", "")
        row["album_id"] = (item.get("album") or {}).get("id", "")
        row["duration_ms"] = item.get("duration_ms", "")
    return row


def write_csvs(snapshot_rows, genre_rows, derived_root):
    derived_root = Path(derived_root)
    derived_root.mkdir(parents=True, exist_ok=True)
    _write_csv(derived_root / "snapshots.csv", SNAPSHOT_COLUMNS, snapshot_rows)
    _write_csv(derived_root / "artist_genres.csv", GENRE_COLUMNS, genre_rows)


def _write_csv(path, columns, rows):
    # newline="" and \n are what keep diffs clean across Windows and Linux.
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    snapshot_rows, genre_rows, skipped = build_rows(RAW_ROOT)
    write_csvs(snapshot_rows, genre_rows, DERIVED_ROOT)
    print(f"{len(snapshot_rows)} snapshot rows, {len(genre_rows)} genre rows")
    for entry in skipped:
        print(f"SKIPPED {entry['path']}: {entry['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_build_derived.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Run it against real captured data**

Run: `python tools/build_derived.py`
Expected: a row count printed, no `SKIPPED` lines, and `data/derived/snapshots.csv` plus `data/derived/artist_genres.csv` created. Open `snapshots.csv` and confirm the ranks run 1–50 per group.

- [ ] **Step 6: Commit**

```bash
git add tools/build_derived.py tests/test_build_derived.py data/derived
git commit -m "feat: build tidy derived tables from raw snapshots"
```

---

### Task 7: Core metrics

Entry/exit events, divergence, mainstream-ness, genre mix, rank timeline. Everything that works from day one.

**Files:**
- Create: `tools/build_metrics.py`
- Test: `tests/test_build_metrics.py`

**Interfaces:**
- Consumes: `tools.build_derived.SNAPSHOT_COLUMNS`, `GENRE_COLUMNS`
- Produces:
  - `RANK_WEIGHT: Callable[[int], float]`
  - `load_rows(derived_root: Path) -> tuple[list[dict], list[dict]]`
  - `rank_timeline(snapshot_rows) -> dict`
  - `entry_exit_events(snapshot_rows) -> list[dict]`
  - `divergence(snapshot_rows) -> list[dict]`
  - `mainstreamness(snapshot_rows) -> list[dict]`
  - `genre_mix(genre_rows, snapshot_rows) -> list[dict]`

Task 8 adds the gated metrics and the `build()` assembler to this same module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_metrics.py`:

```python
from tools.build_metrics import (
    divergence,
    entry_exit_events,
    genre_mix,
    mainstreamness,
    rank_timeline,
)


def row(date, id_, rank, *, kind="artist", time_range="short_term", popularity=50, name=None):
    return {
        "snapshot_date": date,
        "time_range": time_range,
        "kind": kind,
        "rank": rank,
        "spotify_id": id_,
        "name": name or id_.upper(),
        "popularity": popularity,
    }


def genre_row(date, artist_id, genre, time_range="short_term"):
    return {
        "snapshot_date": date,
        "time_range": time_range,
        "artist_id": artist_id,
        "genre": genre,
    }


# --- entry / exit -----------------------------------------------------------

def test_first_snapshot_emits_no_entry_events():
    events = entry_exit_events([row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)])

    assert events == []


def test_detects_entry_and_exit_between_consecutive_snapshots():
    rows = [
        row("2026-09-01", "a1", 1),
        row("2026-09-02", "a2", 1),
    ]

    events = entry_exit_events(rows)

    assert {(e["spotify_id"], e["event"]) for e in events} == {
        ("a2", "entered"),
        ("a1", "exited"),
    }


def test_reentry_is_labelled_distinctly_from_a_first_entry():
    rows = [
        row("2026-09-01", "a1", 1),
        row("2026-09-02", "a2", 1),
        row("2026-09-03", "a1", 1),
    ]

    events = entry_exit_events(rows)
    reentry = [e for e in events if e["snapshot_date"] == "2026-09-03" and e["spotify_id"] == "a1"]

    assert reentry[0]["event"] == "re_entered"


def test_gap_in_snapshot_dates_is_treated_as_consecutive():
    # Consecutive AVAILABLE snapshots, not consecutive calendar days.
    rows = [row("2026-09-01", "a1", 1), row("2026-09-20", "a2", 1)]

    events = entry_exit_events(rows)

    assert len(events) == 2


def test_entry_exit_is_scoped_per_kind_and_time_range():
    rows = [
        row("2026-09-01", "a1", 1, kind="artist"),
        row("2026-09-01", "t1", 1, kind="track"),
        row("2026-09-02", "a1", 1, kind="artist"),
        row("2026-09-02", "t2", 1, kind="track"),
    ]

    events = entry_exit_events(rows)

    assert all(e["kind"] == "track" for e in events)


# --- divergence -------------------------------------------------------------

def test_identical_short_and_long_lists_give_divergence_of_one():
    rows = [
        row("2026-09-01", "a1", 1, time_range="short_term"),
        row("2026-09-01", "a1", 1, time_range="long_term"),
    ]

    assert divergence(rows)[0]["overlap"] == 1.0


def test_disjoint_short_and_long_lists_give_divergence_of_zero():
    rows = [
        row("2026-09-01", "a1", 1, time_range="short_term"),
        row("2026-09-01", "a9", 1, time_range="long_term"),
    ]

    assert divergence(rows)[0]["overlap"] == 0.0


def test_overlap_divides_by_the_smaller_set_not_a_hardcoded_fifty():
    rows = [
        row("2026-09-01", "a1", 1, time_range="short_term"),
        row("2026-09-01", "a2", 2, time_range="short_term"),
        row("2026-09-01", "a1", 1, time_range="long_term"),
    ]

    # long_term has 1 item, of which 1 overlaps -> 1.0, not 0.5
    assert divergence(rows)[0]["overlap"] == 1.0


def test_divergence_skipped_when_a_time_range_is_missing_entirely():
    rows = [row("2026-09-01", "a1", 1, time_range="short_term")]

    assert divergence(rows) == []


# --- mainstreamness ---------------------------------------------------------

def test_mainstreamness_reports_mean_and_median_popularity():
    rows = [
        row("2026-09-01", "a1", 1, popularity=10),
        row("2026-09-01", "a2", 2, popularity=20),
        row("2026-09-01", "a3", 3, popularity=60),
    ]

    result = mainstreamness(rows)[0]

    assert result["mean"] == 30.0
    assert result["median"] == 20.0


# --- genre mix --------------------------------------------------------------

def test_genre_shares_sum_to_one():
    snapshot_rows = [row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock"), genre_row("2026-09-01", "a2", "jazz")]

    shares = genre_mix(genre_rows, snapshot_rows)

    assert round(sum(s["share"] for s in shares), 9) == 1.0


def test_rank_one_outweighs_rank_two_by_the_inverse_rank_rule():
    snapshot_rows = [row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock"), genre_row("2026-09-01", "a2", "jazz")]

    shares = {s["genre"]: s["share"] for s in genre_mix(genre_rows, snapshot_rows)}

    # weights 1/1 and 1/2 -> 2/3 and 1/3
    assert round(shares["rock"], 6) == round(2 / 3, 6)
    assert round(shares["jazz"], 6) == round(1 / 3, 6)


def test_an_artists_weight_is_split_evenly_across_its_genres():
    snapshot_rows = [row("2026-09-01", "a1", 1)]
    genre_rows = [
        genre_row("2026-09-01", "a1", "rock"),
        genre_row("2026-09-01", "a1", "jazz"),
    ]

    shares = {s["genre"]: s["share"] for s in genre_mix(genre_rows, snapshot_rows)}

    assert shares["rock"] == 0.5
    assert shares["jazz"] == 0.5


def test_artist_with_no_genres_is_counted_as_unclassified():
    snapshot_rows = [row("2026-09-01", "a1", 1)]

    shares = {s["genre"]: s["share"] for s in genre_mix([], snapshot_rows)}

    assert shares["unclassified"] == 1.0


def test_genre_mix_ignores_tracks():
    snapshot_rows = [row("2026-09-01", "t1", 1, kind="track")]

    assert genre_mix([], snapshot_rows) == []


# --- rank timeline ----------------------------------------------------------

def test_rank_timeline_is_nested_by_kind_then_time_range():
    rows = [row("2026-09-01", "a1", 3, name="Alpha")]

    timeline = rank_timeline(rows)

    assert timeline["artist"]["short_term"] == [
        {"date": "2026-09-01", "id": "a1", "name": "Alpha", "rank": 3}
    ]
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_build_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.build_metrics'`

- [ ] **Step 3: Write the implementation**

Create `tools/build_metrics.py`:

```python
"""Compute dashboard metrics from the derived tables.

Every function here is pure: it takes rows and returns data. That is what makes
the whole analysis replayable — improve a metric, rerun, and the new definition
applies to all history rather than only to days captured after the change.
"""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

DERIVED_ROOT = Path("data/derived")
SITE_ROOT = Path("site")

TIME_RANGES = ("short_term", "medium_term", "long_term")
UNCLASSIFIED = "unclassified"

# Inverse rank. Without it the 45 artists in the tail outweigh the top 5, and
# the genre chart stops reflecting what you actually listen to.
RANK_WEIGHT = lambda rank: 1.0 / rank  # noqa: E731


def load_rows(derived_root):
    """Read both derived CSVs, coercing the numeric columns."""
    derived_root = Path(derived_root)
    snapshot_rows = _read_csv(derived_root / "snapshots.csv")
    for row in snapshot_rows:
        row["rank"] = int(row["rank"])
        row["popularity"] = int(row["popularity"]) if row["popularity"] != "" else None
    return snapshot_rows, _read_csv(derived_root / "artist_genres.csv")


def _read_csv(path):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _group_by_series(snapshot_rows):
    """Index rows as {(kind, time_range): {date: {id: row}}}."""
    grouped = defaultdict(lambda: defaultdict(dict))
    for row in snapshot_rows:
        grouped[(row["kind"], row["time_range"])][row["snapshot_date"]][row["spotify_id"]] = row
    return grouped


def rank_timeline(snapshot_rows):
    """{kind: {time_range: [{date, id, name, rank}, ...]}}"""
    timeline = defaultdict(lambda: defaultdict(list))
    for row in sorted(
        snapshot_rows, key=lambda r: (r["kind"], r["time_range"], r["snapshot_date"], r["rank"])
    ):
        timeline[row["kind"]][row["time_range"]].append(
            {
                "date": row["snapshot_date"],
                "id": row["spotify_id"],
                "name": row["name"],
                "rank": row["rank"],
            }
        )
    return {kind: dict(ranges) for kind, ranges in timeline.items()}


def entry_exit_events(snapshot_rows):
    """Entries, exits and re-entries between consecutive AVAILABLE snapshots."""
    events = []
    for (kind, time_range), by_date in _group_by_series(snapshot_rows).items():
        dates = sorted(by_date)
        seen_ever = set(by_date[dates[0]]) if dates else set()

        for previous_date, date in zip(dates, dates[1:]):
            previous_ids = set(by_date[previous_date])
            current_ids = set(by_date[date])

            for spotify_id in sorted(current_ids - previous_ids):
                events.append(
                    _event(
                        date,
                        kind,
                        time_range,
                        by_date[date][spotify_id],
                        "re_entered" if spotify_id in seen_ever else "entered",
                    )
                )
            for spotify_id in sorted(previous_ids - current_ids):
                events.append(
                    _event(
                        date, kind, time_range, by_date[previous_date][spotify_id], "exited"
                    )
                )
            seen_ever |= current_ids
    events.sort(key=lambda e: (e["snapshot_date"], e["kind"], e["time_range"], e["spotify_id"]))
    return events


def _event(date, kind, time_range, row, event):
    return {
        "snapshot_date": date,
        "kind": kind,
        "time_range": time_range,
        "spotify_id": row["spotify_id"],
        "name": row["name"],
        "event": event,
    }


def divergence(snapshot_rows):
    """Overlap between short_term and long_term, per date and kind."""
    by_kind_date = defaultdict(lambda: defaultdict(set))
    for row in snapshot_rows:
        if row["time_range"] in ("short_term", "long_term"):
            key = (row["kind"], row["snapshot_date"])
            by_kind_date[key][row["time_range"]].add(row["spotify_id"])

    results = []
    for (kind, date), ranges in by_kind_date.items():
        short, long = ranges.get("short_term"), ranges.get("long_term")
        if not short or not long:
            continue  # one side missing: no meaningful comparison
        results.append(
            {
                "date": date,
                "kind": kind,
                "overlap": len(short & long) / min(len(short), len(long)),
            }
        )
    results.sort(key=lambda r: (r["date"], r["kind"]))
    return results


def mainstreamness(snapshot_rows):
    """Mean and median popularity per date, kind and time range."""
    grouped = defaultdict(list)
    for row in snapshot_rows:
        if row.get("popularity") is not None:
            grouped[(row["snapshot_date"], row["kind"], row["time_range"])].append(
                row["popularity"]
            )

    results = [
        {
            "date": date,
            "kind": kind,
            "time_range": time_range,
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
        }
        for (date, kind, time_range), values in grouped.items()
        if values
    ]
    results.sort(key=lambda r: (r["date"], r["kind"], r["time_range"]))
    return results


def genre_mix(genre_rows, snapshot_rows):
    """Rank-weighted genre shares per date and time range, normalised to 1."""
    genres_by_artist = defaultdict(lambda: defaultdict(list))
    for row in genre_rows:
        genres_by_artist[(row["snapshot_date"], row["time_range"])][row["artist_id"]].append(
            row["genre"]
        )

    weights = defaultdict(lambda: defaultdict(float))
    for row in snapshot_rows:
        if row["kind"] != "artist":
            continue
        key = (row["snapshot_date"], row["time_range"])
        weight = RANK_WEIGHT(row["rank"])
        genres = genres_by_artist[key].get(row["spotify_id"]) or [UNCLASSIFIED]
        for genre in genres:
            weights[key][genre] += weight / len(genres)

    results = []
    for (date, time_range), by_genre in weights.items():
        total = sum(by_genre.values())
        if not total:
            continue
        for genre, weight in by_genre.items():
            results.append(
                {
                    "date": date,
                    "time_range": time_range,
                    "genre": genre,
                    "share": weight / total,
                }
            )
    results.sort(key=lambda r: (r["date"], r["time_range"], r["genre"]))
    return results
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `python -m pytest tests/test_build_metrics.py -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Commit**

```bash
git add tools/build_metrics.py tests/test_build_metrics.py
git commit -m "feat: compute entry/exit, divergence, mainstreamness and genre mix"
```

---

### Task 8: Gated metrics and the data.json assembler

Survival curve and rotation half-life, both of which need months of history, plus the `build()` function that assembles `site/data.json`.

**Files:**
- Modify: `tools/build_metrics.py` (append; do not rewrite Task 7's functions)
- Test: `tests/test_build_metrics_gated.py`

**Interfaces:**
- Consumes: everything Task 7 produced
- Produces:
  - `SURVIVAL_MIN_WEEKS = 8`
  - `HALFLIFE_MIN_SPELLS = 10`
  - `new_artist_survival(snapshot_rows) -> dict`
  - `rotation_half_life(snapshot_rows) -> dict`
  - `build(snapshot_rows, genre_rows, skipped, generated_at) -> dict`
  - `main() -> int`

**`site/data.json` contract** — Task 9 consumes exactly this shape:

```json
{
  "generated_at": "2026-09-03T06:00:12Z",
  "snapshot_dates": ["2026-09-03"],
  "data_quality": {"skipped_files": [], "snapshot_count": 1},
  "headline": {"divergence_artists": 0.42, "mean_popularity": 61.2, "entries_7d": 3, "exits_7d": 4},
  "rank_timeline": {"artist": {"short_term": []}, "track": {"short_term": []}},
  "events": [],
  "divergence": [],
  "mainstreamness": [],
  "genre_mix": [],
  "survival": {"available": false, "weeks_needed": 8, "weeks_have": 2, "curve": []},
  "half_life": {"available": false, "spells_needed": 10, "spells_have": 0, "median_days": null}
}
```

`weeks_have` and `spells_have` exist so the dashboard can say *how much* more
history is needed rather than only that some is. Task 9 reads both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_metrics_gated.py`:

```python
import datetime as dt

from tools.build_metrics import (
    HALFLIFE_MIN_SPELLS,
    build,
    new_artist_survival,
    rotation_half_life,
)


def row(date, id_, rank=1, *, kind="artist", time_range="short_term", popularity=50):
    return {
        "snapshot_date": date,
        "time_range": time_range,
        "kind": kind,
        "rank": rank,
        "spotify_id": id_,
        "name": id_.upper(),
        "popularity": popularity,
    }


def weekly_dates(count, start="2026-01-05"):
    first = dt.date.fromisoformat(start)
    return [(first + dt.timedelta(weeks=index)).isoformat() for index in range(count)]


def test_survival_is_gated_until_enough_history_exists():
    rows = [row(date, "a1") for date in weekly_dates(4)]

    result = new_artist_survival(rows)

    assert result["available"] is False
    assert result["weeks_needed"] == 8
    assert result["curve"] == []


def test_survival_becomes_available_past_the_threshold():
    rows = [row(date, "a1") for date in weekly_dates(10)]

    assert new_artist_survival(rows)["available"] is True


def test_survival_curve_reports_the_fraction_still_present():
    dates = weekly_dates(10)
    rows = [row(date, "a1") for date in dates]
    # a2 joins the same first cohort but leaves after week 2.
    rows += [row(date, "a2", rank=2) for date in dates[:3]]

    curve = {point["week"]: point["fraction"] for point in new_artist_survival(rows)["curve"]}

    assert curve[0] == 1.0
    assert curve[2] == 1.0   # both still present
    assert curve[3] == 0.5   # only a1 survives


def test_half_life_is_gated_until_enough_completed_spells():
    rows = [row("2026-09-01", "a1"), row("2026-09-02", "a2")]

    result = rotation_half_life(rows)

    assert result["available"] is False
    assert result["spells_needed"] == HALFLIFE_MIN_SPELLS
    assert result["median_days"] is None


def test_half_life_reports_median_completed_spell_length():
    rows = []
    dates = weekly_dates(30)
    # 11 artists, each present for exactly two consecutive weekly snapshots.
    # a0 is present in the very first snapshot, so its start date is unknown and
    # it yields no spell — hence 11 artists to produce the 10 spells needed.
    for index in range(HALFLIFE_MIN_SPELLS + 1):
        for date in dates[index : index + 2]:
            rows.append(row(date, f"a{index}"))
    # An anchor present throughout, so no snapshot date is ever empty.
    rows += [row(date, "anchor", rank=50) for date in dates]

    result = rotation_half_life(rows)

    assert result["available"] is True
    assert result["median_days"] == 14  # entered week N, gone by week N+2


def test_half_life_ignores_artists_still_in_rotation():
    # An artist that entered and never left has no completed spell to measure.
    dates = weekly_dates(30)
    rows = [row(date, "a1") for date in dates]

    assert rotation_half_life(rows)["available"] is False


def test_build_emits_every_key_the_dashboard_reads():
    rows = [row("2026-09-01", "a1"), row("2026-09-01", "t1", kind="track")]

    payload = build(rows, [], [], "2026-09-01T06:00:00Z")

    assert set(payload) == {
        "generated_at",
        "snapshot_dates",
        "data_quality",
        "headline",
        "rank_timeline",
        "events",
        "divergence",
        "mainstreamness",
        "genre_mix",
        "survival",
        "half_life",
    }
    assert payload["generated_at"] == "2026-09-01T06:00:00Z"
    assert payload["snapshot_dates"] == ["2026-09-01"]


def test_build_carries_skipped_files_into_data_quality():
    skipped = [{"path": "data/raw/2026-09-01/top_tracks_short_term.json", "reason": "boom"}]

    payload = build([row("2026-09-01", "a1")], [], skipped, "2026-09-01T06:00:00Z")

    assert payload["data_quality"]["skipped_files"] == skipped


def test_build_survives_a_completely_empty_dataset():
    payload = build([], [], [], "2026-09-01T06:00:00Z")

    assert payload["snapshot_dates"] == []
    assert payload["headline"]["divergence_artists"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest tests/test_build_metrics_gated.py -v`
Expected: FAIL — `ImportError: cannot import name 'build' from 'tools.build_metrics'`

- [ ] **Step 3: Append the implementation to `tools/build_metrics.py`**

Add these imports at the top of the file, alongside the existing ones:

```python
import datetime as dt
import json
```

Then append to the end of `tools/build_metrics.py`:

```python
SURVIVAL_MIN_WEEKS = 8
HALFLIFE_MIN_SPELLS = 10


def _short_term_artist_presence(snapshot_rows):
    """{date: {artist_id}} for short_term artists only, plus sorted dates."""
    by_date = defaultdict(set)
    for row in snapshot_rows:
        if row["kind"] == "artist" and row["time_range"] == "short_term":
            by_date[row["snapshot_date"]].add(row["spotify_id"])
    return by_date, sorted(by_date)


def new_artist_survival(snapshot_rows):
    """Retention curve for the first cohort of artists to appear."""
    by_date, dates = _short_term_artist_presence(snapshot_rows)
    weeks_span = _weeks_between(dates[0], dates[-1]) if len(dates) > 1 else 0

    if weeks_span < SURVIVAL_MIN_WEEKS:
        return {
            "available": False,
            "weeks_needed": SURVIVAL_MIN_WEEKS,
            "weeks_have": weeks_span,
            "curve": [],
        }

    cohort = by_date[dates[0]]
    if not cohort:
        return {
            "available": False,
            "weeks_needed": SURVIVAL_MIN_WEEKS,
            "weeks_have": weeks_span,
            "curve": [],
        }

    curve = [
        {
            "week": _weeks_between(dates[0], date),
            "fraction": len(cohort & by_date[date]) / len(cohort),
        }
        for date in dates
    ]
    return {
        "available": True,
        "weeks_needed": SURVIVAL_MIN_WEEKS,
        "weeks_have": weeks_span,
        "curve": curve,
    }


def rotation_half_life(snapshot_rows):
    """Median days a short_term artist survives, over completed spells only."""
    by_date, dates = _short_term_artist_presence(snapshot_rows)

    spells = []
    active = {}  # artist_id -> the date we observed them enter
    previous = set()
    for index, date in enumerate(dates):
        present = by_date[date]
        if index == 0:
            # The first snapshot is a starting state, not an observed entry.
            # We do not know when those artists arrived, so they can never
            # produce a spell — counting them would bias the median downward.
            previous = present
            continue
        for artist_id in present - previous:
            active[artist_id] = date
        for artist_id in previous - present:
            if artist_id in active:
                spells.append(_days_between(active.pop(artist_id), date))
        previous = present

    if len(spells) < HALFLIFE_MIN_SPELLS:
        return {
            "available": False,
            "spells_needed": HALFLIFE_MIN_SPELLS,
            "spells_have": len(spells),
            "median_days": None,
        }
    return {
        "available": True,
        "spells_needed": HALFLIFE_MIN_SPELLS,
        "spells_have": len(spells),
        "median_days": statistics.median(spells),
    }


def _days_between(start, end):
    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days


def _weeks_between(start, end):
    return _days_between(start, end) // 7


def build(snapshot_rows, genre_rows, skipped, generated_at):
    """Assemble the full dashboard payload."""
    dates = sorted({row["snapshot_date"] for row in snapshot_rows})
    events = entry_exit_events(snapshot_rows)
    divergences = divergence(snapshot_rows)
    popularity = mainstreamness(snapshot_rows)

    return {
        "generated_at": generated_at,
        "snapshot_dates": dates,
        "data_quality": {"skipped_files": skipped, "snapshot_count": len(dates)},
        "headline": _headline(dates, events, divergences, popularity),
        "rank_timeline": rank_timeline(snapshot_rows),
        "events": events,
        "divergence": divergences,
        "mainstreamness": popularity,
        "genre_mix": genre_mix(genre_rows, snapshot_rows),
        "survival": new_artist_survival(snapshot_rows),
        "half_life": rotation_half_life(snapshot_rows),
    }


def _headline(dates, events, divergences, popularity):
    latest_divergence = [d for d in divergences if d["kind"] == "artist"]
    latest_popularity = [
        p for p in popularity if p["kind"] == "artist" and p["time_range"] == "short_term"
    ]
    cutoff = _cutoff_date(dates)
    recent = [e for e in events if e["snapshot_date"] > cutoff and e["kind"] == "artist"]

    return {
        "divergence_artists": latest_divergence[-1]["overlap"] if latest_divergence else None,
        "mean_popularity": latest_popularity[-1]["mean"] if latest_popularity else None,
        "entries_7d": sum(1 for e in recent if e["event"] in ("entered", "re_entered")),
        "exits_7d": sum(1 for e in recent if e["event"] == "exited"),
    }


def _cutoff_date(dates):
    if not dates:
        return ""
    return (dt.date.fromisoformat(dates[-1]) - dt.timedelta(days=7)).isoformat()


def main():
    snapshot_rows, genre_rows = load_rows(DERIVED_ROOT)
    _, _, skipped = _skipped_from_raw()
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = build(snapshot_rows, genre_rows, skipped, generated_at)
    SITE_ROOT.mkdir(parents=True, exist_ok=True)
    (SITE_ROOT / "data.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote site/data.json — {len(payload['snapshot_dates'])} snapshots")
    if skipped:
        print(f"WARNING: {len(skipped)} unreadable raw files, see data_quality in data.json")
    return 0


def _skipped_from_raw():
    # build_derived is the only thing that reads raw, so ask it what it skipped.
    from tools.build_derived import RAW_ROOT, build_rows

    return build_rows(RAW_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS, all tests across all files

- [ ] **Step 5: Generate real data.json**

Run:
```bash
python tools/build_derived.py && python tools/build_metrics.py
```
Expected: `site/data.json` written. Confirm `survival.available` and `half_life.available` are both `false` — correct with only a few days of history.

- [ ] **Step 6: Commit**

```bash
git add tools/build_metrics.py tests/test_build_metrics_gated.py site/data.json
git commit -m "feat: add gated survival and half-life metrics, assemble data.json"
```

---

### Task 9: Dashboard

Static page rendering `data.json`. No build step, no framework.

**Files:**
- Create: `site/index.html`
- Create: `site/app.js`
- Create: `site/style.css`
- Test: manual, per steps below

**Interfaces:**
- Consumes: `site/data.json` exactly as specified in Task 8
- Produces: a static site rooted at `site/`

- [ ] **Step 1: Confirm the CDN URL resolves before depending on it**

Run:
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/dist/plot.umd.min.js
```
Expected: `200`. If it is not 200, run `curl -sS https://data.jsdelivr.com/v1/package/npm/@observablehq/plot | head -20` to list published versions and substitute the newest `0.6.x` throughout this task. Pin an exact version — never `@latest`.

- [ ] **Step 2: Write the page**

Create `site/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Taste Drift</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <h1>Taste Drift</h1>
    <p id="subtitle">Loading…</p>
  </header>

  <div id="quality" class="banner" hidden></div>

  <section id="headline" class="tiles"></section>

  <section>
    <h2>Rank over time</h2>
    <div class="controls">
      <label>Kind
        <select id="kind"><option value="artist">Artists</option><option value="track">Tracks</option></select>
      </label>
      <label>Range
        <select id="range">
          <option value="short_term">Last 4 weeks</option>
          <option value="medium_term">Last 6 months</option>
          <option value="long_term">Last year</option>
        </select>
      </label>
    </div>
    <div id="timeline" class="chart"></div>
  </section>

  <section>
    <h2>Genre mix</h2>
    <div id="genres" class="chart"></div>
  </section>

  <section>
    <h2>Short vs long divergence</h2>
    <p class="note">1.0 means your recent listening matches your long-term taste. Lower means you are exploring.</p>
    <div id="divergence" class="chart"></div>
  </section>

  <section>
    <h2>Mainstream-ness</h2>
    <div id="popularity" class="chart"></div>
  </section>

  <section>
    <h2>New artist survival</h2>
    <div id="survival" class="chart"></div>
  </section>

  <section>
    <h2>Rotation half-life</h2>
    <div id="halflife" class="chart"></div>
  </section>

  <section>
    <h2>Recent changes</h2>
    <ul id="events" class="events"></ul>
  </section>

  <script src="https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/dist/plot.umd.min.js"></script>
  <script src="app.js"></script>
</body>
</html>
```

Create `site/style.css`:

```css
:root {
  color-scheme: light dark;
  --bg: #fbfbfa;
  --fg: #1a1a19;
  --muted: #6b6b68;
  --line: #e3e3e0;
  --accent: #2f6f4f;
  --warn: #8a5a1a;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #16171a; --fg: #ececea; --muted: #9a9a97; --line: #2c2e33; --accent: #7fc3a0; --warn: #d7a45c; }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 2rem 1.25rem 5rem; max-width: 60rem;
  background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
header { border-bottom: 1px solid var(--line); padding-bottom: 1rem; margin-bottom: 2rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; letter-spacing: -0.01em; }
h2 { font-size: 1.05rem; margin: 2.5rem 0 .75rem; font-weight: 600; }
#subtitle, .note { color: var(--muted); margin: 0 0 .5rem; font-size: .875rem; }
.banner {
  border: 1px solid var(--warn); border-radius: 6px; padding: .75rem 1rem;
  margin-bottom: 1.5rem; color: var(--warn); font-size: .875rem;
}
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .75rem; }
.tile { border: 1px solid var(--line); border-radius: 8px; padding: .85rem 1rem; }
.tile .value { font-size: 1.5rem; font-weight: 600; letter-spacing: -0.02em; }
.tile .label { color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }
.controls { display: flex; gap: 1rem; margin-bottom: .75rem; font-size: .875rem; }
.chart { overflow-x: auto; min-height: 2rem; }
.pending { color: var(--muted); font-style: italic; font-size: .875rem; }
.events { list-style: none; padding: 0; font-size: .875rem; }
.events li { border-bottom: 1px solid var(--line); padding: .4rem 0; display: flex; gap: .75rem; }
.events time { color: var(--muted); min-width: 6rem; }
.entered { color: var(--accent); }
.exited { color: var(--muted); }
```

Create `site/app.js`:

```js
const $ = (id) => document.getElementById(id);

function tile(label, value) {
  return `<div class="tile"><div class="value">${value}</div><div class="label">${label}</div></div>`;
}

function pending(node, message) {
  node.innerHTML = `<p class="pending">${message}</p>`;
}

function render(data) {
  const dates = data.snapshot_dates;
  $("subtitle").textContent = dates.length
    ? `${dates.length} snapshots, ${dates[0]} to ${dates[dates.length - 1]}`
    : "No snapshots yet.";

  const skipped = data.data_quality.skipped_files;
  if (skipped.length) {
    const quality = $("quality");
    quality.hidden = false;
    quality.textContent =
      `${skipped.length} raw file(s) could not be read and were excluded: ` +
      skipped.map((entry) => entry.path).join(", ");
  }

  const head = data.headline;
  $("headline").innerHTML = [
    tile("Divergence", head.divergence_artists === null ? "—" : head.divergence_artists.toFixed(2)),
    tile("Mean popularity", head.mean_popularity === null ? "—" : head.mean_popularity.toFixed(1)),
    tile("Entries, 7d", head.entries_7d),
    tile("Exits, 7d", head.exits_7d),
  ].join("");

  drawTimeline(data);
  $("kind").onchange = () => drawTimeline(data);
  $("range").onchange = () => drawTimeline(data);

  drawGenres(data.genre_mix);
  drawLine($("divergence"), data.divergence.filter((d) => d.kind === "artist"), "overlap", [0, 1]);
  drawLine(
    $("popularity"),
    data.mainstreamness.filter((m) => m.kind === "artist" && m.time_range === "short_term"),
    "mean",
    [0, 100]
  );
  drawSurvival(data.survival);
  drawHalfLife(data.half_life);
  drawEvents(data.events);
}

function drawTimeline(data) {
  const node = $("timeline");
  const rows = (data.rank_timeline[$("kind").value] || {})[$("range").value] || [];
  if (!rows.length) return pending(node, "No data for this selection yet.");

  node.replaceChildren(
    Plot.plot({
      height: 460,
      marginLeft: 40,
      marginRight: 140,
      y: { reverse: true, label: "rank", domain: [1, 50] },
      x: { label: null, type: "utc" },
      marks: [
        Plot.line(rows, { x: (d) => new Date(d.date), y: "rank", z: "id", strokeOpacity: 0.55 }),
        Plot.text(
          rows.filter((d) => d.date === data.snapshot_dates[data.snapshot_dates.length - 1]),
          { x: (d) => new Date(d.date), y: "rank", text: "name", dx: 6, textAnchor: "start", fontSize: 10 }
        ),
      ],
    })
  );
}

function drawGenres(rows) {
  const node = $("genres");
  const shortTerm = rows.filter((r) => r.time_range === "short_term");
  if (!shortTerm.length) return pending(node, "No genre data yet.");

  // Keep the 12 genres with the highest mean share; bucket the rest as "other".
  const totals = new Map();
  for (const row of shortTerm) totals.set(row.genre, (totals.get(row.genre) || 0) + row.share);
  const top = new Set([...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map((e) => e[0]));

  const folded = new Map();
  for (const row of shortTerm) {
    const genre = top.has(row.genre) ? row.genre : "other";
    const key = `${row.date}|${genre}`;
    folded.set(key, (folded.get(key) || 0) + row.share);
  }
  const series = [...folded.entries()].map(([key, share]) => {
    const [date, genre] = key.split("|");
    return { date, genre, share };
  });

  node.replaceChildren(
    Plot.plot({
      height: 320,
      marginRight: 110,
      y: { label: "share", percent: true },
      x: { label: null, type: "utc" },
      color: { legend: true },
      marks: [Plot.areaY(series, { x: (d) => new Date(d.date), y: "share", fill: "genre" })],
    })
  );
}

function drawLine(node, rows, field, domain) {
  if (!rows.length) return pending(node, "Not enough data yet.");
  node.replaceChildren(
    Plot.plot({
      height: 220,
      y: { domain, label: field },
      x: { label: null, type: "utc" },
      marks: [
        Plot.ruleY(domain.slice(0, 1)),
        Plot.line(rows, { x: (d) => new Date(d.date), y: field }),
        Plot.dot(rows, { x: (d) => new Date(d.date), y: field, r: 2 }),
      ],
    })
  );
}

function drawSurvival(survival) {
  const node = $("survival");
  if (!survival.available) {
    const remaining = survival.weeks_needed - survival.weeks_have;
    return pending(node, `Insufficient data — needs ${remaining} more week(s) of history.`);
  }
  node.replaceChildren(
    Plot.plot({
      height: 240,
      y: { domain: [0, 1], label: "still in top 50", percent: true },
      x: { label: "weeks since first appearance" },
      marks: [Plot.line(survival.curve, { x: "week", y: "fraction" })],
    })
  );
}

function drawHalfLife(halfLife) {
  const node = $("halflife");
  if (!halfLife.available) {
    const remaining = halfLife.spells_needed - halfLife.spells_have;
    return pending(node, `Insufficient data — needs ${remaining} more completed spell(s).`);
  }
  node.innerHTML =
    `<p><strong>${halfLife.median_days} days</strong> is how long a typical artist ` +
    `survives in your top 50, across ${halfLife.spells_have} completed spells.</p>`;
}

function drawEvents(events) {
  const recent = events.filter((e) => e.kind === "artist" && e.time_range === "short_term").reverse().slice(0, 50);
  if (!recent.length) return pending($("events"), "No changes recorded yet.");
  $("events").innerHTML = recent
    .map((event) => {
      const cls = event.event === "exited" ? "exited" : "entered";
      const verb = { entered: "entered", re_entered: "returned to", exited: "left" }[event.event];
      return `<li><time>${event.snapshot_date}</time><span class="${cls}">${verb}</span><span>${event.name}</span></li>`;
    })
    .join("");
}

fetch("data.json")
  .then((response) => {
    if (!response.ok) throw new Error(`data.json returned ${response.status}`);
    return response.json();
  })
  .then(render)
  .catch((error) => {
    $("subtitle").textContent = `Could not load data.json — ${error.message}`;
  });
```

- [ ] **Step 3: Verify it renders**

Run:
```bash
python -m http.server 8000 --directory site
```

Open http://127.0.0.1:8000 and confirm:
- the subtitle shows a snapshot count and date range
- four headline tiles render with numbers, not `—`
- the rank timeline draws lines and the two dropdowns change it
- the genre chart renders a stacked area
- survival and half-life both show "Insufficient data — needs N more…"
- the browser console has no errors

Stop the server with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add site/index.html site/app.js site/style.css
git commit -m "feat: add static taste drift dashboard"
```

---

### Task 10: Publish to Pages and wire the full pipeline into CI

Extends the daily Action to rebuild and deploy. Completes milestone 4.

**Files:**
- Modify: `.github/workflows/snapshot.yml`
- Create: `workflows/rebuild_dashboard.md`
- Modify: `CLAUDE.md` (repo root — currently describes an empty scaffold)

**Interfaces:**
- Consumes: all previous tasks
- Produces: a published dashboard at `https://arno0b.github.io/spotify-taste-drift/`

- [ ] **Step 1: Enable Pages with the Actions source**

```bash
gh api -X POST repos/:owner/:repo/pages -f build_type=workflow
```

If it reports the resource already exists, run `gh api -X PUT repos/:owner/:repo/pages -f build_type=workflow` instead.

**Why not "deploy from a branch":** that mode only permits `/` or `/docs` as the publishing directory. `docs/` already holds the specs and plans, and the site lives in `site/`. The Actions source is what allows an arbitrary directory.

- [ ] **Step 2: Add the rebuild steps and the deploy job**

In `.github/workflows/snapshot.yml`, replace the `Commit whatever was captured` step with the following, and append the `deploy` job at the end of the file:

```yaml
      - name: Rebuild derived tables and metrics
        # always(): a partial capture still produced data worth rebuilding from.
        if: always()
        run: |
          python tools/build_derived.py
          python tools/build_metrics.py

      - name: Commit whatever was captured
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A data site
          git diff --staged --quiet || git commit -m "data: snapshot $(date -u +%F)"
          git push

  deploy:
    needs: snapshot
    # always(): a partial capture should still publish the data that arrived.
    if: always()
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
      contents: read
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
        with:
          # Pick up the commit the snapshot job just pushed, not the SHA this
          # workflow started from.
          ref: main
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site
      - id: deployment
        uses: actions/deploy-pages@v4
```

Also add a test job so the suite runs on every push. Insert this as the first job under `jobs:`:

```yaml
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt pytest
      - run: python -m pytest -v
```

And add `needs: test` to the `snapshot` job so a broken build never writes data.

- [ ] **Step 3: Push and verify end to end**

```bash
git add .github/workflows/snapshot.yml
git commit -m "ci: rebuild metrics and deploy dashboard to pages"
git push
gh workflow run "Daily snapshot"
```

Wait for completion with `gh run watch`. Expected: all three jobs green, and the dashboard live at `https://arno0b.github.io/spotify-taste-drift/`. Open it and confirm it matches what you saw locally in Task 9.

- [ ] **Step 4: Write the rebuild SOP**

Create `workflows/rebuild_dashboard.md`:

```markdown
# Workflow: Rebuild the dashboard

## Objective
Regenerate `data/derived/` and `site/data.json` from the raw snapshots, and
publish the dashboard.

## Trigger
Runs automatically after every capture in `.github/workflows/snapshot.yml`.
Run manually after changing any metric definition.

## Tools
1. `python tools/build_derived.py` — raw JSON to tidy CSVs
2. `python tools/build_metrics.py` — CSVs to `site/data.json`

Always in that order. Both do a full rebuild from scratch, which is the point:
change a metric, rerun, and the new definition applies to all history.

## Expected output
`data/derived/snapshots.csv`, `data/derived/artist_genres.csv`, and
`site/data.json`, all committed. Pages redeploys from `site/`.

## Edge cases

- **`SKIPPED <path>` printed** — a raw file is unreadable. It is excluded and
  surfaced as a banner on the dashboard. Inspect the file; if it is truncated,
  the capture that wrote it was interrupted before atomic writes landed.
- **`survival.available` or `half_life.available` still false** — expected until
  8 weeks of history and 10 completed spells exist. Not a bug.
- **Empty `site/data.json` metrics with snapshots present** — check that
  `build_derived.py` ran first. `build_metrics.py` reads the CSVs, not raw.
- **Pages deploys but shows stale data** — the deploy job checks out `main`. If
  the snapshot job's push failed, the deploy publishes the previous commit.
  Check the snapshot job's log.
```

- [ ] **Step 5: Update CLAUDE.md**

The repo root `CLAUDE.md` still describes an empty scaffold. Replace its "Current State" section with:

```markdown
## Current State

Spotify taste drift tracker. Captures top artists and tracks daily into an
immutable raw layer, rebuilds derived tables and dashboard metrics from raw on
every run, and publishes a static dashboard to GitHub Pages.

**Commands:**
- `python -m pytest` — full test suite, no network access required
- `python tools/snapshot_top_items.py` — capture today (idempotent)
- `python tools/build_derived.py` — raw to tidy CSVs
- `python tools/build_metrics.py` — CSVs to `site/data.json`
- `python -m http.server 8000 --directory site` — preview the dashboard
- `python tools/mint_refresh_token.py` — one-time, mint a refresh token

**Workflows:** `workflows/capture_snapshot.md`, `workflows/rebuild_dashboard.md`

**Design:** `docs/superpowers/specs/2026-09-03-spotify-taste-drift-design.md`

**Conventions specific to this project:**
- `data/raw/` is immutable. Never edit or regenerate it — it cannot be
  re-fetched. Everything below it is disposable and rebuilt from scratch.
- Never make live Spotify calls in tests. Pass a fake session or fixtures.
- This is a public repo. Secrets live only in Actions secrets and `.env`.
```

- [ ] **Step 6: Commit**

```bash
git add workflows/rebuild_dashboard.md CLAUDE.md
git commit -m "docs: add rebuild SOP and update CLAUDE.md for the built project"
git push
```

**Milestone 4 complete.** Capture runs daily, metrics rebuild, the dashboard publishes.

---

## Milestone 5: Unlock (not a task — a calendar note)

Roughly 8 weeks after Task 5 lands, revisit:

- Confirm the survival curve and half-life have flipped to `available: true` and render sensibly.
- Tune `RANK_WEIGHT` in `tools/build_metrics.py` against real data. `1/rank` is a defensible starting point, not a measured one — if the genre chart is dominated by the top 3 artists, try `1/sqrt(rank)` or `51 - rank`.
- Check the Action is still enabled. GitHub disables scheduled workflows after 60 days of repo inactivity; if it has stopped, re-enable in the Actions tab and switch the push to a personal access token.
