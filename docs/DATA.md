# Data formats

Two formats are produced by this repository:

1. **Stored history** (`data/`, committed): written by `scripts/ingest.py`.
2. **Site data** (`_site/data/`, generated at deploy time): written by `scripts/build.py` and read by `site/app.js`.

The input is the producer's `nightly-report.json` (schema 1.x), described in
[openvino_notebooks/.ci/nightly_report](https://github.com/openvinotoolkit/openvino_notebooks/blob/latest/.ci/nightly_report/README.md).

Conventions: timestamps are ISO 8601 UTC, durations are seconds, and notebook paths are relative to `notebooks/`, e.g. `async-api/async-api.ipynb`.
A **night** is one official run, identified by `run.id`. When a run is re-attempted, only its latest attempt is kept.

Status codes used in strings:

| Code | Status | Code | Status |
|---|---|---|---|
| `P` | passed | `S` | skipped |
| `F` | failed | `N` | not_run |
| `T` | timeout | `-` | absent (notebook not in that night) |
| `E` | error | | |

The worst status of a notebook in a night (across configurations) uses this order, worst first:
`E` > `T` > `F` > `N` > `P` > `S` > `-`.

## 1. Stored history (`data/`)

### `data/runs/<nightly_date>_<run_id>.json`

One compact run record per official night, produced by `compact.compact_report()`:

| Key | Content |
|---|---|
| `format` | `1` (version of this compact format) |
| `schema_version` | producer schema version, e.g. `"1.0.0"` |
| `ingested_at` | when the record was written |
| `artifact` | `{"id", "created_at"}` of the source artifact |
| `run`, `summary`, `configs`, `error_groups` | as in the producer report |
| `jobs` | slim jobs: `id, name, config_id, batch, conclusion, duration_s, url, treon_step_url, logs_artifact_url, tests_started, failed_step` (`failed_step` is the step name or null) |
| `notebooks` | `{path: dir_tree_sha}` of the tested commit |
| `pkgsets` | list of distinct key-package sets `{package: version}` |
| `result_fields` | `["notebook","config_id","status","reason","duration_s","timeout_s","peak_rss_bytes","pkgset","env_id","failure_id"]` |
| `results` | list of arrays in `result_fields` order; `reason` = skip_reason or not_run_reason, with the ignore list file appended for ignore-list skips (`ignore_list: gpu_oom.txt`, producer schema >= 1.2); `pkgset` indexes `pkgsets` |
| `failures` | producer failures with excerpts capped (cell source 20 lines, traceback 30, log tail 30, message 1024 chars) |

Use `compact.expand_results(run)` to get result dicts with a resolved `packages` dict.

### `data/catalog.json`

Notebook catalog of the newest official night, rewritten on each ingestion. Built by `compact.catalog_from_report()`:

```json
{"run_id": 1, "nightly_date": "2026-10-12", "notebooks": {"a/a.ipynb": {"title": "...", "dir": "a", "tags": {"categories": []}, "links": {"source": "...", "latest": "...", "history": "..."}, "last_modified": {"sha": "...", "date": "...", "url": "..."}}}}
```

### `data/ingested.json`

Ingestion index: `{"runs": {"<run_id>": {"attempt": 1, "artifact_id": 1, "artifact_created_at": "...", "file": "runs/2026-10-12_123.json", "nightly_date": "2026-10-12"}}}`.
An artifact is downloaded again only when its id differs from the stored one. The record with the higher `run.attempt` wins, or the newer artifact when the attempts are equal.

## 2. Site data (`_site/data/`)

Every file is JSON. All lists are ordered as described. Nights are ordered oldest → newest.

### `meta.json`

```json
{
  "generated_at": "2026-10-12T08:05:00Z",
  "source": {"repository": "openvinotoolkit/openvino_notebooks", "workflow": "treon_nightly.yml", "workflow_url": "https://github.com/openvinotoolkit/openvino_notebooks/actions/workflows/treon_nightly.yml", "artifact": "nightly-dashboard-data", "branch": "latest", "event": "schedule"},
  "windows": {"trend": 90, "matrix": 30, "flaky": 14},
  "official_nights": 42,
  "first_night": "2026-09-01", "last_night": "2026-10-12",
  "test_data": null,
  "producer_readme": "https://github.com/.../README.md",
  "dashboard_repository": "akashchi/notebooks_dashboard",
  "warnings": []
}
```

`test_data` is `null`, or describes the non-official run shown instead of (or after) the official data:
`{"run_id", "url", "event", "branch", "nightly_date", "reason": "no_official_data" | "requested"}`.

### `trend.json`

```json
{"nights": [{"date": "2026-10-12", "run_id": 1, "attempt": 1, "url": "...", "sha": "...", "short_sha": "abc1234", "conclusion": "failure", "test": false,
             "pass_rate": 0.98, "notebook_pass_rate": 0.98, "coverage": 1.0,
             "executed": 589, "passed": 577, "failed": 2, "timeout": 10, "error": 0, "not_run": 0, "skipped": 413,
             "untested": 59, "failing_notebooks": 4, "jobs_failed_before_tests": 0, "wall_clock_s": 12634, "compute_s": 160000}]}
```

This holds the last `windows.trend` official nights. In test-data mode the test run is appended with `"test": true`.
Rates are numbers in [0, 1] or `null`.

### `latest.json`

The night shown on the page: the newest official night, or the test run in test-data mode.

```json
{
  "test": false,
  "run": {"id": 1, "attempt": 1, "number": 88, "url": "...", "event": "schedule", "branch": "latest", "nightly_date": "2026-10-12",
          "started_at": "...", "finished_at": "...", "wall_clock_s": 12634, "conclusion": "failure",
          "commit": {"sha": "...", "short_sha": "...", "message": "...", "url": "...", "date": "...", "author": "..."},
          "inputs": {"ov_branch": "master", "docker_tag": "...", "timeout_s": 1200}},
  "summary": { "...": "producer summary block, unchanged" },
  "previous": null,
  "kpis": {
    "notebook_pass_rate": {"value": 0.98, "delta": 0.0},
    "failing_notebooks": {"value": 4, "delta": 1},
    "new_failures": {"value": 1, "delta": null},
    "fixed": {"value": 0, "delta": null},
    "executed": {"value": 589, "delta": 0},
    "skipped": {"value": 413, "delta": 0},
    "wall_clock_s": {"value": 12634, "delta": -300}
  },
  "configs": [{"id": "cpu-ubuntu-22.04-py3.11", "device": "cpu", "os": "ubuntu-22.04", "os_family": "linux", "python": "3.11"}],
  "notebooks": [
    {"path": "a/a.ipynb", "title": "Title", "dir": "a", "tags": {"categories": ["..."]},
     "links": {"source": "...", "latest": "...", "history": "..."}, "last_modified": {"sha": "...", "date": "...", "url": "..."},
     "status": {"cpu-ubuntu-22.04-py3.11": {"s": "P", "d": 65.9, "f": null, "r": null}},
     "worst": "P", "failing": false, "flaky": false, "new_failure": false}
  ],
  "failures": {"cpu-ubuntu-22.04-py3.11/a/a.ipynb": {"...": "compact failure (see data/runs)"}},
  "attention": [
    {"notebook": "a/a.ipynb", "config_id": "cpu-ubuntu-22.04-py3.11", "status": "failed", "failure_id": "...", "new": true,
     "since": {"date": "...", "run_id": 1, "url": "...", "sha": "..."}, "streak_nights": 1,
     "last_pass": {"date": "...", "run_id": 1, "url": "...", "sha": "..."}, "compare_url": "https://github.com/.../compare/<last_pass_sha>...<since_sha>",
     "cause": "notebook_changed", "cause_detail": "openvino 2026.4.1 → 2026.5.0.dev20261007",
     "category": "cell_error", "error_class": "dependency", "error": "ModuleNotFoundError: No module named 'x'"}
  ],
  "changes": {"new_failures": [{"notebook": "...", "config_id": "...", "status": "failed", "failure_id": "...", "previous_status": "passed"}],
              "fixed": [{"notebook": "...", "config_id": "..."}]},
  "flaky": [{"notebook": "...", "config_id": "...", "score": 0.5, "transitions": 3, "executed": 7, "history": "PFPPFPF"}],
  "duration_regressions": [{"notebook": "...", "config_id": "...", "duration_s": 300.0, "baseline_s": 150.0, "ratio": 2.0}],
  "timeout_risk": [{"notebook": "...", "config_id": "...", "median_s": 1000.0, "timeout_s": 1200, "ratio": 0.83}],
  "error_groups": [{"signature": "...", "category": "...", "error_class": "...", "exception_type": "...", "exception_message": "...",
                    "occurrences": 3, "notebooks": ["..."], "configs": ["..."], "nights_seen": 5, "first_seen": "2026-10-01", "last_seen": "2026-10-12"}],
  "mttr_nights": 2.5,
  "infra": {"jobs_failed_before_tests": [{"name": "...", "url": "...", "config_id": "...", "batch": "batch_0", "failed_step": "Install python dependencies"}],
            "not_run": 0}
}
```

Details:

- **`notebooks`**: the full catalog sorted by path, including notebooks without results in this night (their `status` is `{}` and `worst` is `-`). `failing` means at least one F, T or E result. `r` is the skip or not-run reason (see `results` above). Config `device` is `cpu`, `igpu` or `dgpu` (the page labels CPU configs without a device suffix and GPU ones with "iGPU" or "dGPU").
- **`kpis`**: `delta` is value minus the previous official night's value, or `null` when there is no previous night or the value is missing. `new_failures` and `fixed` count `changes`. `notebook_pass_rate` is computed by the dashboard as passed ÷ executed notebook × configuration runs (the producer's per-notebook value is not used).
- **`attention`**: every failing (notebook, config) of the night, sorted by `new` first, then by `streak_nights` descending, then notebook and config.
  - `since` is the first night of the current failing streak.
  - `last_pass` is the last night with `passed` before it (null if none in history).
  - `cause` is one of `notebook_changed`, `openvino_changed`, `packages_changed`, `external_or_flaky`, `unknown` (no previous pass).
