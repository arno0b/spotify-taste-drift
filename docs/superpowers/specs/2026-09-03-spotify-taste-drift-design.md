# Spotify Taste Drift Tracker — Design

- **Date:** 2026-09-03
- **Status:** Approved design, pending spec review
- **Author:** Sakif + Claude

## 1. Overview

A personal system that snapshots my Spotify top artists and top tracks every
day, stores them immutably, and renders a static dashboard — written as a data
essay — showing how my taste moves over time. A second page lets a visitor run
the same analysis on their own account, entirely in their browser.

The project exists because Spotify's `top-artists` / `top-tracks` endpoints only
ever return *today's* answer. There is no history endpoint and no way to
backfill. The dataset does not exist until we start capturing it, and every day
we do not capture is permanently lost.

That single fact drives every decision below.

## 2. Goals

- Capture top artists and top tracks daily, across all three Spotify time
  ranges, without gaps.
- Preserve captured data losslessly so future analyses can be run over the full
  history.
- Render a static dashboard that makes taste movement legible.
- Require zero ongoing manual effort after setup.

## 3. Non-goals

Explicitly out of scope. These are excluded by decision, not oversight:

- **Audio features / audio analysis** (danceability, valence, energy, tempo).
  Spotify revoked these endpoints for new apps in November 2024. Not available,
  not worked around.
- **Recommendations, related-artists, featured/category playlists.** Same
  deprecation.
- **Spotify as a source of genres or popularity.** Spotify no longer returns
  `genres` or `popularity` on `/me/top/*` — the keys are absent, not empty — and
  a development-mode app receives 403 on `/v1/artists` and `/v1/tracks`.
  Both fields are instead recovered from MusicBrainz and Deezer; see §16. The
  earlier conclusion that they were permanently lost was wrong: it assumed
  Spotify was the only source.
- **Liked-songs library diffing.** Considered and declined; can be added later
  without redesign.
- **Recently-played polling / play counts.** Considered and declined; this is a
  separate project.
- **Server-side multi-user.** No accounts, no server, and above all no custody
  of anyone else's Spotify refresh tokens. Visitors can run the tool on their
  own account, but entirely client-side — see §9.5.
- **Any database.** Files in git are the store.

## 4. Decisions log

| Decision | Choice | Reason |
|---|---|---|
| Where it runs | GitHub Actions cron | Free, no machine to keep powered on, and commits give version history for free |
| Output surface | Static dashboard on GitHub Pages, public repo | Drift data only becomes legible as charts; public repo gets Pages free |
| Capture scope | Top artists + top tracks only | Tight scope, ships fast, extensible later |
| Pipeline shape | Layered, raw-is-sacred | Analysis can be rewritten and replayed over all history |
| Auth flow | Classic Authorization Code (client secret) | PKCE rotates the refresh token on every refresh, which breaks a stateless runner |
| Cadence | Daily, not weekly | Scheduled Actions get delayed or dropped; a dropped daily run is a non-event, a dropped weekly run is a lost data point |
| Audience | Public dashboard plus a client-side self-serve page | Visitors can run it on themselves without a server or any custody of their credentials |
| Visual direction | Editorial / data essay | The content is an argument from data; also avoids the dark-and-green pastiche every other Spotify-stats project lands on |
| Page structure | Generated lede, then stat tiles, then captioned figures | The lede is the one element that stays interesting while the charts are still thin |
| Sparse state | Empty framed figure with a computed arrival date | Never draw a shape that is not real data; the thin early weeks become part of the story |
| Rank figure | Emphasis and context, all 50 drawn | Shows everything honestly, degrades gracefully in week one, and keeps artists comparable against each other |

## 5. Architecture

Three layers. Each is a pure function of the layer above it. Everything below
`data/raw/` can be deleted and rebuilt identically.

```
data/raw/          immutable, append-only, never edited
      |
      v  build_derived.py   (full rebuild every run)
data/derived/      tidy tables
      |
      v  build_metrics.py   (full rebuild every run)
site/data.json     precomputed dashboard metrics
```

