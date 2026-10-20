"""Conversion of a producer report (nightly-report.json, schema 1.x) into the compact run record stored in data/runs/.

Stdlib only. The compact record keeps what the dashboard needs for the current night and for cross-night metrics,
with results stored as arrays and key-package sets deduplicated (see docs/DATA.md).
"""

import re
from typing import Optional

FORMAT_VERSION = 1

RESULT_FIELDS = ("notebook", "config_id", "status", "reason", "duration_s", "timeout_s", "peak_rss_bytes", "pkgset", "env_id", "failure_id")

STATUS_CODES = {"passed": "P", "failed": "F", "timeout": "T", "error": "E", "skipped": "S", "not_run": "N"}
ABSENT_CODE = "-"

CELL_SOURCE_MAX_LINES = 20
TRACEBACK_MAX_LINES = 30
LOG_TAIL_MAX_LINES = 30
LOG_EXCERPT_MAX_LINES = 60
MESSAGE_MAX_CHARS = 1024

_JOB_FIELDS = ("id", "name", "config_id", "batch", "conclusion", "duration_s", "url", "treon_step_url", "logs_artifact_url", "tests_started")
_FAILURE_FIELDS = ("id", "notebook", "config_id", "job_id", "status", "category", "error_class", "signature", "cell_index", "exception_type", "links")


def schema_major(report: dict) -> Optional[int]:
    match = re.match(r"^(\d+)\.", str(report.get("schema_version") or ""))
    return int(match.group(1)) if match else None


def _head(text: Optional[str], max_lines: int) -> Optional[str]:
    if not text:
        return None
    lines = text.splitlines()
    return "\n".join(lines[:max_lines] + (["…"] if len(lines) > max_lines else []))


def _tail(text: Optional[str], max_lines: int) -> Optional[str]:
    if not text:
        return None
    lines = text.splitlines()
    return "\n".join((["…"] if len(lines) > max_lines else []) + lines[-max_lines:])


def clean_title(title: Optional[str]) -> str:
    """Notebook titles come from markdown headings; drop emphasis and code markers."""
    return re.sub(r"\*\*|__|`", "", title or "").strip()


def compact_failure(failure: dict) -> dict:
    compact = {key: failure.get(key) for key in _FAILURE_FIELDS}
    message = failure.get("exception_message")
    compact["exception_message"] = message[:MESSAGE_MAX_CHARS] if message else None
    compact["cell_source_excerpt"] = _head(failure.get("cell_source_excerpt"), CELL_SOURCE_MAX_LINES)
    compact["traceback_excerpt"] = _tail(failure.get("traceback_excerpt"), TRACEBACK_MAX_LINES)
    compact["log_tail"] = _tail(failure.get("log_tail"), LOG_TAIL_MAX_LINES)
    compact["log_excerpt"] = _tail(failure.get("log_excerpt"), LOG_EXCERPT_MAX_LINES)
    compact["links"] = dict(failure.get("links") or {})
    return compact


def compact_job(job: dict) -> dict:
    compact = {key: job.get(key) for key in _JOB_FIELDS}
    compact["failed_step"] = (job.get("failed_step") or {}).get("name")
    return compact


def _reason(result: dict) -> Optional[str]:
    """Skip or not-run reason; an ignore-list skip names its list file, e.g. "ignore_list: gpu_oom.txt" (schema >= 1.2)."""
    reason = result.get("skip_reason") or result.get("not_run_reason")
    if reason and result.get("skip_reason") and result.get("skip_source"):
        return f"{reason}: {result['skip_source']}"
    return reason


def compact_report(report: dict, artifact: Optional[dict] = None, ingested_at: Optional[str] = None) -> dict:
    """Compact run record of a producer report. `artifact` is {"id", "created_at"} of the downloaded artifact."""
    pkgsets: list[dict] = []
    pkgset_index: dict[tuple, int] = {}
    results = []
    for result in report.get("results") or []:
        packages = result.get("packages") or {}
        key = tuple(sorted(packages.items()))
        if key not in pkgset_index:
            pkgset_index[key] = len(pkgsets)
            pkgsets.append(dict(key))
        results.append(
            [
                result.get("notebook"),
                result.get("config_id"),
                result.get("status"),
                _reason(result),
                result.get("duration_s"),
                result.get("timeout_s"),
                result.get("peak_rss_bytes"),
                pkgset_index[key],
                result.get("env_id"),
                result.get("failure_id"),
            ]
        )
    return {
        "format": FORMAT_VERSION,
        "schema_version": report.get("schema_version"),
        "ingested_at": ingested_at,
        "artifact": dict(artifact) if artifact else None,
        "run": dict(report.get("run") or {}),
        "summary": dict(report.get("summary") or {}),
        "configs": [dict(config) for config in report.get("configs") or []],
        "jobs": [compact_job(job) for job in report.get("jobs") or []],
        "notebooks": {notebook["path"]: notebook.get("dir_tree_sha") for notebook in report.get("notebooks") or [] if notebook.get("path")},
        "pkgsets": pkgsets,
        "result_fields": list(RESULT_FIELDS),
        "results": results,
        "failures": [compact_failure(failure) for failure in report.get("failures") or []],
        "error_groups": [dict(group) for group in report.get("error_groups") or []],
    }


def expand_results(run: dict) -> list[dict]:
    """Result rows of a compact run record as dicts; `packages` is resolved from the pkgset table."""
    fields = run.get("result_fields") or list(RESULT_FIELDS)
    pkgsets = run.get("pkgsets") or []
    rows = []
    for values in run.get("results") or []:
        row = dict(zip(fields, values))
        index = row.pop("pkgset", None)
        row["packages"] = pkgsets[index] if isinstance(index, int) and 0 <= index < len(pkgsets) else {}
        rows.append(row)
    return rows


def catalog_from_report(report: dict) -> dict:
    """Notebook catalog (title, tags, links, last modification) of the tested commit."""
    run = report.get("run") or {}
    notebooks = {}
    for notebook in report.get("notebooks") or []:
        if not notebook.get("path"):
            continue
        notebooks[notebook["path"]] = {
            "title": clean_title(notebook.get("title")) or notebook["path"],
            "dir": notebook.get("dir"),
            "tags": notebook.get("tags") or {},
            "links": notebook.get("links") or {},
            "last_modified": notebook.get("last_modified"),
        }
    return {"run_id": run.get("id"), "nightly_date": run.get("nightly_date"), "notebooks": dict(sorted(notebooks.items()))}
