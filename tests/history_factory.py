"""Synthetic nights for the tests and the sample site.

- `make_report()` / `make_run()` / `runs_from_codes()` build minimal producer reports from scratch (metrics unit tests).
- `fixture_night()` derives a night from the real producer report in tests/fixtures/nightly-report.json;
  `sample_reports()` chains such nights into a realistic official history (build tests, sample site data).

Reports are producer-like (schema 1.x); `compact()` turns them into the records stored in data/runs/.
"""

import copy
import functools
import hashlib
import json
import random
from datetime import date as Date
from datetime import timedelta
from pathlib import Path
from typing import Optional

from compact import compact_report

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPOSITORY = "openvinotoolkit/openvino_notebooks"
NB = "a/a.ipynb"
CFG = "cpu-ubuntu-22.04-py3.11"
KEY = (NB, CFG)
PACKAGES = {"openvino": "2026.4.1", "numpy": "2.4.6"}
CODE_STATUS = {"P": "passed", "F": "failed", "T": "timeout", "E": "error", "S": "skipped", "N": "not_run"}
EXECUTED = frozenset({"passed", "failed", "timeout", "error"})
FAILING = frozenset({"failed", "timeout", "error"})

FIXTURE_RUN_ID = 38222478164
FIXTURE_SHA = "0a92f128a6a28ee4c5dbaf853ef6b1e2b64719a1"
L11, L12, L13 = "cpu-ubuntu-22.04-py3.11", "cpu-ubuntu-22.04-py3.12", "cpu-ubuntu-22.04-py3.13"
W11, W12, W13 = "cpu-windows-2022-py3.11", "cpu-windows-2022-py3.12", "cpu-windows-2022-py3.13"
LINUX = (L11, L12, L13)
WINDOWS = (W11, W12, W13)
ASYNC = "async-api/async-api.ipynb"
YOLO = "yolov26-optimization/yolov26-obb.ipynb"
HELLO_WORLD = "hello-world/hello-world.ipynb"
HELLO_DETECTION = "hello-detection/hello-detection.ipynb"
HELLO_SEGMENTATION = "hello-segmentation/hello-segmentation.ipynb"
TOKENIZERS = "openvino-tokenizers/openvino-tokenizers.ipynb"
BACKGROUND = "vision-background-removal/vision-background-removal.ipynb"
SD3 = "stable-diffusion-v3-torch-fx/stable-diffusion-v3-torch-fx.ipynb"


def sha_for(value) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()


def run_url(run_id) -> str:
    return f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"


def day(start: str, offset: int) -> str:
    return (Date.fromisoformat(start) + timedelta(days=offset)).isoformat()


def _rate(part: int, total: int) -> Optional[float]:
    return round(part / total, 4) if total else None


def _lookup(mapping: Optional[dict], key: tuple):
    """Value for (notebook, config_id), else for the notebook (all its configurations)."""
    mapping = mapping or {}
    return mapping[key] if key in mapping else mapping.get(key[0])


def config_entry(config_id: str) -> dict:
    parts = config_id.split("-")
    python = parts[-1][2:] if parts[-1].startswith("py") else None
    os_name = "-".join(parts[1:-1] if python else parts[1:])
    family = "windows" if os_name.startswith("windows") else "macos" if os_name.startswith("macos") else "linux"
    return {
        "id": config_id,
        "device": parts[0],
        "os": os_name,
        "os_family": family,
        "python": python,
        "runner_label": "runner",
        "container_image": None,
        "job_ids": [],
    }


def job_entry(run_id: int, config_id: str, batch: str = "batch_0", index: int = 0, failed_step: Optional[str] = None) -> dict:
    job_id = run_id * 100 + index
    url = f"{run_url(run_id)}/job/{job_id}"
    return {
        "id": job_id,
        "name": f"build_treon ({config_id}, {batch}) / build_treon",
        "config_id": config_id,
        "batch": batch,
        "conclusion": "failure" if failed_step else "success",
        "duration_s": 600.0,
        "url": url,
        "treon_step_url": f"{url}#step:21:1",
        "logs_artifact_url": None,
        "failed_step": {"number": 5, "name": failed_step, "url": f"{url}#step:5:1"} if failed_step else None,
        "tests_started": not failed_step,
    }