- **`changes`**: compares each (notebook, config) with its previous executed status in an earlier official night.
  - `new_failures` are failing now and passed at the previous executed status (`previous_status: "passed"`), or have no earlier executed status at all, e.g. a newly added notebook (`previous_status: null`; not reported on the first night of the history).
  - `fixed` passed now and failed at the previous executed status.
- **`flaky`**: series with ≥ 0.3 transitions per executed night over the last `windows.flaky` executed nights (at least 4 executed), whose `dir_tree_sha` didn't change in that window. Sorted by score descending.
- **`duration_regressions`**: the night's passed duration is > 1.5 × the median of the previous 7 passed durations and more than 60 s above it.
- **`timeout_risk`**: the median of the last 7 executed durations (including this night) is ≥ 0.8 × `timeout_s`.
- **`error_groups`**: the night's groups, enriched with `nights_seen`, `first_seen` and `last_seen` (the signature's occurrences across the official history).
- **`mttr_nights`**: mean length in nights of failing streaks that ended (fixed) within the trend window; null if none.

### `history.json`

```json
{"nights": [{"date": "2026-10-11", "run_id": 1, "url": "...", "test": false}],
 "notebooks": {"a/a.ipynb": {"all": "PPF-", "cfg": {"cpu-ubuntu-22.04-py3.11": "PPF-"}}}}
```

This covers the last `windows.matrix` nights, including the test run in test-data mode. Character `i` of each string is the status in `nights[i]`.
`all` is the worst status across configurations. `cfg` has one entry per configuration seen in the window.
