import json

from tools.enrich_artists import artist_ids_from_raw, enrich

NOW = "2026-09-13T06:00:00Z"


def write_day(root, date, artists):
    day = root / date
    day.mkdir(parents=True)
    (day / "top_artists_short_term.json").write_text(
        json.dumps({"items": [{"id": i, "name": n} for i, n in artists]})
    )
    (day / "meta.json").write_text(json.dumps({"captured_at": f"{date}T06:00:00Z"}))


def ok_lookup(spotify_id):
    return {
        "mbid": f"mb-{spotify_id}",
        "genres": ["dream pop", "shoegaze"],
        "deezer_id": 42,
        "deezer_fans": 1000,
    }


def test_collects_unique_artist_ids_across_all_days(tmp_path):
    write_day(tmp_path, "2026-09-11", [("a1", "First"), ("a2", "Second")])
    write_day(tmp_path, "2026-09-12", [("a2", "Second"), ("a3", "Third")])

    assert artist_ids_from_raw(tmp_path) == {"a1": "First", "a2": "Second", "a3": "Third"}


def test_ignores_tracks_and_staging_dirs(tmp_path):
    write_day(tmp_path, "2026-09-11", [("a1", "First")])
    (tmp_path / "2026-09-11" / "top_tracks_short_term.json").write_text(
        json.dumps({"items": [{"id": "t1", "name": "Song"}]})
    )
    (tmp_path / ".2026-09-12.staging").mkdir()

    assert artist_ids_from_raw(tmp_path) == {"a1": "First"}


def test_enriches_uncached_artists(tmp_path):
    cache = enrich({"a1": "First"}, {}, ok_lookup, now_iso=NOW)

    assert cache["a1"]["status"] == "ok"
    assert cache["a1"]["genres"] == ["dream pop", "shoegaze"]
    assert cache["a1"]["deezer_fans"] == 1000
    assert cache["a1"]["name"] == "First"
    assert cache["a1"]["fetched_at"] == NOW


def test_cached_artists_are_not_looked_up_again():
    def explode(spotify_id):
        raise AssertionError("must not re-look-up a cached artist")

    cache = {"a1": {"status": "ok", "genres": [], "fetched_at": "2026-01-01T00:00:00Z"}}

    result = enrich({"a1": "First"}, cache, explode, now_iso=NOW)

    assert result["a1"]["fetched_at"] == "2026-01-01T00:00:00Z"


def test_a_miss_is_cached_so_it_is_not_retried_every_day():
    cache = enrich({"a1": "First"}, {}, lambda _: None, now_iso=NOW)

    assert cache["a1"]["status"] == "not_found"
    assert cache["a1"]["genres"] == []


def test_a_stale_miss_is_retried_because_musicbrainz_data_improves():
    # MusicBrainz is community-edited: an artist absent today may be added later.
    cache = {
        "a1": {"status": "not_found", "genres": [], "fetched_at": "2026-01-01T00:00:00Z"}
    }

    result = enrich({"a1": "First"}, cache, ok_lookup, now_iso=NOW, retry_after_days=30)

    assert result["a1"]["status"] == "ok"
    assert result["a1"]["genres"] == ["dream pop", "shoegaze"]


def test_a_recent_miss_is_not_retried():
    cache = {
        "a1": {"status": "not_found", "genres": [], "fetched_at": "2026-09-12T00:00:00Z"}
    }

    def explode(spotify_id):
        raise AssertionError("must not retry a recent miss")

    result = enrich({"a1": "First"}, cache, explode, now_iso=NOW, retry_after_days=30)

    assert result["a1"]["status"] == "not_found"


def test_a_lookup_failure_leaves_the_existing_entry_untouched():
    # A network blip must not destroy good cached data.
    cache = {"a1": {"status": "ok", "genres": ["rock"], "fetched_at": "2026-01-01T00:00:00Z"}}

    def boom(spotify_id):
        raise RuntimeError("network died")

    result = enrich({"a1": "First"}, cache, boom, now_iso=NOW, retry_after_days=0)

    assert result["a1"]["genres"] == ["rock"]
    assert result["a1"]["status"] == "ok"


def test_entries_for_artists_no_longer_in_the_top_fifty_are_kept():
    # History still references them, so dropping them would break past snapshots.
    cache = {"old": {"status": "ok", "genres": ["jazz"], "fetched_at": NOW}}

    result = enrich({"a1": "First"}, cache, ok_lookup, now_iso=NOW)

    assert "old" in result
    assert result["old"]["genres"] == ["jazz"]


def test_progress_is_checkpointed_so_an_interrupted_run_is_not_wasted():
    # A full run is several minutes of rate-limited requests. Losing all of it
    # to one interruption would be needless.
    saved = []
    ids = {f"a{i}": f"Artist {i}" for i in range(5)}

    enrich(ids, {}, ok_lookup, now_iso=NOW, on_checkpoint=saved.append, checkpoint_every=2)

    assert len(saved) == 2                  # after the 2nd and 4th lookups
    assert len(saved[0]) == 2
    assert len(saved[1]) == 4


# --- choosing between duplicate Deezer profiles --------------------------------

class FakeHttp:
    """Deezer responses keyed by artist id."""

    def __init__(self, profiles):
        self.profiles = profiles
        self.asked = []

    def get(self, url, timeout=None):
        artist_id = url.rstrip("/").split("/")[-1]
        self.asked.append(artist_id)
        payload = self.profiles.get(artist_id)

        class R:
            status_code = 200 if payload else 404

            @staticmethod
            def json():
                return payload or {}

        return R()


def test_the_profile_with_the_catalogue_wins_not_the_first_one_listed():
    # MusicBrainz held a stub before the real profile, which gave Doja Cat 38
    # followers instead of 2,505,502.
    from tools.enrich_artists import _pick_deezer

    http = FakeHttp({
        "stub": {"nb_album": 0, "nb_fan": 38},
        "real": {"nb_album": 57, "nb_fan": 2505502},
    })

    assert _pick_deezer(["stub", "real"], http) == ("real", 2505502)


def test_order_does_not_matter():
    from tools.enrich_artists import _pick_deezer

    http = FakeHttp({
        "stub": {"nb_album": 1, "nb_fan": 171},
        "real": {"nb_album": 25, "nb_fan": 1670094},
    })

    assert _pick_deezer(["real", "stub"], http)[0] == "real"


def test_a_single_genuinely_small_artist_is_kept():
    from tools.enrich_artists import _pick_deezer

    http = FakeHttp({"only": {"nb_album": 35, "nb_fan": 14751}})

    assert _pick_deezer(["only"], http) == ("only", 14751)


def test_followers_break_a_tie_on_catalogue_size():
    from tools.enrich_artists import _pick_deezer

    http = FakeHttp({"a": {"nb_album": 5, "nb_fan": 10}, "b": {"nb_album": 5, "nb_fan": 900}})

    assert _pick_deezer(["a", "b"], http)[0] == "b"


def test_no_candidates_yields_no_reach_rather_than_an_error():
    from tools.enrich_artists import _pick_deezer

    assert _pick_deezer([], FakeHttp({})) == (None, None)


def test_entries_from_an_older_lookup_version_are_refetched():
    # Improving the lookup must replay over existing entries, the same way the
    # derived layer is rebuilt from raw.
    cache = {"a1": {"status": "ok", "genres": ["rock"], "fetched_at": NOW, "lookup_version": 1}}

    result = enrich({"a1": "First"}, cache, ok_lookup, now_iso=NOW)

    assert result["a1"]["genres"] == ["dream pop", "shoegaze"]
