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
        "names",
        "events",
        "divergence",
        "genre_coverage",
        "genre_shift",
        "horizon",
        "reach",
        "survival",
        "half_life",
    }
    # "mainstreamness" is gone for good: it measured Spotify's popularity
    # score, which no longer exists. "reach" replaces it using Deezer fan
    # counts, which are a different thing and are named differently.
    assert "mainstreamness" not in payload
    assert "mean_popularity" not in payload["headline"]
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
