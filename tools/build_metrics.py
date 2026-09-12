"""Compute dashboard metrics from the derived tables.

Every function here is pure: it takes rows and returns data. That is what makes
the whole analysis replayable — improve a metric, rerun, and the new definition
applies to all history rather than only to days captured after the change.
"""

import csv
import datetime as dt
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Same reason as the other entry points: allow `python tools/build_metrics.py`
# from the repo root. build_derived is imported lazily in _skipped_from_raw().
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DERIVED_ROOT = Path("data/derived")
SITE_ROOT = Path("site")

TIME_RANGES = ("short_term", "medium_term", "long_term")
UNCLASSIFIED = "unclassified"

# Inverse rank. Without it the 45 artists in the tail outweigh the top 5, and
# the genre chart stops reflecting what you actually listen to.
RANK_WEIGHT = lambda rank: 1.0 / rank  # noqa: E731


def load_rows(derived_root):
    """Read both derived CSVs, coercing the numeric columns."""
    derived_root = Path(derived_root)
    snapshot_rows = _read_csv(derived_root / "snapshots.csv")
    for row in snapshot_rows:
        row["rank"] = int(row["rank"])
        row["popularity"] = int(row["popularity"]) if row["popularity"] != "" else None
    return snapshot_rows, _read_csv(derived_root / "artist_genres.csv")


def _read_csv(path):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _group_by_series(snapshot_rows):
    """Index rows as {(kind, time_range): {date: {id: row}}}."""
    grouped = defaultdict(lambda: defaultdict(dict))
    for row in snapshot_rows:
        grouped[(row["kind"], row["time_range"])][row["snapshot_date"]][row["spotify_id"]] = row
    return grouped


def rank_timeline(snapshot_rows):
    """{kind: {time_range: [{date, id, name, rank}, ...]}}"""
    timeline = defaultdict(lambda: defaultdict(list))
    for row in sorted(
        snapshot_rows, key=lambda r: (r["kind"], r["time_range"], r["snapshot_date"], r["rank"])
    ):
        timeline[row["kind"]][row["time_range"]].append(
            {
                "date": row["snapshot_date"],
                "id": row["spotify_id"],
                "name": row["name"],
                "rank": row["rank"],
            }
        )
    return {kind: dict(ranges) for kind, ranges in timeline.items()}


def entry_exit_events(snapshot_rows):
    """Entries, exits and re-entries between consecutive AVAILABLE snapshots."""
    events = []
    for (kind, time_range), by_date in _group_by_series(snapshot_rows).items():
        dates = sorted(by_date)
        seen_ever = set(by_date[dates[0]]) if dates else set()

        for previous_date, date in zip(dates, dates[1:]):
            previous_ids = set(by_date[previous_date])
            current_ids = set(by_date[date])

            for spotify_id in sorted(current_ids - previous_ids):
                events.append(
                    _event(
                        date,
                        kind,
                        time_range,
                        by_date[date][spotify_id],
                        "re_entered" if spotify_id in seen_ever else "entered",
                    )
                )
            for spotify_id in sorted(previous_ids - current_ids):
                events.append(
                    _event(
                        date, kind, time_range, by_date[previous_date][spotify_id], "exited"
                    )
                )
            seen_ever |= current_ids
    events.sort(key=lambda e: (e["snapshot_date"], e["kind"], e["time_range"], e["spotify_id"]))
    return events


def _event(date, kind, time_range, row, event):
    return {
        "snapshot_date": date,
        "kind": kind,
        "time_range": time_range,
        "spotify_id": row["spotify_id"],
        "name": row["name"],
        "event": event,
    }


def divergence(snapshot_rows):
    """Overlap between short_term and long_term, per date and kind."""
    by_kind_date = defaultdict(lambda: defaultdict(set))
    for row in snapshot_rows:
        if row["time_range"] in ("short_term", "long_term"):
            key = (row["kind"], row["snapshot_date"])
            by_kind_date[key][row["time_range"]].add(row["spotify_id"])

    results = []
    for (kind, date), ranges in by_kind_date.items():
        short, long = ranges.get("short_term"), ranges.get("long_term")
        if not short or not long:
            continue  # one side missing: no meaningful comparison
        results.append(
            {
                "date": date,
                "kind": kind,
                "overlap": len(short & long) / min(len(short), len(long)),
            }
        )
    results.sort(key=lambda r: (r["date"], r["kind"]))
    return results


def mainstreamness(snapshot_rows):
    """Mean and median popularity per date, kind and time range."""
    grouped = defaultdict(list)
    for row in snapshot_rows:
        if row.get("popularity") is not None:
            grouped[(row["snapshot_date"], row["kind"], row["time_range"])].append(
                row["popularity"]
            )

    results = [
        {
            "date": date,
            "kind": kind,
            "time_range": time_range,
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
        }
        for (date, kind, time_range), values in grouped.items()
        if values
    ]
    results.sort(key=lambda r: (r["date"], r["kind"], r["time_range"]))
    return results


def genre_mix(genre_rows, snapshot_rows):
    """Rank-weighted genre shares per date and time range, normalised to 1."""
    genres_by_artist = defaultdict(lambda: defaultdict(list))
    for row in genre_rows:
        genres_by_artist[(row["snapshot_date"], row["time_range"])][row["artist_id"]].append(
            row["genre"]
        )

    weights = defaultdict(lambda: defaultdict(float))
    for row in snapshot_rows:
        if row["kind"] != "artist":
            continue
        key = (row["snapshot_date"], row["time_range"])
        weight = RANK_WEIGHT(row["rank"])
        genres = genres_by_artist[key].get(row["spotify_id"]) or [UNCLASSIFIED]
        for genre in genres:
            weights[key][genre] += weight / len(genres)

    results = []
    for (date, time_range), by_genre in weights.items():
        total = sum(by_genre.values())
        if not total:
            continue
        for genre, weight in by_genre.items():
            results.append(
                {
                    "date": date,
                    "time_range": time_range,
                    "genre": genre,
                    "share": weight / total,
                }
            )
    results.sort(key=lambda r: (r["date"], r["time_range"], r["genre"]))
    return results