Full rebuild rather than incremental update is deliberate: it costs under a
second for years of small files, and it means a corrected or improved metric is
retroactively applied to all history.

### Repo layout

```
spotify project/
├── .github/workflows/snapshot.yml    # daily cron
├── tools/
│   ├── mint_refresh_token.py         # one-time, local, browser consent
│   ├── spotify_auth.py               # refresh_token -> access_token
│   ├── snapshot_top_items.py         # capture -> data/raw/
│   ├── build_derived.py              # raw     -> data/derived/
│   └── build_metrics.py              # derived -> site/data.json
├── workflows/
│   ├── capture_snapshot.md
│   └── rebuild_dashboard.md
├── tests/
│   ├── fixtures/                     # hand-written snapshot dirs
│   ├── test_build_derived.py
│   └── test_build_metrics.py
├── site/
│   ├── index.html  app.js  style.css # the dashboard
│   ├── try/index.html  try/try.js    # self-serve, PKCE, client-side only
│   └── data.json                     # generated, committed
├── data/
│   ├── raw/<YYYY-MM-DD>/*.json       # immutable
│   └── derived/snapshots.csv, artist_genres.csv
├── .env.example
├── requirements.txt
└── docs/superpowers/specs/
```

Follows the WAT convention from `CLAUDE.md`: deterministic Python in `tools/`,
markdown SOPs in `workflows/`, secrets only in `.env`.

## 6. Data flow

Daily at 06:00 UTC:

1. **Auth** — `spotify_auth.py` exchanges `SPOTIFY_REFRESH_TOKEN` plus
   client id/secret for a one-hour access token.
2. **Capture** — `snapshot_top_items.py` makes 6 calls:
   `{artists, tracks} x {short_term, medium_term, long_term}`, `limit=50`.
   Each response is written verbatim to
   `data/raw/<date>/top_<kind>_<time_range>.json`, plus a `meta.json`.
3. **Derive** — `build_derived.py` walks all of `data/raw/` and rebuilds
   `snapshots.csv` and `artist_genres.csv` from scratch.
4. **Metrics** — `build_metrics.py` reads the derived tables and writes
   `site/data.json`.
5. **Commit and deploy** — commit any changes, push, publish the site.

### Idempotency

If `data/raw/<today>/` already exists, capture exits 0 without writing. Manual
re-runs, double-fired crons, and retries cannot corrupt or duplicate a day.

## 7. Data contracts

### `data/raw/<YYYY-MM-DD>/meta.json`

```json
{
  "captured_at": "2026-09-03T06:00:12Z",
  "script_version": "1.0.0",
  "calls": {
    "top_artists_short_term": {"status": 200, "count": 50},
    "top_tracks_long_term":   {"status": 500, "count": 0}
  },
  "partial": true
}
```

`partial` is true when any of the 6 calls failed.

### `data/derived/snapshots.csv`

One row per entity per time range per day.

| column | type | notes |
|---|---|---|
| `snapshot_date` | date | `YYYY-MM-DD`, UTC |
| `captured_at` | datetime | ISO 8601 UTC |
| `time_range` | enum | `short_term` / `medium_term` / `long_term` |
| `kind` | enum | `artist` / `track` |
| `rank` | int | 1 to 50 |
| `spotify_id` | string | |
| `name` | string | |
| `popularity` | int | Always empty since 2026-09-13 — Spotify withdrew the field. Column retained so the CSV contract is stable. |
| `primary_artist_id` | string | tracks only; empty for artists |
| `album_id` | string | tracks only; empty for artists |
| `duration_ms` | int | tracks only; empty for artists |

### `data/derived/artist_genres.csv`

Long form, so genre membership is tracked *as of each snapshot* rather than
last-known-value.

| column | type |
|---|---|
| `snapshot_date` | date |
| `time_range` | enum |
| `artist_id` | string |
| `genre` | string |

Spotify attaches genres to artists only, never to tracks. All genre analysis is
therefore artist-based. No metric in this spec requires track-level genres.

## 8. Metric definitions

