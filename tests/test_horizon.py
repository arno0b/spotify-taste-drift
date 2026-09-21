from tools.build_metrics import build, genre_shift, horizon_shift


def row(date, id_, rank, *, kind="artist", time_range="short_term"):
    return {
        "snapshot_date": date,
        "time_range": time_range,
        "kind": kind,
        "rank": rank,
        "spotify_id": id_,
        "name": id_.upper(),
        "popularity": None,
    }


def mix(date, genre, share, time_range):
    return {"date": date, "time_range": time_range, "genre": genre, "share": share}


# --- horizon shift ----------------------------------------------------------
#
# Spotify returns three time horizons in every capture, so comparing them is
# real drift available from one snapshot — no waiting for history to accumulate.

def test_an_artist_absent_from_the_year_ranks_as_the_strongest_riser():
    rows = [
        row("2026-09-21", "new", 1, time_range="short_term"),
        row("2026-09-21", "old", 2, time_range="short_term"),
        row("2026-09-21", "old", 1, time_range="long_term"),
    ]

    result = horizon_shift(rows)[0]

    assert result["ascending"][0]["spotify_id"] == "new"
    assert result["ascending"][0]["long"] is None  # absent over the year


def test_ascending_is_ordered_by_how_far_an_entry_has_climbed():
    rows = [
        row("2026-09-21", "a", 1, time_range="short_term"),
        row("2026-09-21", "b", 2, time_range="short_term"),
        row("2026-09-21", "a", 5, time_range="long_term"),
        row("2026-09-21", "b", 40, time_range="long_term"),
    ]

    ascending = horizon_shift(rows)[0]["ascending"]

    # b climbed 38 places, a climbed 4.
    assert [e["spotify_id"] for e in ascending] == ["b", "a"]
    assert ascending[0]["gap"] == 38


def test_fading_lists_yearly_favourites_that_have_dropped_out():
    rows = [
        row("2026-09-21", "kept", 1, time_range="short_term"),
        row("2026-09-21", "kept", 2, time_range="long_term"),
        row("2026-09-21", "gone", 1, time_range="long_term"),
    ]

    fading = horizon_shift(rows)[0]["fading"]

    assert [e["spotify_id"] for e in fading] == ["gone"]
    assert fading[0]["long"] == 1


def test_counts_split_the_lists_into_new_dropped_and_steady():
    rows = [
        row("2026-09-21", "both", 1, time_range="short_term"),
        row("2026-09-21", "both", 1, time_range="long_term"),
        row("2026-09-21", "onlyshort", 2, time_range="short_term"),
        row("2026-09-21", "onlylong", 2, time_range="long_term"),
    ]

    counts = horizon_shift(rows)[0]["counts"]

    assert counts == {"short_only": 1, "long_only": 1, "both": 1}


def test_artists_and_tracks_are_reported_separately():
    # Each kind needs both horizons present, or it is skipped by design.
    rows = []
    for kind in ("artist", "track"):
        rows += [
            row("2026-09-21", f"{kind}1", 1, kind=kind, time_range="short_term"),
            row("2026-09-21", f"{kind}1", 3, kind=kind, time_range="long_term"),
        ]

    kinds = {r["kind"] for r in horizon_shift(rows)}

    assert kinds == {"artist", "track"}


def test_a_date_missing_a_horizon_is_skipped_rather_than_half_reported():
    rows = [row("2026-09-21", "a", 1, time_range="short_term")]

    assert horizon_shift(rows) == []


def test_every_snapshot_date_gets_its_own_comparison():
    rows = []
    for date in ("2026-09-20", "2026-09-21"):
        rows += [
            row(date, "a", 1, time_range="short_term"),
            row(date, "a", 2, time_range="long_term"),
        ]

    assert sorted(r["date"] for r in horizon_shift(rows)) == ["2026-09-20", "2026-09-21"]


# --- genre shift ------------------------------------------------------------

def test_genre_shift_reports_share_now_against_share_over_the_year():
    rows = [
        mix("2026-09-21", "indie pop", 0.072, "short_term"),
        mix("2026-09-21", "indie pop", 0.041, "long_term"),
    ]

    result = genre_shift(rows)[0]

    assert result["genre"] == "indie pop"
    assert round(result["delta"], 4) == 0.031


def test_a_genre_absent_from_the_last_four_weeks_reads_as_a_full_loss():
    rows = [mix("2026-09-21", "trap", 0.032, "long_term")]

    result = genre_shift(rows)[0]

    assert result["short_share"] is None  # rendered as a dash, not a zero
    assert round(result["delta"], 4) == -0.032


def test_genre_shift_ignores_medium_term():
    rows = [mix("2026-09-21", "pop", 0.5, "medium_term")]

    assert genre_shift(rows) == []


def test_genre_shift_is_ordered_by_largest_move_first():
    rows = [
        mix("2026-09-21", "small", 0.02, "short_term"),
        mix("2026-09-21", "small", 0.01, "long_term"),
        mix("2026-09-21", "big", 0.20, "short_term"),
        mix("2026-09-21", "big", 0.05, "long_term"),
    ]

    assert [r["genre"] for r in genre_shift(rows)] == ["big", "small"]


# --- payload --------------------------------------------------------------

def test_build_exposes_track_divergence_for_the_lede():
    rows = []
    for kind in ("artist", "track"):
        rows += [
            row("2026-09-21", f"{kind}1", 1, kind=kind, time_range="short_term"),
            row("2026-09-21", f"{kind}1", 1, kind=kind, time_range="long_term"),
            row("2026-09-21", f"{kind}2", 2, kind=kind, time_range="long_term"),
        ]

    payload = build(rows, [], [], "2026-09-21T06:00:00Z")

    # The page's headline claim is the gap between these two numbers.
    assert payload["headline"]["divergence_artists"] is not None
    assert payload["headline"]["divergence_tracks"] is not None
    assert "horizon" in payload
    assert "genre_shift" in payload


# --- payload size ---------------------------------------------------------

def test_rank_timeline_is_windowed_so_the_payload_stops_growing():
    # data.json is fetched on every page load. Unbounded growth would make the
    # page slower every day; derived/ keeps the full history regardless.
    from tools.build_metrics import rank_timeline

    rows = []
    for day in range(1, 29):
        rows.append(row(f"2026-09-{day:02d}", "a", 1))

    windowed = rank_timeline(rows, window=7)["artist"]["short_term"]

    assert len({e["date"] for e in windowed}) == 7
    assert max(e["date"] for e in windowed) == "2026-09-28"  # keeps the newest


def test_rank_timeline_without_a_window_keeps_everything():
    from tools.build_metrics import rank_timeline

    rows = [row(f"2026-09-{day:02d}", "a", 1) for day in range(1, 15)]

    assert len(rank_timeline(rows)["artist"]["short_term"]) == 14


def test_names_map_covers_every_id_in_the_shipped_timeline():
    from tools.build_metrics import names_of, rank_timeline

    rows = [row("2026-09-21", "a1", 1), row("2026-09-21", "a2", 2)]
    timeline = rank_timeline(rows)

    names = names_of(rows, timeline)

    ids = {e["id"] for r in timeline["artist"].values() for e in r}
    assert ids <= set(names), "a row would render with an undefined name"
    assert names["a1"] == "A1"
