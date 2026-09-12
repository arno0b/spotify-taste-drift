# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Current State

Spotify taste drift tracker. Captures top artists and tracks daily into an
immutable raw layer, rebuilds derived tables and dashboard metrics from raw on
every run, and publishes a static dashboard to GitHub Pages.

The daily capture is **live** — `.github/workflows/snapshot.yml`, 06:00 UTC.

**Commands:**
- `python -m pytest` — full suite, no network access required
- `python tools/snapshot_top_items.py` — capture today (idempotent)
- `python tools/build_derived.py` — raw to tidy CSVs
- `python tools/build_metrics.py` — CSVs to `site/data.json`
- `python -m http.server 8000 --directory site` — preview the dashboard
- `python tools/mint_refresh_token.py` — one-time, mint a refresh token

Load `.env` first when running anything that touches Spotify:

```powershell
Get-Content .env | ForEach-Object { if ($_ -match '^\s*([^#=]+?)\s*=\s*(.+)$') { Set-Item "env:$($Matches[1])" $Matches[2] } }
```

**Workflows:** `workflows/capture_snapshot.md`, `workflows/rebuild_dashboard.md`

**Design:** `docs/superpowers/specs/2026-09-03-spotify-taste-drift-design.md`
**Plan:** `docs/superpowers/plans/2026-09-03-spotify-taste-drift.md`

## Project-specific conventions

- **`data/raw/` is immutable.** Never edit or regenerate it — it cannot be
  re-fetched. Everything below it is disposable and rebuilt from scratch on
  every run, which is what lets an improved metric replay across all history.
- **Never make live Spotify calls in tests.** Pass a fake session or fixtures.
  The whole suite runs offline.
- **This is a public repo.** Secrets live only in `.env` (gitignored) and GitHub
  Actions secrets, never in the tree. The self-serve page's client ID is public
  by design — a PKCE client has no secret.
- **Never draw a shape that is not real data** in the UI. No illustrative
  curves, no sample trends, even labelled as examples. A figure that is not
  ready keeps its slot as an empty framed area captioned with the date it will
  arrive.
- **Entry points need the `sys.path` shim.** Running `python tools/x.py` puts
  only `tools/` on the path, so each entry point inserts the repo root before
  importing from the `tools` package. Tests never catch a missing shim because
  `pyproject.toml` sets `pythonpath` for pytest only.

## What Spotify will and will not give you

Verified against real captures, not documentation. Re-probe before assuming any
of it has changed.

- **`/me/top/{artists,tracks}` items carry no `genres` and no `popularity`.**
  The keys are absent, not empty. Artists return only `external_urls, href, id,
  images, name, type, uri`.
- **`/v1/artists` and `/v1/tracks` return 403** for a development-mode app, so
  there is no fallback route to those fields. Genre mix and mainstream-ness were
  removed for this reason; do not reintroduce them without re-probing.
- **Audio features, audio analysis, recommendations, related-artists** were
  revoked for new apps in November 2024.
- **Fewer than 50 items is normal.** A real capture returned 44 `short_term`
  artists. This is why divergence divides by `min(|A|, |B|)`.
- **Development mode caps the app at 25 users**, added by hand in the dashboard
  under User Management. This gates the self-serve page, not the cron.

## The WAT Architecture

Workflows (markdown SOPs in `workflows/`) say what to do. Agents — you — read
the workflow, run tools in order, handle failures, and ask when unsure. Tools
(Python in `tools/`) do the deterministic work. Keeping execution in tested
scripts rather than improvising each step is what makes the system reliable.

**Operating rules:**
1. **Check `tools/` before building anything new.**
2. **When something fails, fix the tool and record what you learned** in the
   relevant workflow — rate limits, timing quirks, API surprises. Check before
   re-running anything that costs credits.
3. **Keep workflows current, but do not create or overwrite them without
   asking.** They are the user's instructions, to be refined rather than
   discarded.

## File Structure

```
.github/workflows/   # GitHub Actions
tools/               # Python, runnable as `python tools/<name>.py` from root
workflows/           # Markdown SOPs
tests/               # pytest, offline
site/                # static dashboard, published to Pages
data/raw/            # immutable daily captures — the whole point of the repo
data/derived/        # regenerable tidy tables
docs/superpowers/    # spec and implementation plan
.tmp/                # disposable intermediates
.env                 # secrets, gitignored
```

Note this project deviates from the generic WAT convention that deliverables go
to cloud services: the deliverable here is a static site published to GitHub
Pages, and `data/` is deliberately committed rather than treated as disposable,
because the git history *is* the drift record.
