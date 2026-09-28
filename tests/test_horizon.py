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


# --- listener-facing framing ------------------------------------------------

def test_phase_names_the_band_rather_than_reporting_a_coefficient():
    from tools.build_metrics import phase_of

    assert phase_of(0.90)["name"] == "comfort"
    assert phase_of(0.65)["name"] == "settled"
    assert phase_of(0.50)["name"] == "drifting"
    assert phase_of(0.30)["name"] == "exploring"
    assert phase_of(0.05)["name"] == "overhaul"


def test_phase_sentence_avoids_jargon():
    from tools.build_metrics import phase_of

    sentence = phase_of(0.50)["sentence"]

    for jargon in ("divergence", "overlap", "coefficient", "rank"):
        assert jargon not in sentence.lower()


def test_phase_marker_runs_from_comfort_to_exploring():
    from tools.build_metrics import phase_of

    # Everything an old favourite sits at the comfort end.
    assert phase_of(1.0)["position"] == 0
    assert phase_of(0.0)["position"] == 100
    assert phase_of(0.56)["position"] == 44


def test_phase_is_absent_rather_than_guessed_when_there_is_no_data():
    from tools.build_metrics import phase_of

    assert phase_of(None) is None


def test_images_are_emitted_only_for_what_the_horizon_can_show():
    from tools.build_metrics import images_of

    rows = [
        dict(row("2026-09-21", "shown", 1), image_url="https://i.scdn.co/a"),
        dict(row("2026-09-21", "unused", 2), image_url="https://i.scdn.co/b"),
    ]
    horizon = [{"ascending": [{"spotify_id": "shown"}], "fading": []}]

    images = images_of(rows, horizon)

    assert images == {"shown": "https://i.scdn.co/a"}


def test_an_entry_with_no_artwork_is_simply_absent_from_the_map():
    from tools.build_metrics import images_of

    rows = [dict(row("2026-09-21", "a", 1), image_url="")]
    horizon = [{"ascending": [{"spotify_id": "a"}], "fading": []}]

    assert images_of(rows, horizon) == {}


def test_tracks_carry_the_performer_as_a_subtitle():
    # A song title alone is not recognisable; Spotify never shows one without
    # its artist.
    rows = [
        dict(row("2026-09-27", "t1", 1, kind="track"), primary_artist_name="Sade"),
        dict(row("2026-09-27", "t1", 9, kind="track", time_range="long_term"),
             primary_artist_name="Sade"),
    ]

    entry = horizon_shift(rows)[0]["ascending"][0]

    assert entry["subtitle"] == "Sade"


def test_artists_have_an_empty_subtitle_rather_than_a_missing_key():
    rows = [
        row("2026-09-27", "a1", 1),
        row("2026-09-27", "a1", 9, time_range="long_term"),
    ]

    entry = horizon_shift(rows)[0]["ascending"][0]

    assert entry["subtitle"] == ""


# --- album runs -------------------------------------------------------------

def trk(date, id_, rank, album_id, album_name, artist, year, time_range="short_term"):
    r = row(date, id_, rank, kind="track", time_range=time_range)
    r.update(album_id=album_id, album_name=album_name, primary_artist_name=artist,
             release_year=year)
    return r


def test_an_album_contributing_several_tracks_is_reported_as_a_run():
    from tools.build_metrics import album_runs

    rows = [
        trk("2026-09-28", "t1", 3, "al1", "Hurry Up Tomorrow", "The Weeknd", 2025),
        trk("2026-09-28", "t2", 7, "al1", "Hurry Up Tomorrow", "The Weeknd", 2025),
        trk("2026-09-28", "t3", 9, "al2", "Something Else", "Someone", 2020),
    ]

    runs = album_runs(rows)

    assert len(runs) == 1
    assert runs[0]["name"] == "Hurry Up Tomorrow"
    assert runs[0]["count"] == 2
    assert runs[0]["best_rank"] == 3  # its highest-placed track


def test_runs_are_ordered_by_how_many_tracks_they_contribute():
    from tools.build_metrics import album_runs

    rows = [trk("2026-09-28", f"a{i}", i + 1, "big", "Big", "X", 2025) for i in range(4)]
    rows += [trk("2026-09-28", f"b{i}", 20 + i, "small", "Small", "Y", 2025) for i in range(2)]

    assert [r["name"] for r in album_runs(rows)] == ["Big", "Small"]


