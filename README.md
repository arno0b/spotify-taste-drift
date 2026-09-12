# Spotify Taste Drift Tracker

Snapshots my Spotify top artists and top tracks every day, stores them
immutably, and renders a static dashboard showing how my taste moves over time.

Spotify's top-artists / top-tracks endpoints only ever return *today's* answer —
there is no history endpoint and no way to backfill. So the dataset does not
exist until you start capturing it, and every day you miss is gone for good.
That constraint drives the whole design.

## Status

Capture is live. The daily cron runs at 06:00 UTC and the dashboard is published to GitHub Pages.

See [the design spec](docs/superpowers/specs/2026-09-03-spotify-taste-drift-design.md).

## How it works

```
data/raw/          immutable daily API responses, append-only
      |
      v  build_derived.py
data/derived/      tidy tables
      |
      v  build_metrics.py
site/data.json     precomputed dashboard metrics
```

A GitHub Actions cron captures daily, rebuilds everything downstream from raw,
and commits. Because every layer below `data/raw/` is a pure function of raw, an
improved metric can be replayed across all history rather than only applying
going forward.

## Note on the public repo

The captured data is public by design — this repo publishes to GitHub Pages.
It contains music taste only: top artists, top tracks, ranks, genres,
popularity. No email, no playlists, no play timestamps. Credentials live in
GitHub Actions secrets and never enter the tree.

## Running it on your own account

There is a self-serve page at [`/try/`](https://arno0b.github.io/spotify-taste-drift/try/)
that computes your short-vs-long divergence in your browser. It sends nothing
anywhere — there is no server. Snapshots accumulate in that browser's
localStorage only.

**Access is by invitation.** Spotify caps apps in development mode at 25 users,
each added by hand using their Spotify account email. If you would like to be
added, ask. If you sign in without being on the list, Spotify rejects it and the
page explains why rather than showing an error.

## What this does not measure

Spotify withdrew `genres` and `popularity` from the top-item endpoints, and
development-mode apps get 403 on `/v1/artists` and `/v1/tracks`. There is
therefore no genre analysis and no mainstream-ness measure, and no way to add
them. Audio features, recommendations and related-artists were revoked for new
apps back in November 2024.

What remains works from IDs and ranks alone: rank movement, entry and exit,
short-vs-long divergence, new-artist survival, and rotation half-life.
