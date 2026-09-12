from tools.build_metrics import (
    divergence,
    entry_exit_events,
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
