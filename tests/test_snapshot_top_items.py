import json

import pytest

from tools.snapshot_top_items import capture, make_fetcher

NOW = "2026-09-03T06:00:12Z"


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


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
    result = capture(
        lambda kind, tr: (200, {"items": []}), tmp_path, "2026-09-03", now_iso=NOW
    )

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