def test_only_the_latest_snapshot_and_only_the_recent_window_count():
    from tools.build_metrics import album_runs

    rows = [
        trk("2026-09-27", "old1", 1, "al1", "Yesterday", "X", 2025),
        trk("2026-09-27", "old2", 2, "al1", "Yesterday", "X", 2025),
        trk("2026-09-28", "y1", 1, "al2", "Yearly", "X", 2025, time_range="long_term"),
        trk("2026-09-28", "y2", 2, "al2", "Yearly", "X", 2025, time_range="long_term"),
        trk("2026-09-28", "n1", 1, "al3", "Now", "X", 2025),
        trk("2026-09-28", "n2", 2, "al3", "Now", "X", 2025),
    ]

    assert [r["name"] for r in album_runs(rows)] == ["Now"]


# --- release profile --------------------------------------------------------

def test_release_profile_measures_how_current_the_music_is():
    from tools.build_metrics import release_profile

    rows = [
        trk("2026-09-28", "t1", 1, "a", "A", "X", 2026),
        trk("2026-09-28", "t2", 2, "b", "B", "X", 2025),
        trk("2026-09-28", "t3", 3, "c", "C", "X", 1971),
        trk("2026-09-28", "t4", 4, "d", "D", "X", 2010),
    ]

    p = release_profile(rows)[0]

    assert p["median_year"] == 2017  # median of 1971, 2010, 2025, 2026
    assert p["recent_share"] == 0.5  # 2025 and 2026 of four
    assert p["oldest"] == 1971 and p["newest"] == 2026


def test_release_profile_buckets_by_decade_for_the_spread():
    from tools.build_metrics import release_profile

    rows = [
        trk("2026-09-28", "t1", 1, "a", "A", "X", 1971),
        trk("2026-09-28", "t2", 2, "b", "B", "X", 1978),
        trk("2026-09-28", "t3", 3, "c", "C", "X", 2025),
    ]

    decades = {d["decade"]: d["count"] for d in release_profile(rows)[0]["decades"]}

    assert decades == {1970: 2, 2020: 1}


def test_tracks_with_no_release_year_are_excluded_not_counted_as_zero():
    from tools.build_metrics import release_profile

    rows = [
        trk("2026-09-28", "t1", 1, "a", "A", "X", 2025),
        trk("2026-09-28", "t2", 2, "b", "B", "X", None),
    ]

    assert release_profile(rows)[0]["measured"] == 1


def test_coverage_names_the_artists_it_could_not_identify():
    # The gap skews regional, so the figure has to be able to say who is missing.
    from tools.build_metrics import genre_coverage

    snapshot_rows = [row("2026-09-28", "known", 1), row("2026-09-28", "unknown", 2)]
    snapshot_rows[1]["name"] = "Meghdol"
    genre_rows = [{"snapshot_date": "2026-09-28", "time_range": "short_term",
                   "artist_id": "known", "genre": "rock"}]

    cov = genre_coverage(genre_rows, snapshot_rows)[0]

    assert cov["unidentified"] == ["Meghdol"]


def test_deluxe_and_standard_editions_count_as_one_record():
    # Spotify issues separate album ids per edition, which split one album run
    # in two: "Hurry Up Tomorrow" was reported as 4 and 2 instead of 6.
    from tools.build_metrics import album_runs

    rows = [
        trk("2026-09-28", "t1", 2, "std", "Hurry Up Tomorrow", "The Weeknd", 2025),
        trk("2026-09-28", "t2", 5, "std", "Hurry Up Tomorrow", "The Weeknd", 2025),
        trk("2026-09-28", "t3", 8, "deluxe", "Hurry Up Tomorrow", "The Weeknd", 2025),
    ]

    runs = album_runs(rows)

    assert len(runs) == 1
    assert runs[0]["count"] == 3
    # Links to the edition holding the highest-placed track.
    assert runs[0]["album_id"] == "std"


def test_two_albums_sharing_a_title_but_not_an_artist_stay_separate():
    from tools.build_metrics import album_runs

    rows = [
        trk("2026-09-28", "a1", 1, "x1", "Greatest Hits", "Artist One", 2020),
        trk("2026-09-28", "a2", 2, "x1", "Greatest Hits", "Artist One", 2020),
        trk("2026-09-28", "b1", 3, "y1", "Greatest Hits", "Artist Two", 2021),
        trk("2026-09-28", "b2", 4, "y1", "Greatest Hits", "Artist Two", 2021),
    ]

    assert len(album_runs(rows)) == 2