def notebook_entry(path: str, sha: str, dir_tree_sha: Optional[str]) -> dict:
    directory = path.split("/")[0]
    return {
        "path": path,
        "dir": directory,
        "title": f"**{directory.replace('-', ' ').title()}**",
        "tags": {"categories": ["Model Demos"], "libraries": [], "other": [], "tasks": []},
        "dir_tree_sha": dir_tree_sha,
        "last_modified": {
            "sha": sha,
            "short_sha": sha[:7],
            "message": "Update",
            "author": "Bot",
            "date": "2026-09-01T00:00:00Z",
            "url": f"https://github.com/{REPOSITORY}/commit/{sha}",
        },
        "links": {
            "source": f"https://github.com/{REPOSITORY}/blob/{sha}/notebooks/{path}",
            "latest": f"https://github.com/{REPOSITORY}/blob/latest/notebooks/{path}",
            "history": f"https://github.com/{REPOSITORY}/commits/{sha}/notebooks/{directory}",
        },
    }


def result(notebook: str = NB, config_id: str = CFG, status: str = "passed", **fields) -> dict:
    """A producer result; `failure={...}` sets details (exception_type, exception_message, category, ...) of its failure."""
    entry = {
        "notebook": notebook,
        "config_id": config_id,
        "status": status,
        "skip_reason": "skip_config" if status == "skipped" else None,
        "not_run_reason": "job_failed" if status == "not_run" else None,
        "duration_s": 100.0 if status in EXECUTED else None,
        "timeout_s": 1200,
        "peak_rss_bytes": None,
        "packages": dict(PACKAGES) if status in EXECUTED else {},
        "env_id": "env-1" if status in EXECUTED else None,
        "failure_id": f"{config_id}/{notebook}" if status in FAILING else None,
    }
    entry.update(fields)
    return entry


def make_failure(entry: dict, run_id: int, details: Optional[dict] = None) -> dict:
    timeout = entry["status"] == "timeout"
    failure = {
        "id": entry["failure_id"],
        "notebook": entry["notebook"],
        "config_id": entry["config_id"],
        "job_id": None,
        "status": entry["status"],
        "category": "timeout" if timeout else "cell_error",
        "error_class": "timeout" if timeout else "runtime",
        "signature": None,
        "cell_index": 3,
        "cell_source_excerpt": "model = core.compile_model(model_path)",
        "exception_type": None if timeout else "RuntimeError",
        "exception_message": f"Timeout reached ({entry.get('timeout_s')}s), process killed" if timeout else "boom",
        "traceback_excerpt": None,
        "log_tail": None,
        "log_excerpt": None,
        "links": {"job": f"{run_url(run_id)}/job/1"},
    }
    failure.update(details or {})
    if not failure["signature"]:
        failure["signature"] = sha_for(f"{failure['category']}|{failure['exception_type']}|{failure['exception_message']}")[:12]
    return failure


def group_failures(failures: list) -> list:
    groups: dict = {}
    for failure in failures:
        group = groups.setdefault(
            failure["signature"],
            {
                "signature": failure["signature"],
                "category": failure.get("category"),
                "error_class": failure.get("error_class"),
                "exception_type": failure.get("exception_type"),
                "exception_message": failure.get("exception_message"),
                "occurrences": 0,
                "notebooks": [],
                "configs": [],
            },
        )
        group["occurrences"] += 1
        if failure["notebook"] not in group["notebooks"]:
            group["notebooks"].append(failure["notebook"])
        if failure["config_id"] not in group["configs"]:
            group["configs"].append(failure["config_id"])
    for group in groups.values():
        group["notebooks"].sort()
        group["configs"].sort()
    return sorted(groups.values(), key=lambda group: (-group["occurrences"], group["signature"]))


