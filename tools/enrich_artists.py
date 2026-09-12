"""Enrich Spotify artists with genres and reach, via MusicBrainz and Deezer.

Spotify withdrew `genres` and `popularity` from the top-item endpoints, and a
development-mode app is 403 on /v1/artists. Both are recoverable from elsewhere:

    Spotify artist ID
      -> MusicBrainz /url lookup on the open.spotify.com URL   (exact identity)
      -> MusicBrainz artist: genres, plus linked Deezer URL
      -> Deezer /artist/<id>: fan count                        (reach proxy)

Matching by artist *name* was tried and rejected. It silently returns the wrong
artist: a name search for "Joji" hit a different act with 122 fans instead of
562,912, and "Øneheart" matched an unrelated "One Heart". Going through
MusicBrainz's URL relationships keeps identity exact.

Results are cached in data/enrichment/artists.json and keyed by Spotify artist
ID, so enrichment applies retroactively to every snapshot ever captured, and a
daily run only looks up artists it has never seen.
"""

import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RAW_ROOT = Path("data/raw")
CACHE_PATH = Path("data/enrichment/artists.json")
TIME_RANGES = ("short_term", "medium_term", "long_term")

MB_ROOT = "https://musicbrainz.org/ws/2"
DEEZER_ROOT = "https://api.deezer.com"
USER_AGENT = "SpotifyTasteDrift/1.0 ( https://github.com/arno0b/spotify-taste-drift )"

# MusicBrainz asks for no more than one request per second, and answers 503
# when you exceed it. This is a courtesy limit on a free community service.
MB_MIN_INTERVAL = 1.15
MB_MAX_RETRIES = 3
RETRY_AFTER_DAYS = 30


