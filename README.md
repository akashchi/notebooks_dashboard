# OpenVINO Notebooks — nightly dashboard

A lightweight dashboard of the nightly notebook tests of
[openvinotoolkit/openvino_notebooks](https://github.com/openvinotoolkit/openvino_notebooks)
([`treon_nightly.yml`](https://github.com/openvinotoolkit/openvino_notebooks/actions/workflows/treon_nightly.yml)),
published at **https://akashchi.github.io/notebooks_dashboard/**.

## What it shows

- **Latest nightly:** notebook pass rate, failing notebooks, new failures, fixed notebooks, executed and skipped notebook runs and wall clock, each with the change since the previous night.
- **Trends** over the last 90 nights: notebook pass rate, and two bars per night: executed notebook runs stacked by status (passed, error, failed, timeout, plus not run when a job stopped early) and skipped runs.
- **Needs attention:** every failing notebook × configuration with the night it started failing and the streak length, the last passing night, a diff link between the two commits, a probable cause (notebook changed / OpenVINO changed / packages changed / external or flaky), the error, and links to the job, logs and source.
- **Notebook health:** each notebook's status per configuration, with columns grouped by OS → device → Python and filterable by each of them, and a 30-night history strip; searchable and filterable by status. Expanding a notebook shows per-configuration history and failure details (traceback, failing cell, log tail).
- **Flaky notebooks, timeout risks, duration regressions, recurring error groups** and **infrastructure problems** (jobs that failed before running notebooks).

Metric definitions are in [docs/DATA.md](docs/DATA.md) and in the producer's
[README](https://github.com/openvinotoolkit/openvino_notebooks/blob/latest/.ci/nightly_report/README.md).

## How it works

```
openvino_notebooks: treon_nightly.yml ──► artifact "nightly-dashboard-data" (nightly-report.json, kept ~60-90 days)
                                                    │  pulled daily at 08:00 UTC
this repo: .github/workflows/update.yml ────────────┘
  1. pytest
  2. scripts/ingest.py   download new official nights → data/runs/<date>_<run_id>.json (compact) + data/catalog.json, committed
  3. scripts/build.py    cross-night metrics → _site/data/{meta,trend,latest,history}.json + site/ (static HTML/CSS/JS)
  4. deploy to GitHub Pages
```

- **Official nights only:** scheduled runs (`event: schedule`) of `treon_nightly` on the `latest` branch. They are configured in [dashboard.json](dashboard.json).
- **Test data:** while no official night has been ingested, or when the workflow is started manually with `use_latest_artifact`, the page shows the most recent artifact of any branch or event, behind a yellow "Test data" banner. Test data is never stored in `data/`.
- **History:** kept in `data/` indefinitely, because the upstream artifacts expire. The daily commit also keeps the scheduled workflow active, since GitHub disables schedules in public repositories after 60 days without activity.
- **No dependencies:** the scripts use only the Python standard library (pytest only for tests), and the page is plain HTML, CSS and JavaScript with SVG charts. There is no build step and no CDN.

## Setup

1. **Enable GitHub Pages** with Source = **GitHub Actions**: *Settings → Pages → Build and deployment → Source: GitHub Actions*, or run
   `gh api -X POST repos/<owner>/notebooks_dashboard/pages -f build_type=workflow`.
2. Run **Actions → Update dashboard → Run workflow** once. Later runs are scheduled daily at 08:00 UTC, and pushes to `main` redeploy the site.
3. *(Only if ingestion fails with HTTP 401/403.)* The workflow downloads the artifacts with its own `GITHUB_TOKEN`. If that is rejected, create a fine-grained personal access token with **public repositories (read-only)** access and store it as the repository secret `NIGHTLY_ARTIFACTS_TOKEN`.

## Local usage

```bash
# Requires a GitHub token to download artifacts (anonymous downloads are rejected).
GH_TOKEN=$(gh auth token) python scripts/ingest.py               # add --use_latest_artifact to fetch test data
python scripts/build.py --out _site
python -m http.server 8000 --directory _site                      # open http://localhost:8000
python -m pytest                                                  # tests (pip install pytest)
```

To preview several nights from runs of any branch or event (for example pull request test runs), store them in a
local history under `.cache/` (never committed) and build from it:

```bash
GH_TOKEN=$(gh auth token) python scripts/local_history.py <run_id> <run_id> ...
python scripts/build.py --data_dir .cache/local --cache_dir .cache/local --out _site
```

## Repository layout

| Path               | Content                                                                        |
| ------------------ | ------------------------------------------------------------------------------ |
| `dashboard.json` | Source repository, workflow, artifact, official branch/event, time windows     |
| `scripts/`       | `github_api.py`, `ingest.py`, `compact.py`, `metrics.py`, `build.py`, `local_history.py` |
| `site/`          | The page:`index.html`, `app.js`, `style.css`, `favicon.svg`            |
| `data/`          | Stored history (committed by the workflow)                                     |
| `docs/DATA.md`   | Stored and site data formats, metric definitions                               |
| `tests/`         | pytest suite and fixtures                                                      |
