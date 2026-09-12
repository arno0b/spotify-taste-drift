# Workflow: Enrich artists with genres and reach

## Objective
Recover the two fields Spotify withdrew — genres and a popularity measure — from
sources that still publish them, and cache the result so it is fetched once per
artist rather than once per day.

## Trigger
Runs in `.github/workflows/snapshot.yml` after every capture. Also
`python tools/enrich_artists.py` locally.

## Why this exists

Spotify removed `genres` and `popularity` from `/me/top/*`, and a
development-mode app gets **403** on `/v1/artists` and `/v1/tracks`. Neither
field is reachable through Spotify at all. Both are recoverable elsewhere:

```
Spotify artist ID
  -> MusicBrainz /url?resource=https://open.spotify.com/artist/<id>  (exact identity)
  -> MusicBrainz /artist/<mbid>?inc=genres+tags+url-rels             (genres, Deezer link)
  -> Deezer /artist/<deezer_id>                                      (nb_fan -> reach)
```

## Identity matching — do not use artist names

Name matching was tried and **rejected**. It returns wrong artists that look
entirely plausible:

| Artist | By name search | Via MusicBrainz bridge |
|---|---|---|
| Joji | 122 fans — a different act | 562,912 fans |
| Øneheart | matched "One Heart", 209 fans | 28,777 fans |

Every match goes through MusicBrainz's URL relationships, which link the exact
Spotify and Deezer entities. Never reintroduce a name-based fallback.

## Tool
`python tools/enrich_artists.py`

## Caching

Results live in `data/enrichment/artists.json`, keyed by **Spotify artist ID**.
Two consequences worth understanding:

- **It is retroactive.** Because the raw layer has always stored artist IDs,
  resolving an artist today fills in genres for every snapshot that ever
  contained them, including ones captured before this tool existed.
- **A daily run is nearly free.** Only artists never seen before are looked up.
  The first run resolved 83 artists in roughly ten minutes; a typical day adds
  none or one.

## Rate limits

MusicBrainz asks for **no more than one request per second** and answers `503`
when exceeded. The tool paces itself at 1.15s between calls and retries `503`
with exponential backoff. A meaningful `User-Agent` is required by their policy
and is set in the module.

Do not parallelise this. It is a free community service.

Progress is checkpointed to disk every 20 lookups, so an interrupted run is not
wasted.

## Expected output

`data/enrichment/artists.json`, committed. Entries carry `status` of `ok`,
`not_found`, or `error`.

First real run: **83 artists seen, 71 resolved, 56 with genres, 64 with reach.**

## Edge cases

- **`not_found`** — nobody has linked that Spotify artist in MusicBrainz. Common
  for anonymous lo-fi and production-library acts (`Cozy Room`, `Jazzy Coffee`,
  `Warm Blanket`) and for regional artists (`Meghdol`). Not a fault, and not
  worth chasing.
- **Misses are retried after 30 days.** MusicBrainz is community-edited, so an
  artist absent today may be added later. Successful entries are never
  re-fetched.
- **The Action step is `continue-on-error`.** MusicBrainz and Deezer are free
  services that can be down. If enrichment fails, the previous cache is still on
  disk and the pipeline proceeds. Enrichment is an improvement, not a
  dependency — unlike a missed capture, a missed enrichment costs nothing
  permanent because it can always be run again.
- **Coverage is reported, not hidden.** `genre_coverage` in `data.json` states
  what fraction of each list was resolved, and the dashboard caption says so.
  Genre shares are computed over resolved artists only: an unresolved artist is
  a gap in knowledge, not a genre, and bucketing it as "unclassified" would
  conflate the two.
- **Genres fall back to community tags** when MusicBrainz has no curated genre.
  Tags are messier but far better than nothing for a long-tail artist.