def summarize(results: list, notebook_paths: list) -> dict:
    counts = {status: 0 for status in CODE_STATUS.values()}
    by_notebook: dict = {}
    for entry in results:
        counts[entry["status"]] += 1
        by_notebook.setdefault(entry["notebook"], set()).add(entry["status"])
    executed = sum(counts[status] for status in EXECUTED)
    tested = sorted(notebook for notebook, statuses in by_notebook.items() if statuses & EXECUTED)
    failing = sorted(notebook for notebook, statuses in by_notebook.items() if statuses & FAILING)
    untested = sorted(set(notebook_paths) - set(tested))
    durations = [entry["duration_s"] for entry in results if entry.get("duration_s")]
    return {
        "notebooks": {
            "total": len(notebook_paths),
            "tested": len(tested),
            "untested": len(untested),
            "untested_list": untested,
            "passing_everywhere": len(tested) - len(failing),
            "failing_somewhere": len(failing),
            "failing_everywhere": sum(1 for notebook in failing if "passed" not in by_notebook[notebook]),
            "failing_list": failing,
        },
        "results": {
            "planned": len(results),
            "executed": executed,
            **counts,
            "pass_rate": _rate(counts["passed"], executed),
            "test_s": round(sum(durations), 1),
            "compute_s": round(sum(durations) * 1.5, 1),
        },
        "pass_rate": _rate(counts["passed"], executed),
        "notebook_pass_rate": _rate(len(tested) - len(failing), len(tested)),
        "coverage": _rate(len(results) - counts["not_run"], len(results)),
    }


def make_report(
    run_id: int,
    date: str,
    results: list,
    *,
    attempt: int = 1,
    sha: Optional[str] = None,
    dir_shas: Optional[dict] = None,
    jobs: Optional[list] = None,
    event: str = "schedule",
    branch: str = "latest",
    wall_clock_s: float = 3600.0,
) -> dict:
    """A minimal producer report. `dir_shas` = {notebook: dir_tree_sha}; notebooks default to a constant sha."""
    results = [dict(entry) for entry in results]
    failures = []
    for entry in results:
        details = entry.pop("failure", None)
        if entry["status"] in FAILING and entry.get("failure_id"):
            failures.append(make_failure(entry, run_id, details))
    dir_shas = dir_shas or {}
    paths = sorted({entry["notebook"] for entry in results} | set(dir_shas))
    sha = sha or sha_for(f"commit-{run_id}")
    configs = sorted({entry["config_id"] for entry in results})
    return {
        "schema_version": "1.0.0",
        "generated_at": f"{date}T05:00:00Z",
        "run": {
            "id": run_id,
            "attempt": attempt,
            "number": run_id % 1000,
            "workflow": "treon_nightly",
            "repository": REPOSITORY,
            "event": event,
            "branch": branch,
            "nightly_date": date,
            "created_at": f"{date}T00:40:00Z",
            "started_at": f"{date}T00:40:00Z",
            "finished_at": f"{date}T04:00:00Z",
            "wall_clock_s": wall_clock_s,
            "conclusion": "failure" if failures else "success",
            "url": run_url(run_id),
            "commit": {
                "sha": sha,
                "short_sha": sha[:7],
                "message": f"Commit of {date}",
                "author": "Bot",
                "date": f"{date}T00:00:00Z",
                "url": f"https://github.com/{REPOSITORY}/commit/{sha}",
            },
            "inputs": {"ov_branch": "master", "docker_tag": "latest", "timeout_s": 1200},
        },
        "configs": [config_entry(config_id) for config_id in configs],
        "jobs": jobs if jobs is not None else [job_entry(run_id, config_id, index=index) for index, config_id in enumerate(configs)],
        "notebooks": [notebook_entry(path, sha, dir_shas.get(path, sha_for(f"dir-{path}"))) for path in paths],
        "results": results,
        "failures": failures,
        "error_groups": group_failures(failures),
        "summary": summarize(results, paths),
    }


def compact(report: dict) -> dict:
    run = report["run"]
    return compact_report(report, artifact={"id": run["id"] + 1, "created_at": run.get("finished_at")}, ingested_at=run.get("finished_at"))


