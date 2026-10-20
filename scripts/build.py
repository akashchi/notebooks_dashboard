"""Builds the dashboard site: copies site/ into the output directory and writes the site data (docs/DATA.md, section 2).

Usage: python scripts/build.py [--config dashboard.json] [--data_dir data] [--cache_dir .cache] [--site_dir site] [--out _site] [--now ISO]

Inputs are the stored history (data/runs/*.json, data/catalog.json) and, when ingest.py cached them, the test data
(.cache/test_run.json + test_run_meta.json, optional test_catalog.json). With test data, the test run is shown after the
official nights. Without any night, all four data files are still written with empty lists and null values
(latest.json has "run": null) so that the page can show that there is no data yet. Stdlib only.
"""

import argparse
import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import metrics
from compact import ABSENT_CODE, FORMAT_VERSION, STATUS_CODES, expand_results

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINDOWS = {"trend": 90, "matrix": 30, "flaky": 14}
CONFIG_FIELDS = ("id", "device", "os", "os_family", "python")
DATA_FILES = ("meta", "trend", "latest", "history")


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Path, document) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def _timestamp(now: Union[None, str, datetime] = None) -> str:
    if now is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(now, datetime):
        moment = now
    else:
        moment = datetime.fromisoformat(str(now).strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(config: Union[dict, str, Path]) -> dict:
    return dict(config) if isinstance(config, dict) else _read_json(Path(config))


def windows_of(config: dict) -> dict:
    windows = dict(DEFAULT_WINDOWS)
    for name, value in (config.get("windows") or {}).items():
        if isinstance(value, int) and not isinstance(value, bool):
            windows[name] = value
    return windows


def load_runs(data_dir: Path, warnings: list) -> list:
    """Compact run records of data/runs/*.json; unreadable files and other formats are skipped with a warning."""
    runs_dir = Path(data_dir) / "runs"
    records = []
    for path in sorted(runs_dir.glob("*.json")) if runs_dir.is_dir() else []:
        try:
            record = _read_json(path)
        except (OSError, ValueError) as exc:
            warnings.append(f"Skipped unreadable run file runs/{path.name}: {exc}")
            continue
        if not isinstance(record, dict) or record.get("format") != FORMAT_VERSION:
            found = record.get("format") if isinstance(record, dict) else type(record).__name__
            warnings.append(f"Skipped run file runs/{path.name}: unsupported format {found!r}")
            continue
        if not isinstance(record.get("run"), dict) or record["run"].get("id") is None:
            warnings.append(f"Skipped run file runs/{path.name}: no run id")
            continue
        records.append(record)
    return records


def load_catalog(path: Path, warnings: list) -> Optional[dict]:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        catalog = _read_json(path)
    except (OSError, ValueError) as exc:
        warnings.append(f"Ignored unreadable catalog {path.name}: {exc}")
        return None
    if not isinstance(catalog, dict) or not isinstance(catalog.get("notebooks"), dict):
        warnings.append(f"Ignored catalog {path.name}: no notebooks")
        return None
    return catalog


def load_test_data(cache_dir: Path, official_ids: set, warnings: list) -> tuple:
    """(test run record, meta.json "test_data") from the ingest cache, or (None, None) when there is no usable test data."""
    cache_dir = Path(cache_dir)
    run_path, meta_path = cache_dir / "test_run.json", cache_dir / "test_run_meta.json"
    if not run_path.is_file() or not meta_path.is_file():
        return None, None
    try:
        record, meta = _read_json(run_path), _read_json(meta_path)
    except (OSError, ValueError) as exc:
        warnings.append(f"Ignored the test data: {exc}")
        return None, None
    if not isinstance(record, dict) or record.get("format") != FORMAT_VERSION or not isinstance(record.get("run"), dict) or record["run"].get("id") is None:
        warnings.append("Ignored the test data: test_run.json is not a compact run record")
        return None, None
    run = record["run"]
    if str(run["id"]) in {str(run_id) for run_id in official_ids}:
        warnings.append(f"Ignored the test data: run {run['id']} is already an official night")
        return None, None
    meta = meta if isinstance(meta, dict) else {}
    test_data = {}
    for key, field in (("run_id", "id"), ("url", "url"), ("event", "event"), ("branch", "branch"), ("nightly_date", "nightly_date")):
        test_data[key] = meta.get(key) if meta.get(key) is not None else run.get(field)
    test_data["reason"] = meta.get("reason") or ("requested" if official_ids else "no_official_data")
    return record, test_data


def make_meta(config: dict, history: metrics.History, test_data, warnings: list, generated_at: str, dashboard_repository) -> dict:
    source = dict(config.get("source") or {})
    repository, workflow = source.get("repository"), source.get("workflow")
    workflow_url = f"https://github.com/{repository}/actions/workflows/{workflow}" if repository and workflow else None
    ordered = {"repository": repository, "workflow": workflow, "workflow_url": workflow_url}
    ordered.update({key: value for key, value in source.items() if key not in ordered})
    official = history.official
    return {
        "generated_at": generated_at,
        "source": ordered,
        "windows": windows_of(config),
        "official_nights": len(official),
        "first_night": history.refs[0]["date"] if official else None,
        "last_night": history.refs[len(official) - 1]["date"] if official else None,
        "test_data": test_data,
        "producer_readme": config.get("producer_readme"),
        "dashboard_repository": dashboard_repository,
        "warnings": list(warnings),
    }


def _last(items: list, count: int) -> list:
    return items[len(items) - count :] if count > 0 else []


def make_trend(history: metrics.History, windows: dict) -> dict:
    nights = [metrics.night_entry(record) for record in _last(history.official, windows["trend"])]
    if history.test_run is not None:
        nights.append(metrics.night_entry(history.test_run, test=True))
    return {"nights": nights}


def make_history(history: metrics.History, windows: dict) -> dict:
    count = min(len(history.runs), max(windows["matrix"], 0))
    first = len(history.runs) - count
    nights = [
        {"date": ref["date"], "run_id": ref["run_id"], "url": ref["url"], "test": history.is_test(night)}
        for night, ref in enumerate(history.refs)
        if night >= first
    ]
    return {"nights": nights, "notebooks": metrics.status_matrix(history, first) if count else {}}


def notebook_rows(record: Optional[dict], catalog: Optional[dict], flaky: list, changes: dict) -> list:
    """The catalog (plus notebooks of the night missing from it) sorted by path, with the night's statuses."""
    catalog_notebooks = (catalog or {}).get("notebooks") or {}
    statuses: dict = {}
    for row in expand_results(record) if record else []:
        notebook, config_id = row.get("notebook"), row.get("config_id")
        if not notebook or not config_id:
            continue
        statuses.setdefault(notebook, {})[config_id] = {
            "s": STATUS_CODES.get(row.get("status"), ABSENT_CODE),
            "d": row.get("duration_s"),
            "f": row.get("failure_id"),
            "r": row.get("reason"),
        }
    paths = set(catalog_notebooks) | set((record or {}).get("notebooks") or {}) | set(statuses)
    flaky_notebooks = {item["notebook"] for item in flaky}
    new_failure_notebooks = {item["notebook"] for item in changes["new_failures"]}
    rows = []
    for path in sorted(paths):
        info = catalog_notebooks.get(path) or {}
        status = statuses.get(path, {})
        codes = [value["s"] for value in status.values()]
        rows.append(
            {
                "path": path,
                "title": info.get("title") or path,
                "dir": info.get("dir") or (path.rsplit("/", 1)[0] if "/" in path else None),
                "tags": info.get("tags") or {},
                "links": info.get("links") or {},
                "last_modified": info.get("last_modified"),
                "status": status,
                "worst": metrics.worst_code(codes),
                "failing": any(code in metrics.FAILING_CODES for code in codes),
                "flaky": path in flaky_notebooks,
                "new_failure": path in new_failure_notebooks,
            }
        )
    return rows


def make_latest(history: metrics.History, catalog: Optional[dict], repository: Optional[str], windows: dict) -> dict:
    night = history.displayed
    record = history.runs[night] if night is not None else None
    previous = history.runs[night - 1] if night else None
    entry = metrics.night_entry(record) if record else {}
    previous_entry = metrics.night_entry(previous) if previous else {}
    has_previous = previous is not None
    if record:
        changes = metrics.changes(history, night)
        new_keys = {(item["notebook"], item["config_id"]) for item in changes["new_failures"]}
        flaky = metrics.flaky(history, night, windows["flaky"])
        attention = metrics.attention(history, night, repository, new_keys)
        regressions = metrics.duration_regressions(history, night)
        risk = metrics.timeout_risk(history, night)
        groups = metrics.error_groups(history, night)
        failures = dict(history.failures[night])
    else:
        changes, flaky, attention, regressions, risk, groups, failures = {"new_failures": [], "fixed": []}, [], [], [], [], [], {}

    def kpi(name: str) -> dict:
        return metrics.kpi(entry.get(name), previous_entry.get(name), has_previous)

    kpis = {name: kpi(name) for name in ("pass_rate", "notebook_pass_rate", "coverage", "failing_notebooks")}
    kpis["new_failures"] = {"value": len(changes["new_failures"]) if record else None, "delta": None}
    kpis["fixed"] = {"value": len(changes["fixed"]) if record else None, "delta": None}
    kpis["not_run"] = kpi("not_run")
    kpis["wall_clock_s"] = kpi("wall_clock_s")
    previous_ref = None
    if previous:
        ref = history.refs[night - 1]
        previous_ref = {"date": ref["date"], "run_id": ref["run_id"], "url": ref["url"]}
    return {
        "test": history.test_run is not None,
        "run": dict(record.get("run") or {}) if record else None,
        "summary": record.get("summary") if record else None,
        "previous": previous_ref,
        "kpis": kpis,
        "configs": [{field: config.get(field) for field in CONFIG_FIELDS} for config in (record or {}).get("configs") or []],
        "notebooks": notebook_rows(record, catalog, flaky, changes),
        "failures": failures,
        "attention": attention,
        "changes": changes,
        "flaky": flaky,
        "duration_regressions": regressions,
        "timeout_risk": risk,
        "error_groups": groups,
        "mttr_nights": metrics.mttr_nights(history, windows["trend"]),
        "infra": {"jobs_failed_before_tests": metrics.jobs_failed_before_tests(record) if record else [], "not_run": entry.get("not_run")},
    }


def compute(
    config: dict,
    records: list,
    catalog: Optional[dict] = None,
    test_run: Optional[dict] = None,
    test_data: Optional[dict] = None,
    warnings: Optional[list] = None,
    now: Union[None, str, datetime] = None,
    dashboard_repository: Optional[str] = None,
) -> dict:
    """The four site data documents {"meta", "trend", "latest", "history"} (pure: no file access)."""
    windows = windows_of(config)
    repository = (config.get("source") or {}).get("repository")
    history = metrics.History(records, test_run)
    return {
        "meta": make_meta(config, history, test_data if test_run is not None else None, warnings or [], _timestamp(now), dashboard_repository),
        "trend": make_trend(history, windows),
        "latest": make_latest(history, catalog, repository, windows),
        "history": make_history(history, windows),
    }


def prepare_out(out_dir: Union[str, Path]) -> Path:
    """Empties the output directory; refuses directories that don't look like a previous build."""
    out = Path(out_dir).resolve()
    cwd = Path.cwd().resolve()
    if out in {ROOT, cwd, Path.home().resolve(), Path(out.anchor)} or out in ROOT.parents or out in cwd.parents:
        raise ValueError(f"refusing to use {out} as the output directory")
    if out.exists():
        if not out.is_dir():
            raise ValueError(f"output path {out} is not a directory")
        if any(out.iterdir()) and not ((out / "index.html").exists() or (out / "data").is_dir()):
            raise ValueError(f"refusing to clean {out}: it is not empty and doesn't look like a site build")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    return out


def write_site(documents: dict, site_dir: Optional[Union[str, Path]], out_dir: Union[str, Path]) -> Path:
    site = Path(site_dir).resolve() if site_dir else None
    resolved_out = Path(out_dir).resolve()
    if site is not None and (site == resolved_out or resolved_out in site.parents):
        raise ValueError(f"the output directory {resolved_out} must not contain the site sources {site}")
    out = prepare_out(out_dir)
    if site is not None and site.is_dir():
        shutil.copytree(site, out, dirs_exist_ok=True)
    data_dir = out / "data"
    data_dir.mkdir(exist_ok=True)
    for name in DATA_FILES:
        _write_json(data_dir / f"{name}.json", documents[name])
    return out


def build(config, data_dir, cache_dir, site_dir, out_dir, now=None) -> dict:
    """Reads the history and test data, writes the site into `out_dir` and returns the four data documents."""
    config = load_config(config)
    warnings: list = []
    records = load_runs(Path(data_dir), warnings)
    official_ids = {record["run"]["id"] for record in records}
    test_run, test_data = load_test_data(Path(cache_dir), official_ids, warnings)
    catalog = load_catalog(Path(cache_dir) / "test_catalog.json", warnings) if test_run is not None else None
    if catalog is None:
        catalog = load_catalog(Path(data_dir) / "catalog.json", warnings)
    dashboard_repository = os.environ.get("GITHUB_REPOSITORY") or config.get("dashboard_repository")
    documents = compute(config, records, catalog, test_run, test_data, warnings, now, dashboard_repository)
    write_site(documents, site_dir, out_dir)
    return documents


def summary_lines(documents: dict, out_dir) -> list:
    meta, latest = documents["meta"], documents["latest"]
    lines = [f"Site written to {out_dir}: {meta['official_nights']} official night(s) ({meta['first_night'] or '-'} .. {meta['last_night'] or '-'})."]
    run = latest["run"]
    if run is None:
        lines.append("No data to show yet.")
    else:
        kind = "test run" if latest["test"] else "night"
        lines.append(
            f"Shown {kind}: {run.get('nightly_date')} run {run.get('id')} - {latest['kpis']['failing_notebooks']['value']} failing notebook(s), "
            f"{len(latest['changes']['new_failures'])} new failure(s), {len(latest['changes']['fixed'])} fixed, {len(latest['flaky'])} flaky."
        )
    if meta["test_data"]:
        lines.append(f"Test data: run {meta['test_data']['run_id']} ({meta['test_data']['reason']}).")
    lines.extend(f"Warning: {warning}" for warning in meta["warnings"])
    return lines


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the notebooks dashboard site.")
    parser.add_argument("--config", default=str(ROOT / "dashboard.json"), help="dashboard configuration (default: dashboard.json)")
    parser.add_argument("--data_dir", default=str(ROOT / "data"), help="stored history (default: data)")
    parser.add_argument("--cache_dir", default=str(ROOT / ".cache"), help="ingest cache with optional test data (default: .cache)")
    parser.add_argument("--site_dir", default=str(ROOT / "site"), help="static site files (default: site)")
    parser.add_argument("--out", default=str(ROOT / "_site"), help="output directory, emptied first (default: _site)")
    parser.add_argument("--now", default=None, help="generation time, ISO 8601 (default: current time)")
    args = parser.parse_args(argv)
    try:
        documents = build(args.config, args.data_dir, args.cache_dir, args.site_dir, args.out, now=args.now)
    except Exception as exc:  # noqa: BLE001 - report any failure as a non-zero exit code
        traceback.print_exc()
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print("\n".join(summary_lines(documents, args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
