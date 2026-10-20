import copy
import json

import pytest

from conftest import FIXTURES_DIR
from compact import RESULT_FIELDS, catalog_from_report, clean_title, compact_report, expand_results, schema_major


@pytest.fixture
def report():
    return json.loads((FIXTURES_DIR / "nightly-report.json").read_text(encoding="utf-8"))


def test_compact_round_trip(report):
    record = compact_report(report, {"id": 7, "created_at": "2026-10-12T09:20:27Z"}, "2026-10-12T10:00:00Z")
    assert record["format"] == 1 and record["schema_version"] == report["schema_version"]
    assert record["artifact"] == {"id": 7, "created_at": "2026-10-12T09:20:27Z"} and record["ingested_at"] == "2026-10-12T10:00:00Z"
    assert record["run"] == report["run"] and record["summary"] == report["summary"] and record["configs"] == report["configs"]
    assert record["result_fields"] == list(RESULT_FIELDS)

    rows = expand_results(record)
    assert len(rows) == len(report["results"])
    for row, original in zip(rows, report["results"]):
        assert row["notebook"] == original["notebook"] and row["config_id"] == original["config_id"] and row["status"] == original["status"]
        reason = original["skip_reason"] or original["not_run_reason"]
        assert row["reason"] == (f"{reason}: {original['skip_source']}" if original["skip_reason"] and original.get("skip_source") else reason)
        assert row["duration_s"] == original["duration_s"] and row["failure_id"] == original["failure_id"] and row["env_id"] == original["env_id"]
        assert row["packages"] == original["packages"]
    assert len(record["pkgsets"]) == len({json.dumps(r["packages"], sort_keys=True) for r in report["results"]})
    assert record["notebooks"] == {n["path"]: n["dir_tree_sha"] for n in report["notebooks"]}
    assert len(json.dumps(record)) < len(json.dumps(report))


def test_compact_jobs_and_failures(report):
    report = copy.deepcopy(report)
    report["jobs"][0]["failed_step"] = {"number": 3, "name": "Install python dependencies", "url": "u"}
    failure = report["failures"][0]
    failure["traceback_excerpt"] = "\n".join(f"line {i}" for i in range(100))
    failure["cell_source_excerpt"] = "\n".join(f"src {i}" for i in range(50))
    failure["exception_message"] = "x" * 5000
    failure["log_excerpt"] = "\n".join(f"log {i}" for i in range(100))
    record = compact_report(report)
    job = record["jobs"][0]
    assert job["failed_step"] == "Install python dependencies" and job["id"] == report["jobs"][0]["id"] and "runner_name" not in job
    compact = record["failures"][0]
    assert compact["traceback_excerpt"].splitlines()[0] == "…" and compact["traceback_excerpt"].splitlines()[-1] == "line 99"
    assert len(compact["traceback_excerpt"].splitlines()) == 31
    assert compact["cell_source_excerpt"].splitlines()[0] == "src 0" and compact["cell_source_excerpt"].splitlines()[-1] == "…"
    assert len(compact["exception_message"]) == 1024
    assert compact["log_excerpt"].splitlines()[:2] == ["…", "log 40"] and compact["log_excerpt"].splitlines()[-1] == "log 99"
    assert compact["links"] == failure["links"] and compact["signature"] == failure["signature"]


def test_expand_results_tolerates_bad_pkgset_index():
    record = {
        "result_fields": list(RESULT_FIELDS),
        "pkgsets": [{"openvino": "1"}],
        "results": [["a/a.ipynb", "c", "passed", None, 1.0, 10, None, 5, None, None]],
    }
    assert expand_results(record)[0]["packages"] == {}
    assert expand_results({}) == []


def test_catalog(report):
    catalog = catalog_from_report(report)
    assert catalog["run_id"] == report["run"]["id"] and catalog["nightly_date"] == report["run"]["nightly_date"]
    assert list(catalog["notebooks"]) == sorted(n["path"] for n in report["notebooks"])
    first = catalog["notebooks"]["3D-point-pillars/pointpillars.ipynb"]
    assert first["title"] == "PointPillar for 3D object detection" and first["links"]["source"].startswith("https://github.com/")


@pytest.mark.parametrize("title, expected", [("**Bold** title", "Bold title"), ("`code` x", "code x"), (None, ""), ("  plain ", "plain")])
def test_clean_title(title, expected):
    assert clean_title(title) == expected


@pytest.mark.parametrize("version, expected", [("1.0.0", 1), ("2.3.1", 2), ("x", None), (None, None)])
def test_schema_major(version, expected):
    assert schema_major({"schema_version": version}) == expected


def test_reason_names_ignore_list_source(report):
    report = copy.deepcopy(report)
    template = report["results"][0]
    report["results"] = [
        {**template, "status": "skipped", "skip_reason": "ignore_list", "skip_source": "gpu_oom.txt", "not_run_reason": None},
        {**template, "status": "skipped", "skip_reason": "skip_config", "skip_source": None, "not_run_reason": None},
        {**template, "status": "not_run", "skip_reason": None, "not_run_reason": "job_failed"},
    ]
    rows = expand_results(compact_report(report))
    assert [row["reason"] for row in rows] == ["ignore_list: gpu_oom.txt", "skip_config", "job_failed"]
