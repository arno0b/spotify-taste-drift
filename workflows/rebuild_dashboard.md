# Workflow: Rebuild the dashboard

## Objective
Regenerate `data/derived/` and `site/data.json` from the raw snapshots, and
publish the dashboard to GitHub Pages.

## Trigger
Runs automatically after every capture in `.github/workflows/snapshot.yml`.
Run manually after changing any metric definition.

## Tools
1. `python tools/build_derived.py` — raw JSON to tidy CSVs
2. `python tools/build_metrics.py` — CSVs to `site/data.json`

Always in that order. Both do a full rebuild from scratch, which is the point:
change a metric, rerun, and the new definition applies to all history.

## Expected output
`data/derived/snapshots.csv`, `data/derived/artist_genres.csv` (always empty —
see below), and `site/data.json`, all committed. Pages redeploys from `site/`.

## Publishing

Pages uses **`build_type: workflow`**, not deploy-from-a-branch. That is not a
preference: deploy-from-a-branch only permits `/` or `/docs` as the publishing
directory, `docs/` already holds the specs and plans, and the site lives in
`site/`. The `deploy` job uploads `site/` as an artifact instead.

The deploy job checks out `ref: main` rather than the workflow's starting SHA,
so it publishes the commit the snapshot job just pushed.

## Previewing locally

```bash
python -m http.server 8000 --directory site
```

Then open <http://127.0.0.1:8000>. There is no build step, so what you see
locally is what deploys.

## Edge cases

- **`SKIPPED <path>` printed** — a raw file is unreadable. It is excluded and
  surfaced as a banner on the dashboard. Inspect the file; if it is truncated,
  the capture that wrote it was interrupted before the atomic rename landed.
- **`0 genre rows`** — expected and permanent. Spotify withdrew `genres` from
  the top-item endpoints. `artist_genres.csv` is still written, empty, so the
  file contract does not change. Not a fault.
- **`survival.available` or `half_life.available` still false** — expected until
  8 weeks of history and 10 completed spells exist. The dashboard shows an empty
  framed figure with the date it will arrive. Not a bug.
- **Figure 1 shows a ranked list instead of lines** — correct with a single
  snapshot. There is no movement to plot yet; lines appear on the second day.
- **Empty `site/data.json` metrics with snapshots present** — check that
  `build_derived.py` ran first. `build_metrics.py` reads the CSVs, not raw.
- **Pages deploys but shows stale data** — the deploy job checks out `main`. If
  the snapshot job's push failed, the deploy publishes the previous commit.
  Check the snapshot job's log.
- **Every run commits even when nothing changed** — expected. `generated_at` in
  `data.json` always differs, so there is always a diff. This is deliberate: it
  keeps the repository active and stops GitHub disabling the scheduled workflow
  after 60 days of inactivity.

## Verifying the dashboard without a browser

There is a headless smoke test approach that catches contract breaks: execute
`render()` from `site/app.js` in node with a stubbed `document` and `Plot`, then
assert no element contains `undefined`, `NaN` or `[object Object]`. It proves
the data contract and the code path, not the visual result. Only a browser can
confirm how it looks.
