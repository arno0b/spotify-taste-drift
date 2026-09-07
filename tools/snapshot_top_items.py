"""Capture today's Spotify top artists and tracks into the immutable raw layer.

Spotify's top-items endpoints have no history and cannot be backfilled, so a
missed capture is permanent data loss. Everything here is built around not
losing a day: partial results are kept, writes are atomic, and reruns are safe.
"""

import datetime as dt
import json
import shutil
import sys
import time
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
                delay = float(retry_after) if retry_after else 2.0**attempt
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
