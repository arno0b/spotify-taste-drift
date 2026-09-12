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