Precise definitions so the implementation is unambiguous. "Consecutive
snapshots" always means consecutive *available* snapshot dates, not consecutive
calendar days — this makes every metric gap-tolerant.

### 8.1 Rank timeline

For each `(time_range, kind)`, the series of `(snapshot_date, spotify_id, rank)`.
Absence from a snapshot means outside the top 50, not rank 51.

### 8.2 Entry / exit events

Between consecutive snapshots `d_prev` and `d`:

- **entered** — present in `d`, absent in `d_prev`
- **exited** — present in `d_prev`, absent in `d`
- **re-entered** — entered, *and* present in any snapshot before `d_prev`

Every entity in the first-ever snapshot is flagged `initial: true` and excluded
from entry counts, so day one does not register as 50 entries.

### 8.3 Short-vs-long divergence

For a given date and kind, with `A` = `short_term` ids and `B` = `long_term` ids:

```
overlap = |A intersect B| / min(|A|, |B|)
```

Range 0 to 1. High means settled taste, low means an exploration phase. `min()`
rather than a hardcoded 50 so short result sets are handled correctly.

### 8.4 Genre mix over time

For each `(date, time_range)`, an artist at rank `r` carries weight `w = 1/r`,
split equally across its genres. Shares are normalised to sum to 1.

Genres come from the enrichment cache (§16), not from Spotify.

**Unresolved artists are excluded from the shares entirely**, not bucketed as
"unclassified". An artist we could not identify is a gap in our knowledge, not a
genre someone listens to; folding it in as a band would conflate "42 percent
unknown music" with "we identified 58 percent of it". Coverage is published
separately as `genre_coverage` and stated in the figure's caption.

### 8.5a Horizon shift — ascending and fading

Spotify returns three time horizons in **every** capture. Comparing short_term
against long_term inside a single snapshot is therefore real drift available on
day one, without waiting for history to accumulate. This was overlooked in the
original design, which measured drift only across snapshots and left the page
with almost nothing to show for its first weeks.

- **Ascending** — present in the last four weeks, ranked lower or absent over
  the year. Ordered by the climb. An entry absent from a 50-item list is treated
  as sitting at rank 60, so "absent over the year" reads as the strongest climb
  rather than being dropped from the comparison.
- **Fading** — in the yearly top 50, absent from the last four weeks entirely.
- **Counts** — new to rotation, dropped away, steady.

A date missing either horizon is skipped: half a comparison would read as
everything having appeared from nowhere.

### 8.5b Genre shift

Genre share in the last four weeks against the last year. Converts the genre
figure from composition into drift. A genre absent from one window stays null so
the page renders a dash — dropping out entirely is not the same as holding a
zero share.

### 8.5 Reach

Median Deezer fan count of the artists in a list, per `(date, time_range)`.

Replaces the old mainstream-ness metric, which measured Spotify's withdrawn
0–100 popularity score. It is deliberately named differently because it measures
a different thing on a different platform.

Median rather than mean: fan counts span five orders of magnitude, so a single
very large artist would drag a mean far above anything typical of the list.

### 8.6 New-artist survival

A cohort is the set of artists whose first-ever appearance in `short_term`
artists is on date `d0`. For each week `w` after `d0`, the fraction of that
cohort still present. **Requires at least 8 weeks of history to display.**

### 8.7 Rotation half-life

For `short_term` artists, a completed spell is an entry followed by a later
exit; its length is the number of days between those two snapshot dates. Report
the median across all completed spells. **Requires at least 10 completed spells
to display.**

### Insufficient-data gating

Metrics 8.6 and 8.7 render an explicit "insufficient data — needs N more weeks"
placeholder until their thresholds are met. They are built in milestone 3 but
will show nothing meaningful for the first couple of months, which is expected.

Note also that `long_term` is approximately a trailing year, not lifetime.

## 9. Dashboard and self-serve page

Two static pages. Vanilla JS plus Observable Plot loaded from CDN at a pinned
exact version — one charting dependency, no build step, no bundler.

### 9.1 Visual direction

Editorial: the page reads as a data essay, not an instrument panel.