def make_run(run_id: int, date: str, results: list, **kwargs) -> dict:
    return compact(make_report(run_id, date, results, **kwargs))


def runs_from_codes(
    series: dict,
    *,
    start: str = "2026-10-01",
    first_id: int = 1000,
    durations: Optional[dict] = None,
    packages: Optional[dict] = None,
    env_ids: Optional[dict] = None,
    dir_shas: Optional[dict] = None,
    failures: Optional[dict] = None,
    shas: Optional[list] = None,
    timeout_s: int = 1200,
) -> list:
    """Compact runs, one per night, from status-code strings.

    `series` maps (notebook, config_id) to codes like "PPF-S" (one character per night, "-" = absent).
    durations/packages/env_ids map (notebook, config_id) to per-night lists; dir_shas maps notebook to a per-night list;
    failures maps (notebook, config_id) to failure details used on every failing night; shas lists commit shas per night.
    """
    nights = max(len(codes) for codes in series.values())
    runs = []
    for night in range(nights):
        results = []
        for key, codes in series.items():
            code = codes[night] if night < len(codes) else "-"
            if code == "-":
                continue
            fields = {"timeout_s": timeout_s}
            for name, per_key in (("duration_s", durations), ("packages", packages), ("env_id", env_ids)):
                if per_key and key in per_key and per_key[key][night] is not None:
                    fields[name] = per_key[key][night]
            if failures and key in failures and CODE_STATUS[code] in FAILING:
                fields["failure"] = dict(failures[key])
            results.append(result(key[0], key[1], CODE_STATUS[code], **fields))
        night_dirs = {notebook: values[night] for notebook, values in (dir_shas or {}).items() if values[night] is not None}
        run_id = first_id + night
        runs.append(make_run(run_id, day(start, night), results, dir_shas=night_dirs, sha=shas[night] if shas else None))
    return runs


@functools.lru_cache(maxsize=1)
def _fixture_text() -> str:
    return (FIXTURES_DIR / "nightly-report.json").read_text(encoding="utf-8")


def fixture_report(run_id: Optional[int] = None, sha: Optional[str] = None) -> dict:
    """A fresh copy of the real producer report; run id and commit sha are replaced everywhere (URLs included)."""
    text = _fixture_text()
    if run_id is not None:
        text = text.replace(str(FIXTURE_RUN_ID), str(run_id))
    if sha is not None:
        text = text.replace(FIXTURE_SHA, sha)
    return json.loads(text)


