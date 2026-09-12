from tools.build_metrics import (
    divergence,
    entry_exit_events,
    genre_coverage,
    genre_mix,
    rank_timeline,
    reach,
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


# --- rank timeline ----------------------------------------------------------

def test_rank_timeline_is_nested_by_kind_then_time_range():
    rows = [row("2026-09-01", "a1", 3, name="Alpha")]

    timeline = rank_timeline(rows)

    assert timeline["artist"]["short_term"] == [
        {"date": "2026-09-01", "id": "a1", "name": "Alpha", "rank": 3}
    ]


# --- genre mix (from enrichment, not Spotify) -------------------------------

def genre_row(date, artist_id, genre, time_range="short_term"):
    return {
        "snapshot_date": date,
        "time_range": time_range,
        "artist_id": artist_id,
        "genre": genre,
    }


def test_genre_shares_sum_to_one():
    snapshot_rows = [row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock"), genre_row("2026-09-01", "a2", "jazz")]

    assert round(sum(s["share"] for s in genre_mix(genre_rows, snapshot_rows)), 9) == 1.0


def test_rank_one_outweighs_rank_two_by_the_inverse_rank_rule():
    snapshot_rows = [row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock"), genre_row("2026-09-01", "a2", "jazz")]

    shares = {s["genre"]: s["share"] for s in genre_mix(genre_rows, snapshot_rows)}

    assert round(shares["rock"], 6) == round(2 / 3, 6)   # weights 1/1 and 1/2
    assert round(shares["jazz"], 6) == round(1 / 3, 6)


def test_an_artists_weight_is_split_evenly_across_its_genres():
    snapshot_rows = [row("2026-09-01", "a1", 1)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock"), genre_row("2026-09-01", "a1", "jazz")]

    shares = {s["genre"]: s["share"] for s in genre_mix(genre_rows, snapshot_rows)}

    assert shares["rock"] == 0.5
    assert shares["jazz"] == 0.5


def test_nothing_resolved_emits_no_figure_at_all():
    assert genre_mix([], [row("2026-09-01", "a1", 1)]) == []


def test_unresolved_artists_are_excluded_rather_than_bucketed():
    # An unresolved artist is a gap in knowledge, not a genre. Including it as
    # a band would conflate "unknown music" with "we failed to identify it".
    snapshot_rows = [row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock")]

    shares = {s["genre"]: s["share"] for s in genre_mix(genre_rows, snapshot_rows)}

    assert shares == {"rock": 1.0}
    assert "unclassified" not in shares


def test_coverage_reports_the_gap_the_genre_chart_hides():
    snapshot_rows = [row("2026-09-01", "a1", 1), row("2026-09-01", "a2", 2)]
    genre_rows = [genre_row("2026-09-01", "a1", "rock")]

    cov = genre_coverage(genre_rows, snapshot_rows)[0]

    assert cov["resolved"] == 1
    assert cov["total"] == 2
    # Rank-weighted: rank 1 carries 1/1 of 1.5 total weight, so 2/3 not 1/2.
    assert round(cov["share"], 6) == round(2 / 3, 6)


def test_genre_mix_ignores_tracks():
    assert genre_mix([], [row("2026-09-01", "t1", 1, kind="track")]) == []


# --- reach ------------------------------------------------------------------

def fan_row(date, id_, rank, fans, time_range="short_term"):
    r = row(date, id_, rank, time_range=time_range)
    r["deezer_fans"] = fans
    return r


def test_reach_uses_the_median_so_one_giant_artist_cannot_skew_it():
    rows = [
        fan_row("2026-09-01", "a1", 1, 1_000),
        fan_row("2026-09-01", "a2", 2, 2_000),
        fan_row("2026-09-01", "a3", 3, 14_000_000),
    ]

    result = reach(rows)[0]

    assert result["median_fans"] == 2_000
    assert result["artists_measured"] == 3


def test_reach_skips_artists_with_no_fan_count():
    rows = [fan_row("2026-09-01", "a1", 1, 500), fan_row("2026-09-01", "a2", 2, None)]

    assert reach(rows)[0]["artists_measured"] == 1


def test_reach_is_empty_when_nothing_is_enriched():
    assert reach([row("2026-09-01", "a1", 1)]) == []