- Serif for the lede and figure captions, sans for labels and UI chrome.
- Warm off-white ground, near-black text, a single green accent.
- Charts sit directly on the page ground with no card borders or drop shadows.
  They are figures in an article, not widgets on a grid.
- Narrow measure for the text column; figures may exceed it.
- Every figure is numbered and captioned. The caption says what the reader is
  looking at, not what the chart type is.

### 9.2 Page structure

In order:

1. **Lede** — one sentence generated from the current data, restated on every
   rebuild. It states the *finding*, not the metric: when artist and track
   divergence differ by 15 points or more, it says so — "I keep my artists and
   change the songs: 54% of the artists I'm playing now are long-term regulars,
   but only 24% of the tracks are." Otherwise it falls back to the single
   divergence figure. Available from one day's capture, so it is never empty.
2. **Byline** — snapshot count and date range.
3. **Stat tiles** — divergence, entries 7d, exits 7d. Below the lede, not
   above it.
4. **Figures**, each numbered and captioned.

### 9.3 Figures

| # | Figure | Notes |
|---|---|---|
| 1 | Rank movement | Ranked table: position, name, change against the snapshot nearest a week back, and an inline sparkline. Replaced a 53-line bump chart that drew fifty near-flat strands to convey that four entries had moved. A gap in a sparkline means the entry left the top fifty and is deliberately not interpolated. |
| 2 | Ascending and fading | Two tables side by side, from §8.5a. The page's main drift figure. |
| 3 | Genre shift | Table, four weeks against the year, from §8.5b |
| 4 | Short-vs-long divergence | Line, 0 to 1 |
| 5 | Reach | Median Deezer fans, log scale |
| 6 | New-artist survival | Gated, ≥ 8 weeks |
| 7 | Rotation half-life | Gated, ≥ 10 completed spells |
| — | Then versus now | Slope chart between two dates. Deferred to M6: needs roughly a month of history, and reads the same data as Figure 1. |
| — | Recent changes | Entry / exit feed, most recent first |

Figure 1 draws all fifty rather than only the top ten because a rank chart's
purpose is showing artists trade places, which a filtered chart cannot do.

### 9.4 Sparse state

The first weeks have little data, and the page must look deliberate rather than
broken. Two rules:

- **Never draw a shape that is not real data.** No illustrative curves, no
  sample shapes, no placeholder trends — even labelled as examples. The page's
  entire claim is that every mark is a real observation.
- **A figure that is not ready keeps its slot**, drawn as an empty framed area,
  with a caption stating what it needs and the date it arrives: "Needs eight
  weeks of history. First appears 2 November 2026." The date is computed from
  the metric's own `weeks_have` / `spells_have` counters, never hardcoded.

Note that only Figures 5 and 6 are genuinely gated. The lede, all three tiles,
and Figures 1–4 work from a single day's capture, so week one is thin, not
blank.

### 9.5 Self-serve page

A second page letting a visitor run the analysis on their own account.

- **Auth: PKCE, in the browser.** No client secret, so nothing is shipped that
  could be extracted. The refresh-token rotation that ruled PKCE out for the
  cron (§4) is irrelevant here: this page never stores a refresh token, it uses
  the one-hour access token for the visit and discards it.
- **No data leaves the browser.** Analysis runs client-side; nothing is sent to
  any server, because there is no server. Say this on the page, plainly.
- **Instant result.** Short-vs-long divergence is computable from a single
  visit, since it compares two lists fetched in the same session. That is the
  visitor's immediate payoff.
- **Accumulation is optional and local.** Snapshots persist to `localStorage`,
  so a visitor who returns builds their own history. It lives in that browser
  only — cleared site data loses it, and it never syncs anywhere. The page must
  say so rather than implying durability it cannot provide.
- Same Spotify client ID as the cron, with the Pages origin added as a second
  redirect URI.

**The 25-user cap is the hard constraint here.** See §14.

## 10. Auth setup

One-time, manual, local:

1. Register an app at developer.spotify.com. Dev mode is sufficient; see §14 for
   the user cap it imposes on the self-serve page.