def fixture_night(
    run_id: int,
    date: str,
    *,
    statuses: Optional[dict] = None,
    durations: Optional[dict] = None,
    packages: Optional[dict] = None,
    set_packages: Optional[dict] = None,
    dir_shas: Optional[dict] = None,
    drop: tuple = (),
    failed_jobs: tuple = (),
    errors: Optional[dict] = None,
    attempt: int = 1,
    sha: Optional[str] = None,
    event: str = "schedule",
    branch: str = "latest",
    wall_clock_s: float = 3753.0,
) -> dict:
    """A producer report derived from the fixture (10 notebooks x 6 configurations).

    statuses / durations / set_packages / errors are keyed by (notebook, config_id) or by notebook (all configurations);
    packages = {name: version} replaces that package wherever it is installed; dir_shas = {notebook: dir_tree_sha};
    drop = notebooks without results; failed_jobs = [(config_id, batch, step name)] jobs that failed before the tests.
    """
    sha = sha or sha_for(f"commit-{run_id}")
    report = fixture_report(run_id, sha)
    run = report["run"]
    run.update(
        {
            "attempt": attempt,
            "number": run_id % 10000,
            "event": event,
            "branch": branch,
            "nightly_date": date,
            "created_at": f"{date}T00:40:00Z",
            "started_at": f"{date}T00:40:00Z",
            "finished_at": f"{date}T03:50:00Z",
            "wall_clock_s": wall_clock_s,
        }
    )
    run["commit"].update({"short_sha": sha[:7], "message": f"Nightly commit of {date}", "date": f"{date}T00:00:00Z"})
    failed_job_ids = set()
    for job in report["jobs"]:
        for config_id, batch, step in failed_jobs:
            if job["config_id"] == config_id and job["batch"] == batch:
                job.update({"conclusion": "failure", "tests_started": False, "failed_step": {"number": 5, "name": step, "url": f"{job['url']}#step:5:1"}})
                failed_job_ids.add(job["id"])
    for notebook in report["notebooks"]:
        if notebook["path"] in (dir_shas or {}):
            notebook["dir_tree_sha"] = dir_shas[notebook["path"]]
    fixture_failures = {failure["id"]: failure for failure in report["failures"]}
    results, failures = [], []
    for entry in report["results"]:
        key = (entry["notebook"], entry["config_id"])
        if entry["notebook"] in drop:
            continue
        status = "not_run" if entry["job_id"] in failed_job_ids else _lookup(statuses, key) or entry["status"]
        if status != entry["status"]:
            entry["status"] = status
            entry["skip_reason"] = "skip_config" if status == "skipped" else None
            entry["not_run_reason"] = "job_failed" if status == "not_run" else None
            if status == "timeout":
                entry["duration_s"] = entry["timeout_s"] + 4.4
            elif status not in EXECUTED:
                entry["duration_s"] = None
        duration = _lookup(durations, key)
        if duration is not None and status in EXECUTED:
            entry["duration_s"] = duration
        changed = False
        for name, version in (packages or {}).items():
            if name in entry["packages"] and entry["packages"][name] != version:
                entry["packages"][name] = version
                changed = True
        forced = _lookup(set_packages, key)
        if forced and entry["packages"]:
            entry["packages"].update(forced)
            changed = True
        if changed:
            entry["env_id"] = sha_for(json.dumps([entry["config_id"], entry["packages"]], sort_keys=True))[:12]
        entry["failure_id"] = f"{entry['config_id']}/{entry['notebook']}" if status in FAILING else None
        if status in FAILING:
            original = fixture_failures.get(entry["failure_id"])
            details = _lookup(errors, key)
            if original is not None and original["status"] == status and not details:
                failures.append(copy.deepcopy(original))
            else:
                failures.append(make_failure(entry, run_id, details))
        results.append(entry)
    report["results"] = results
    report["failures"] = failures
    report["error_groups"] = group_failures(failures)
    report["summary"].update(summarize(results, [notebook["path"] for notebook in report["notebooks"]]))
    run["conclusion"] = "failure" if failures or failed_job_ids else "success"
    return report