def artist_ids_from_raw(raw_root):
    """{spotify_id: name} for every artist ever captured."""
    raw_root = Path(raw_root)
    found = {}
    for day_dir in sorted(p for p in raw_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for time_range in TIME_RANGES:
            path = day_dir / f"top_artists_{time_range}.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            for item in payload.get("items", []):
                if item.get("id"):
                    found[item["id"]] = item.get("name", "")
    return found


def _is_stale(entry, now_iso, retry_after_days):
    """Misses are retried eventually — MusicBrainz is community-edited and grows."""
    if entry.get("status") == "ok":
        return False
    fetched = entry.get("fetched_at")
    if not fetched:
        return True
    age = _days_between(fetched, now_iso)
    return age >= retry_after_days


def _days_between(earlier_iso, later_iso):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (dt.datetime.strptime(later_iso, fmt) - dt.datetime.strptime(earlier_iso, fmt)).days


def enrich(
    ids,
    cache,
    lookup,
    *,
    now_iso,
    retry_after_days=RETRY_AFTER_DAYS,
    on_progress=None,
    on_checkpoint=None,
    checkpoint_every=20,
):
    """Fill in missing entries. Existing good entries are never re-fetched.

    `lookup(spotify_id)` returns a dict of enrichment fields, or None if the
    artist could not be resolved. Entries for artists no longer in the top fifty
    are kept, because past snapshots still reference them.

    `on_checkpoint` receives a copy of the partial result every
    `checkpoint_every` lookups. A full run is several minutes of
    rate-limited requests, so losing all of it to one interruption would be
    needless.
    """
    result = dict(cache)
    todo = [
        (spotify_id, name)
        for spotify_id, name in sorted(ids.items())
        if spotify_id not in result or _is_stale(result[spotify_id], now_iso, retry_after_days)
    ]

    for index, (spotify_id, name) in enumerate(todo, start=1):
        if on_progress:
            on_progress(index, len(todo), name)
        try:
            found = lookup(spotify_id)
        except Exception as error:  # noqa: BLE001 - a blip must not destroy good data
            if spotify_id in result:
                continue
            result[spotify_id] = {
                "name": name, "status": "error", "reason": str(error)[:200],
                "genres": [], "mbid": None, "deezer_id": None, "deezer_fans": None,
                "fetched_at": now_iso,
            }
            continue

        if found is None:
            result[spotify_id] = {
                "name": name, "status": "not_found",
                "genres": [], "mbid": None, "deezer_id": None, "deezer_fans": None,
                "fetched_at": now_iso,
            }
        else:
            result[spotify_id] = {
                "name": name, "status": "ok",
                "genres": found.get("genres", []),
                "mbid": found.get("mbid"),
                "deezer_id": found.get("deezer_id"),
                "deezer_fans": found.get("deezer_fans"),
                "fetched_at": now_iso,
            }

        if on_checkpoint and index % checkpoint_every == 0:
            # A copy: callers must get a stable snapshot, not a view onto a dict
            # that keeps mutating underneath them.
            on_checkpoint(dict(result))

    return result


# --- live lookup -----------------------------------------------------------

class _Pacer:
    """Keeps MusicBrainz calls at or under one per second, with 503 retries."""

    def __init__(self, session=None, sleep=None):
        self.session = session or requests
        self.sleep = sleep or time.sleep
        self._last = 0.0

    def get(self, path, params):
        for attempt in range(MB_MAX_RETRIES + 1):
            wait = MB_MIN_INTERVAL - (time.time() - self._last)
            if wait > 0:
                self.sleep(wait)
            response = self.session.get(
                f"{MB_ROOT}{path}",
                params={**params, "fmt": "json"},
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            self._last = time.time()
            if response.status_code == 503 and attempt < MB_MAX_RETRIES:
                self.sleep(2.0**attempt)
                continue
            return response
        return response


def make_lookup(session=None, sleep=None):
    """Return lookup(spotify_id) -> enrichment dict or None."""
    pacer = _Pacer(session, sleep)
    http = session or requests

    def lookup(spotify_id):
        response = pacer.get(
            "/url",
            {"resource": f"https://open.spotify.com/artist/{spotify_id}", "inc": "artist-rels"},
        )
        if response.status_code == 404:
            return None  # nobody has linked this Spotify artist in MusicBrainz
        if response.status_code != 200:
            raise RuntimeError(f"MusicBrainz url lookup {response.status_code}")

        mbids = [r["artist"]["id"] for r in response.json().get("relations", []) if r.get("artist")]
        if not mbids:
            return None

        detail = pacer.get(f"/artist/{mbids[0]}", {"inc": "genres+tags+url-rels"})
        if detail.status_code != 200:
            raise RuntimeError(f"MusicBrainz artist lookup {detail.status_code}")
        data = detail.json()

        # Prefer curated genres; fall back to community tags, which are messier
        # but far better than nothing for a long-tail artist.
        genres = [g["name"] for g in data.get("genres", [])]
        if not genres:
            genres = [t["name"] for t in data.get("tags", []) if t.get("count", 0) > 0]

        deezer_id, deezer_fans = None, None
        for relation in data.get("relations", []):
            url = (relation.get("url") or {}).get("resource", "")
            if "deezer.com/artist/" in url:
                deezer_id = url.rstrip("/").split("/")[-1]
                break
        if deezer_id:
            try:
                fans = http.get(f"{DEEZER_ROOT}/artist/{deezer_id}", timeout=30)
                if fans.status_code == 200:
                    deezer_fans = fans.json().get("nb_fan")
            except Exception:  # noqa: BLE001 - reach is optional, genres are not
                pass

        return {
            "mbid": mbids[0],
            "genres": sorted(genres),
            "deezer_id": deezer_id,
            "deezer_fans": deezer_fans,
        }

    return lookup


def load_cache(path=CACHE_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def save_cache(cache, path=CACHE_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    ids = artist_ids_from_raw(RAW_ROOT)
    cache = load_cache()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def progress(index, total, name):
        print(f"  [{index}/{total}] {name}", flush=True)

    before = sum(1 for e in cache.values() if e.get("status") == "ok")
    cache = enrich(
        ids, cache, make_lookup(),
        now_iso=now_iso, on_progress=progress, on_checkpoint=save_cache,
    )
    save_cache(cache)

    ok = sum(1 for e in cache.values() if e.get("status") == "ok")
    with_genres = sum(1 for e in cache.values() if e.get("genres"))
    with_fans = sum(1 for e in cache.values() if e.get("deezer_fans"))
    print(
        f"{len(ids)} artists seen, {len(cache)} cached, {ok} resolved "
        f"(+{ok - before} this run), {with_genres} with genres, {with_fans} with reach"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
