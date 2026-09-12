# Workflow: Capture daily snapshot

## Objective
Record today's Spotify top artists and tracks into `data/raw/<date>/` before
the data is gone. Spotify has no history endpoint — a missed day is permanent.

## Trigger
`.github/workflows/snapshot.yml`, daily at 06:00 UTC. Also `gh workflow run
"Daily snapshot"` on demand.

## Required inputs
Repo secrets `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SPOTIFY_REFRESH_TOKEN`. Locally, the same three in `.env`.

## Tool
`python tools/snapshot_top_items.py`

Run it locally with the `.env` values loaded:

```powershell
Get-Content .env | ForEach-Object { if ($_ -match '^\s*([^#=]+?)\s*=\s*(.+)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }; python tools/snapshot_top_items.py
```

Note the regex uses `(.+)` not `(.*)` deliberately — `Set-Item` throws on an
empty value, so blank lines like an unfilled `SPOTIFY_REFRESH_TOKEN=` must be
skipped rather than set.

## Expected output
`data/raw/<YYYY-MM-DD>/` with six response files plus `meta.json`, committed
to the branch. Exit 0 on a clean capture, exit 1 on a partial one.

The date is **UTC**, so the directory name can be "yesterday" relative to your
local clock. That is correct and keeps local runs consistent with the Action.

## What the API actually returns

Verified against a real capture on 2026-09-13. `/me/top/artists` items carry
only: `external_urls, href, id, images, name, type, uri`.

**There is no `genres` and no `popularity`.** Spotify withdrew both, and a
development-mode app also gets **403 Forbidden** on `/v1/artists` and
`/v1/tracks`, so there is no fallback route to them. The genre-mix and
mainstream-ness metrics were removed for this reason. Do not try to reintroduce
them without first re-probing those two endpoints.

## Edge cases and what they mean

- **`Already captured`** — the day directory exists. Correct, not an error.
  Reruns are safe by design.
- **Fewer than 50 items** — normal. A real capture returned 44 `short_term`
  artists. This is why divergence divides by `min(|A|, |B|)` rather than 50.
- **Exit 1 with `PARTIAL CAPTURE`** — some calls failed. The data that did
  arrive was still written and committed. Check `meta.json` for which call
  failed and its status code. No action needed unless it repeats.
- **`invalid_grant` on token refresh** — the refresh token was revoked, usually
  by a Spotify password change or removing the app's authorisation. Re-run
  `python tools/mint_refresh_token.py` and update `.env` and the repo secret.
- **`ModuleNotFoundError: No module named 'tools'`** — should not recur. Each
  entry point inserts the repo root into `sys.path`, because running
  `python tools/x.py` otherwise puts only `tools/` on the path. Tests never
  catch this since `pyproject.toml` sets `pythonpath` for pytest only.
- **Empty `items`** — valid data. Spotify has not computed top items for that
  time range yet. Record it, do not treat it as failure.
- **Workflow silently stops running** — GitHub disables scheduled workflows
  after 60 days of repo inactivity, and `GITHUB_TOKEN` pushes are unreliable as
  "activity". Re-enable in the Actions tab. If it recurs, switch the push to a
  personal access token so the commits count as user activity.
