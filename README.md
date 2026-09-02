# Spotify Taste Drift Tracker

Snapshots my Spotify top artists and top tracks every day, stores them
immutably, and renders a static dashboard showing how my taste moves over time.

Spotify's top-artists / top-tracks endpoints only ever return *today's* answer —
there is no history endpoint and no way to backfill. So the dataset does not
exist until you start capturing it, and every day you miss is gone for good.
That constraint drives the whole design.

## Status

Design approved. Implementation not started.

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