2. Set two redirect URIs:
   - `http://127.0.0.1:8888/callback` — for minting the cron's refresh token.
     Spotify rejects `localhost` and requires the loopback IP literal.
   - `https://arno0b.github.io/spotify-taste-drift/try/` — for the self-serve
     page's PKCE flow.
3. Run `python tools/mint_refresh_token.py`. It starts a local server, opens a
   browser for consent, and prints the refresh token.
4. Store `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`
   as GitHub repo secrets, and in a gitignored `.env` for local runs.

Single scope: `user-top-read`. Nothing else is requested.

## 11. Error handling

Governing rule: **a silent failure is a permanent data gap.** Nothing fails
quietly.

| Condition | Behaviour |
|---|---|
| Token refresh fails | Non-zero exit, Action fails, GitHub emails |
| HTTP 429 | Respect `Retry-After`, 3 retries with exponential backoff |
| Partial capture | Write what succeeded, set `partial: true`, **still fail the job loudly**; derive layer tolerates partial days |
| Empty top-50 | Valid data. Record it, do not error |
| Malformed JSON in raw | Derive layer skips the file and continues, so one bad file cannot permanently block the build — but it records the skip in `data.json`, and the dashboard renders a data-quality banner naming the affected dates. Loud, without being fatal. |

## 12. Testing

`build_derived.py` and `build_metrics.py` are pure functions over files. They
are where every real bug will live, and they get TDD coverage against
hand-written fixture snapshot directories.

Fixture cases that must be covered:

- a partial day (some calls failed)
- a multi-day gap in snapshots
- an entity that exits and later re-enters
- two entities with equal popularity (tie handling)
- the first-ever snapshot (no entry events emitted)
- an artist with zero genres
- a day with fewer than 50 results

The Spotify client is tested against recorded fixtures. **No live API calls in
CI.** The dashboard gets a smoke test that it loads and renders; no deeper
frontend testing.

## 13. Risks and watch-items

- **60-day workflow disable.** GitHub auto-disables scheduled workflows after 60
  days of repo inactivity, and pushes made with the default `GITHUB_TOKEN` are
  unreliable as "activity". Mitigation: commit on every run — `meta.json` always
  changes, so there is always a diff. If it still gets disabled, switch the push
  to a personal access token so commits count as user activity. Watch this at
  the 60-day mark.
- **Further Spotify deprecations.** The November 2024 cuts show these endpoints
  are not guaranteed. The raw layer means anything already captured survives an
  endpoint disappearing.
- **Refresh token revocation.** Changing the Spotify password or removing the
  app's authorisation invalidates the token. Symptom is a failing daily job;
  remedy is re-running `mint_refresh_token.py`.
- **Cron drift.** Actions schedules are best-effort. Daily cadence absorbs this.
- **Pages on a private repo.** GitHub Pages from a private repo requires a paid
  plan. See open decision below.

## 14. Repo visibility

**Decided 2026-09-03: public repo.** This enables GitHub Pages on a free
account, which is the chosen output surface.

Consequences, accepted knowingly:

- The captured listening history is public. Anyone can read which artists and
  tracks are in my top 50 and how that has changed. There is nothing here beyond
  music taste — no email, no playlists, no play timestamps.
- Actions secrets remain safe in a public repo. GitHub does not expose secrets
  to workflows triggered by pull requests from forks, and the daily job runs
  only on `schedule` and `workflow_dispatch`.
- The `.gitignore` must keep `.env` out of the repo permanently. In a public
  repo a single committed secret is a disclosed secret, so `mint_refresh_token.py`
  writes only to stdout and never to a tracked file.
- The three secrets live in GitHub repo secrets, never in the tree.

### The 25-user cap

A Spotify app in development mode serves at most **25 users**, each added by
hand in the developer dashboard using their Spotify account email. Anyone not on
that list gets an auth error, not a degraded experience.

Consequences for the self-serve page (§9.5):

- It works for roughly 25 named people, not the public. Design for friends, not
  for traffic.
- The failure is indistinguishable from a bug unless handled. The page must
  detect the auth rejection and say plainly that access is by invitation and how
  to ask for it — never leave a visitor staring at a broken login.