def sample_reports(nights: int = 24, start: str = "2026-09-17", seed: int = 7) -> list:
    """A realistic official history built from the fixture (oldest first; one calendar day without a nightly).

    - openvino 2026.4.0 -> 2026.4.1 (night 10) -> 2026.5.0.dev20261009 (last 4 nights): async-api kernel dies on Python 3.13;
    - yolov26-obb gets slower every night on Linux and times out in the last 2 nights (timeout risk);
    - hello-world is flaky on Windows / Python 3.12 (network errors);
    - hello-detection fails everywhere for 3 nights after a notebook change, fixed by the next change (MTTR);
    - openvino-tokenizers fails on Linux since transformers 5 (packages_changed, long streak);
    - vision-background-removal is much slower on Linux / Python 3.11 in the last night (duration regression) and fails on
      Windows / Python 3.13 since a notebook change 3 nights ago (notebook_changed);
    - stable-diffusion-v3 never passed on Linux / Python 3.12 (unknown cause) and is absent for 2 nights;
    - a Windows job fails before the tests in night 12 (not_run);
    - hello-segmentation errors on Windows / Python 3.11 in the last night only; night 7 is a second attempt.
    """
    rng = random.Random(seed)
    base_durations = {(entry["notebook"], entry["config_id"]): entry["duration_s"] for entry in fixture_report()["results"]}
    reports = []
    for night in range(nights):
        run_id = 37000000000 + night * 37013
        date = day(start, night + (1 if night >= 16 else 0))
        last = night == nights - 1
        if night < 10:
            openvino = "2026.4.0"
        elif night < nights - 4:
            openvino = "2026.4.1"
        else:
            openvino = "2026.5.0.dev20261009"
        statuses: dict = {ASYNC: "passed", YOLO: "passed"}
        durations = {key: round(value * rng.uniform(0.92, 1.08), 2) for key, value in base_durations.items() if value}
        errors: dict = {}
        set_packages: dict = {}
        dir_shas: dict = {}
        for config_id in LINUX:
            durations[(YOLO, config_id)] = round(640 + 18 * night + rng.uniform(-15, 15), 2)
            if night >= nights - 2:
                statuses[(YOLO, config_id)] = "timeout"
                durations[(YOLO, config_id)] = 1204.4
        if night >= nights - 4:
            statuses[(ASYNC, L13)] = statuses[(ASYNC, W13)] = "failed"
        if night >= 6 and night % 3 == 1:
            statuses[(HELLO_WORLD, W12)] = "failed"
            errors[(HELLO_WORLD, W12)] = {
                "category": "cell_error",
                "error_class": "network",
                "exception_type": "ConnectionError",
                "exception_message": "HTTPSConnectionPool(host='storage.openvinotoolkit.org', port=443): Read timed out. (read timeout=30)",
            }
        if 8 <= night <= 10:
            statuses[HELLO_DETECTION] = "failed"
            errors[HELLO_DETECTION] = {
                "category": "cell_error",
                "error_class": "missing_file",
                "exception_type": "FileNotFoundError",
                "exception_message": "[Errno 2] No such file or directory: 'model/v3/detection.xml'",
            }
            dir_shas[HELLO_DETECTION] = sha_for("hello-detection-v2")
        elif night > 10:
            dir_shas[HELLO_DETECTION] = sha_for("hello-detection-v3")
        if night >= 15:
            set_packages[TOKENIZERS] = {"transformers": "5.10.4", "huggingface-hub": "1.2.0"}
            for config_id in LINUX:
                statuses[(TOKENIZERS, config_id)] = "failed"
            errors[TOKENIZERS] = {
                "category": "cell_error",
                "error_class": "dependency",
                "exception_type": "ImportError",
                "exception_message": "cannot import name 'AutoModelForVision2Seq' from 'transformers'\n(site-packages/transformers/__init__.py)",
            }
        if night >= nights - 3:
            dir_shas[BACKGROUND] = sha_for("vision-background-removal-v2")
            statuses[(BACKGROUND, W13)] = "failed"
            errors[(BACKGROUND, W13)] = {
                "category": "cell_error",
                "error_class": "runtime",
                "exception_type": "AttributeError",
                "exception_message": "'NoneType' object has no attribute 'shape'",
            }
        statuses[(SD3, L12)] = "failed"
        errors[(SD3, L12)] = {
            "category": "cell_error",
            "error_class": "resource",
            "exception_type": "MemoryError",
            "exception_message": "Unable to allocate 1.50 GiB for an array with shape (1, 16, 128, 128, 1536) and data type float32",
        }
        if last:
            durations[(BACKGROUND, L11)] = round(base_durations[(BACKGROUND, L11)] + 185.0, 2)
            statuses[(HELLO_SEGMENTATION, W11)] = "error"
            errors[(HELLO_SEGMENTATION, W11)] = {
                "category": "infrastructure",
                "error_class": "resource",
                "exception_type": "OSError",
                "exception_message": "[WinError 1455] The paging file is too small for this operation to complete",
            }
        reports.append(
            fixture_night(
                run_id,
                date,
                statuses=statuses,
                durations=durations,
                packages={"openvino": openvino},
                set_packages=set_packages,
                dir_shas=dir_shas,
                drop=(SD3,) if night in (4, 5) else (),
                failed_jobs=((W11, "batch_1", "Install python dependencies"),) if night == 12 else (),
                errors=errors,
                attempt=2 if night == 7 else 1,
                wall_clock_s=round(3600 + rng.uniform(-300, 300) + (900 if night >= nights - 2 else 0), 1),
            )
        )
    return reports