SURVIVAL_MIN_WEEKS = 8
HALFLIFE_MIN_SPELLS = 10


def _short_term_artist_presence(snapshot_rows):
    """{date: {artist_id}} for short_term artists only, plus sorted dates."""
    by_date = defaultdict(set)
    for row in snapshot_rows:
        if row["kind"] == "artist" and row["time_range"] == "short_term":
            by_date[row["snapshot_date"]].add(row["spotify_id"])
    return by_date, sorted(by_date)


def new_artist_survival(snapshot_rows):
    """Retention curve for the first cohort of artists to appear."""
    by_date, dates = _short_term_artist_presence(snapshot_rows)
    weeks_span = _weeks_between(dates[0], dates[-1]) if len(dates) > 1 else 0

    if weeks_span < SURVIVAL_MIN_WEEKS:
        return {
            "available": False,
            "weeks_needed": SURVIVAL_MIN_WEEKS,
            "weeks_have": weeks_span,
            "curve": [],
        }

    cohort = by_date[dates[0]]
    if not cohort:
        return {
            "available": False,
            "weeks_needed": SURVIVAL_MIN_WEEKS,
            "weeks_have": weeks_span,
            "curve": [],
        }

    curve = [
        {
            "week": _weeks_between(dates[0], date),
            "fraction": len(cohort & by_date[date]) / len(cohort),
        }
        for date in dates
    ]
    return {
        "available": True,
        "weeks_needed": SURVIVAL_MIN_WEEKS,
        "weeks_have": weeks_span,
        "curve": curve,
    }


def rotation_half_life(snapshot_rows):
    """Median days a short_term artist survives, over completed spells only."""
    by_date, dates = _short_term_artist_presence(snapshot_rows)

    spells = []
    active = {}  # artist_id -> the date we observed them enter
    previous = set()
    for index, date in enumerate(dates):
        present = by_date[date]
        if index == 0:
            # The first snapshot is a starting state, not an observed entry.
            # We do not know when those artists arrived, so they can never
            # produce a spell — counting them would bias the median downward.
            previous = present
            continue
        for artist_id in present - previous:
            active[artist_id] = date
        for artist_id in previous - present:
            if artist_id in active:
                spells.append(_days_between(active.pop(artist_id), date))
        previous = present

    if len(spells) < HALFLIFE_MIN_SPELLS:
        return {
            "available": False,
            "spells_needed": HALFLIFE_MIN_SPELLS,
            "spells_have": len(spells),
            "median_days": None,
        }
    return {
        "available": True,
        "spells_needed": HALFLIFE_MIN_SPELLS,
        "spells_have": len(spells),
        "median_days": statistics.median(spells),
    }


def _days_between(start, end):
    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days


def _weeks_between(start, end):
    return _days_between(start, end) // 7


def build(snapshot_rows, genre_rows, skipped, generated_at):
    """Assemble the full dashboard payload."""
    dates = sorted({row["snapshot_date"] for row in snapshot_rows})
    events = entry_exit_events(snapshot_rows)
    divergences = divergence(snapshot_rows)
    popularity = mainstreamness(snapshot_rows)

    return {
        "generated_at": generated_at,
        "snapshot_dates": dates,
        "data_quality": {"skipped_files": skipped, "snapshot_count": len(dates)},
        "headline": _headline(dates, events, divergences, popularity),
        "rank_timeline": rank_timeline(snapshot_rows),
        "events": events,
        "divergence": divergences,
        "mainstreamness": popularity,
        "genre_mix": genre_mix(genre_rows, snapshot_rows),
        "survival": new_artist_survival(snapshot_rows),
        "half_life": rotation_half_life(snapshot_rows),
    }


def _headline(dates, events, divergences, popularity):
    latest_divergence = [d for d in divergences if d["kind"] == "artist"]
    latest_popularity = [
        p for p in popularity if p["kind"] == "artist" and p["time_range"] == "short_term"
    ]
    cutoff = _cutoff_date(dates)
    recent = [e for e in events if e["snapshot_date"] > cutoff and e["kind"] == "artist"]

    return {
        "divergence_artists": latest_divergence[-1]["overlap"] if latest_divergence else None,
        "mean_popularity": latest_popularity[-1]["mean"] if latest_popularity else None,
        "entries_7d": sum(1 for e in recent if e["event"] in ("entered", "re_entered")),
        "exits_7d": sum(1 for e in recent if e["event"] == "exited"),
    }


def _cutoff_date(dates):
    if not dates:
        return ""
    return (dt.date.fromisoformat(dates[-1]) - dt.timedelta(days=7)).isoformat()


def main():
    snapshot_rows, genre_rows = load_rows(DERIVED_ROOT)
    _, _, skipped = _skipped_from_raw()
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = build(snapshot_rows, genre_rows, skipped, generated_at)
    SITE_ROOT.mkdir(parents=True, exist_ok=True)
    (SITE_ROOT / "data.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote site/data.json — {len(payload['snapshot_dates'])} snapshots")
    if skipped:
        print(f"WARNING: {len(skipped)} unreadable raw files, see data_quality in data.json")
    return 0


def _skipped_from_raw():
    # build_derived is the only thing that reads raw, so ask it what it skipped.
    from tools.build_derived import RAW_ROOT, build_rows

    return build_rows(RAW_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