- Lifting the cap means applying for extended quota mode. Spotify's review is
  aimed at real organisations and solo projects are frequently rejected. Treat
  approval as unlikely and do not design around getting it.

Verify the current cap and application terms against Spotify's developer docs
before building the self-serve page — this is the constraint most likely to have
changed.

### Reconsidering visibility

If this is reconsidered later, moving to a private repo costs nothing in code —
the dashboard is static files and can be served locally with
`python -m http.server` from `site/` — but published Pages would need a paid
plan, and the git history would remain public unless the repo is recreated.

## 15. Milestones

**M1 — Capture, shipped first and fast.** Auth, `snapshot_top_items.py`, the
daily Action, raw commits. No analysis at all. This is urgent in a way the rest
is not: every day before M1 lands is a data point that can never be recovered.

**M2 — Derive layer.** `build_derived.py` plus its tests.

**M3 — Metrics.** `build_metrics.py` plus its tests, including the gated
long-horizon metrics.

**M4 — Dashboard.** Static site, wired to `data.json`, published to Pages.

**M5 — Self-serve page.** PKCE login, instant divergence, optional local
accumulation, and an honest not-on-the-allowlist state.

**M6 — Unlock.** Once enough history exists, verify the gated metrics render,
add Figure 7 (then versus now), and tune the genre weighting against real data.


## 16. Enrichment: genres and reach

Spotify withdrew both fields, but it is not the only source for either.

```
Spotify artist ID
  -> MusicBrainz /url lookup on the open.spotify.com URL   (exact identity)
  -> MusicBrainz artist: genres, plus linked Deezer URL
  -> Deezer /artist/<id>: fan count                        (reach)
```

**Identity is matched by URL relationship, never by artist name.** Name matching
was tested and returns wrong artists that look plausible: "Joji" matched an
unrelated act with 122 fans rather than 562,912, and "Øneheart" matched "One
Heart". This is not a tunable heuristic — it is a correctness requirement.

Results are cached in `data/enrichment/artists.json`, keyed by Spotify artist ID.
Two properties follow:

- **Retroactive.** The raw layer has always stored artist IDs, so resolving an
  artist fills in genres for every snapshot that ever contained them — including
  snapshots captured before enrichment existed. This is the raw-is-sacred design
  paying off exactly as intended.
- **Cheap after the first run.** Only unseen artists are looked up. The first run
  resolved 83 artists in about ten minutes; a typical day adds none or one.

MusicBrainz is a free community service that asks for at most one request per
second. The tool paces itself, retries 503 with backoff, sends a meaningful
User-Agent, and checkpoints to disk every 20 lookups. Do not parallelise it.

**Coverage is partial and is reported rather than hidden.** The first run
resolved 71 of 83 artists; misses are anonymous lo-fi and production-library
acts and regional artists that MusicBrainz genuinely does not catalogue. Misses
are retried after 30 days, since MusicBrainz is community-edited and grows.

The enrichment step in the Action is `continue-on-error`: unlike a missed
capture, a missed enrichment costs nothing permanent, because it can always be
run again against data already on disk.

See `workflows/enrich_artists.md`.

## 17. Payload budget

`data.json` is fetched on every page load and had reached 1.3 MB at ten
snapshots, on track for tens of megabytes within a year. It is now held to
roughly 400 KB by shipping only what is rendered:

- `rank_timeline` is capped at the most recent 60 snapshot dates, and artist
  names are normalised into a separate `names` map rather than repeated on every
  one of thousands of rows.
- `horizon`, `genre_shift` and `genre_coverage` ship the latest date only. They
  are single-moment comparisons; no time series of them is drawn.
- `genre_mix` is not shipped at all. It fed the stacked-area chart that the
  genre-shift table replaced, and is now an internal input to `genre_shift`.
- `events` is capped at the most recent 400.

Every long-horizon metric is computed here from the **full** derived history, so
trimming the browser payload costs the analysis nothing. `data/derived/` and
`data/raw/` keep everything.