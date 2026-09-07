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
