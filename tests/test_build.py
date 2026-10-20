import functools
import json
from pathlib import Path

import build
import pytest
from compact import catalog_from_report
from history_factory import (
    ASYNC,
    BACKGROUND,
    HELLO_DETECTION,
    HELLO_SEGMENTATION,
    HELLO_WORLD,
    L11,
    L12,
    L13,
    LINUX,
    SD3,
    TOKENIZERS,
    W11,
    W12,
    W13,
    WINDOWS,
    YOLO,
    compact,
    day,
    fixture_night,
    fixture_report,
    run_url,
    sample_reports,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-12T08:05:00Z"
STATUS_CHARS = set("PFTENS-")
META_KEYS = {
    "generated_at",
    "source",
    "windows",
    "official_nights",
    "first_night",
    "last_night",
    "test_data",
    "producer_readme",
    "dashboard_repository",
    "warnings",
}
TREND_KEYS = set(
    "date run_id attempt url sha short_sha conclusion test pass_rate notebook_pass_rate coverage executed passed failed timeout error not_run skipped "
    "untested failing_notebooks jobs_failed_before_tests wall_clock_s compute_s".split()
)
LATEST_KEYS = set(
    "test run summary previous kpis configs notebooks failures attention changes flaky duration_regressions timeout_risk error_groups mttr_nights infra".split()
)
KPI_NAMES = ["pass_rate", "notebook_pass_rate", "coverage", "failing_notebooks", "new_failures", "fixed", "not_run", "wall_clock_s"]
NOTEBOOK_KEYS = {"path", "title", "dir", "tags", "links", "last_modified", "status", "worst", "failing", "flaky", "new_failure"}
ATTENTION_KEYS = set("notebook config_id status failure_id new since streak_nights last_pass compare_url cause cause_detail category error_class error".split())
REF_KEYS = {"date", "run_id", "url", "sha"}
CAUSES = {"notebook_changed", "openvino_changed", "packages_changed", "external_or_flaky", "unknown"}


def check_meta(meta: dict) -> None:
    assert set(meta) == META_KEYS
    assert {"repository", "workflow", "workflow_url", "artifact", "branch", "event"} <= set(meta["source"])
    assert meta["source"]["workflow_url"] == "https://github.com/openvinotoolkit/openvino_notebooks/actions/workflows/treon_nightly.yml"
    assert meta["generated_at"] == NOW
    assert set(meta["windows"]) == {"trend", "matrix", "flaky"}
    assert isinstance(meta["official_nights"], int) and isinstance(meta["warnings"], list)
    if meta["test_data"] is not None:
        assert set(meta["test_data"]) == {"run_id", "url", "event", "branch", "nightly_date", "reason"}
        assert meta["test_data"]["reason"] in {"no_official_data", "requested"}


def check_trend(trend: dict) -> None:
    assert set(trend) == {"nights"}
    for night in trend["nights"]:
        assert set(night) == TREND_KEYS
        for rate in ("pass_rate", "notebook_pass_rate", "coverage"):
            assert night[rate] is None or 0 <= night[rate] <= 1
    official = [night for night in trend["nights"] if not night["test"]]
    assert official == trend["nights"][: len(official)]
    assert [night["date"] for night in official] == sorted(night["date"] for night in official)


def check_latest(latest: dict) -> None:
    assert set(latest) == LATEST_KEYS
    assert list(latest["kpis"]) == KPI_NAMES
    assert all(set(kpi) == {"value", "delta"} for kpi in latest["kpis"].values())
    assert latest["kpis"]["new_failures"]["delta"] is None and latest["kpis"]["fixed"]["delta"] is None
    assert latest["previous"] is None or set(latest["previous"]) == {"date", "run_id", "url"}
    assert all(set(config) == {"id", "device", "os", "os_family", "python"} for config in latest["configs"])
    paths = [notebook["path"] for notebook in latest["notebooks"]]
    assert paths == sorted(set(paths))
    for notebook in latest["notebooks"]:
        assert set(notebook) == NOTEBOOK_KEYS
        assert all(set(value) == {"s", "d", "f", "r"} and value["s"] in STATUS_CHARS for value in notebook["status"].values())
        codes = [value["s"] for value in notebook["status"].values()]
        assert notebook["failing"] == any(code in "FTE" for code in codes)
        assert notebook["worst"] in STATUS_CHARS and (codes or notebook["worst"] == "-")
    for item in latest["attention"]:
        assert set(item) == ATTENTION_KEYS
        assert set(item["since"]) == REF_KEYS and (item["last_pass"] is None or set(item["last_pass"]) == REF_KEYS)
        assert item["cause"] in CAUSES and item["status"] in {"failed", "timeout", "error"}
        assert item["streak_nights"] >= 1
    order = [(not item["new"], -item["streak_nights"], item["notebook"], item["config_id"]) for item in latest["attention"]]
    assert order == sorted(order)
    assert set(latest["changes"]) == {"new_failures", "fixed"}
    if latest["run"] is not None:
        assert latest["kpis"]["new_failures"]["value"] == len(latest["changes"]["new_failures"])
        assert latest["kpis"]["fixed"]["value"] == len(latest["changes"]["fixed"])
        assert {item["failure_id"] for item in latest["attention"]} <= set(latest["failures"])
    assert all(set(item) == {"notebook", "config_id", "score", "transitions", "executed", "history"} for item in latest["flaky"])
    assert all(set(item) == {"notebook", "config_id", "duration_s", "baseline_s", "ratio"} for item in latest["duration_regressions"])
    assert all(set(item) == {"notebook", "config_id", "median_s", "timeout_s", "ratio"} for item in latest["timeout_risk"])
    assert all({"signature", "occurrences", "nights_seen", "first_seen", "last_seen"} <= set(group) for group in latest["error_groups"])
    assert set(latest["infra"]) == {"jobs_failed_before_tests", "not_run"}
    assert all(set(job) == {"name", "url", "config_id", "batch", "failed_step"} for job in latest["infra"]["jobs_failed_before_tests"])


def check_history(history: dict) -> None:
    assert set(history) == {"nights", "notebooks"}
    assert all(set(night) == {"date", "run_id", "url", "test"} for night in history["nights"])
    width = len(history["nights"])
    for entry in history["notebooks"].values():
        assert set(entry) == {"all", "cfg"} and entry["cfg"]
        for codes in [entry["all"], *entry["cfg"].values()]:
            assert len(codes) == width and set(codes) <= STATUS_CHARS


def check_all(files: dict) -> None:
    check_meta(files["meta"])
    check_trend(files["trend"])
    check_latest(files["latest"])
    check_history(files["history"])


def config(**windows) -> dict:
    data = json.loads((ROOT / "dashboard.json").read_text(encoding="utf-8"))
    data["windows"] = {**data["windows"], **windows}
    return data


def write_json(path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def store(data_dir, reports, catalog: bool = True) -> None:
    for report in reports:
        record = compact(report)
        write_json(data_dir / "runs" / f"{record['run']['nightly_date']}_{record['run']['id']}.json", record)
    if catalog and reports:
        write_json(data_dir / "catalog.json", catalog_from_report(reports[-1]))


def cache_test_run(cache_dir, report, reason: str, catalog: bool = True) -> None:
    record = compact(report)
    run = record["run"]
    write_json(cache_dir / "test_run.json", record)
    meta = {
        "run_id": run["id"],
        "url": run["url"],
        "event": run["event"],
        "branch": run["branch"],
        "nightly_date": run["nightly_date"],
        "reason": reason,
        "artifact_id": 5,
    }
    write_json(cache_dir / "test_run_meta.json", meta)
    if catalog:
        write_json(cache_dir / "test_catalog.json", catalog_from_report(report))


def run_build(tmp_path, cfg=None) -> dict:
    out = tmp_path / "_site"
    documents = build.build(cfg or config(), tmp_path / "data", tmp_path / ".cache", tmp_path / "site", out, now=NOW)
    files = {name: json.loads((out / "data" / f"{name}.json").read_text(encoding="utf-8")) for name in build.DATA_FILES}
    assert files == documents
    return files


@functools.lru_cache(maxsize=1)
def five_nights() -> tuple:
    """async-api fails on Linux / Python 3.13 since night 3 (new openvino); hello-detection fails in nights 1-3;
    hello-world fails on Windows / Python 3.12 in night 4 only; yolov26-obb times out on Linux every night (fixture)."""
    reports = []
    for night in range(5):
        statuses = {ASYNC: "passed"}
        if night >= 3:
            statuses[(ASYNC, L13)] = "failed"
        if 1 <= night <= 3:
            statuses[HELLO_DETECTION] = "failed"
        if night == 4:
            statuses[(HELLO_WORLD, W12)] = "failed"
        openvino = "2026.4.1" if night < 3 else "2026.5.0.dev20261007"
        reports.append(fixture_night(1000 + night, day("2026-10-01", night), statuses=statuses, packages={"openvino": openvino}))
    return tuple(reports)


def test_build_official_history(tmp_path):
    reports = five_nights()
    store(tmp_path / "data", reports)
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>", encoding="utf-8")
    files = run_build(tmp_path)
    check_all(files)
    assert (tmp_path / "_site" / "index.html").read_text(encoding="utf-8") == "<html></html>"

    meta = files["meta"]
    assert (meta["official_nights"], meta["first_night"], meta["last_night"]) == (5, "2026-10-01", "2026-10-05")
    assert (meta["test_data"], meta["warnings"], meta["windows"]) == (None, [], {"trend": 90, "matrix": 30, "flaky": 14})
    assert meta["producer_readme"] == config()["producer_readme"]

    assert [night["run_id"] for night in files["trend"]["nights"]] == [1000, 1001, 1002, 1003, 1004]
    assert not any(night["test"] for night in files["trend"]["nights"])

    latest = files["latest"]
    assert latest["test"] is False and latest["run"]["id"] == 1004 and latest["run"]["nightly_date"] == "2026-10-05"
    assert latest["summary"]["results"]["planned"] == 60
    assert latest["previous"] == {"date": "2026-10-04", "run_id": 1003, "url": run_url(1003)}
    assert [config["id"] for config in latest["configs"]] == [*LINUX, *WINDOWS]
    assert latest["changes"]["new_failures"] == [{"notebook": HELLO_WORLD, "config_id": W12, "status": "failed", "failure_id": f"{W12}/{HELLO_WORLD}", "previous_status": "passed"}]
    assert latest["changes"]["fixed"] == [{"notebook": HELLO_DETECTION, "config_id": config_id} for config_id in [*LINUX, *WINDOWS]]
    kpis = latest["kpis"]
    assert kpis["failing_notebooks"] == {"value": 3, "delta": 0}
    assert (kpis["new_failures"]["value"], kpis["fixed"]["value"], kpis["not_run"]) == (1, 6, {"value": 0, "delta": 0})
    assert kpis["pass_rate"]["delta"] is not None and kpis["wall_clock_s"]["delta"] == 0

    attention = [(item["notebook"], item["config_id"], item["new"], item["streak_nights"], item["cause"]) for item in latest["attention"]]
    assert attention == [
        (HELLO_WORLD, W12, True, 1, "external_or_flaky"),
        (YOLO, L11, False, 5, "unknown"),
        (YOLO, L12, False, 5, "unknown"),
        (YOLO, L13, False, 5, "unknown"),
        (ASYNC, L13, False, 2, "openvino_changed"),
    ]
    async_item = latest["attention"][-1]
    assert async_item["cause_detail"] == "openvino 2026.4.1 → 2026.5.0.dev20261007"
    assert async_item["since"]["run_id"] == 1003 and async_item["last_pass"]["run_id"] == 1002
    assert (
        async_item["compare_url"]
        == f"https://github.com/openvinotoolkit/openvino_notebooks/compare/{async_item['last_pass']['sha']}...{async_item['since']['sha']}"
    )
    assert (async_item["category"], async_item["error"]) == ("kernel_died", "DeadKernelError: Kernel died")
    assert set(latest["failures"]) == {f"{L13}/{ASYNC}", f"{W12}/{HELLO_WORLD}", *(f"{config_id}/{YOLO}" for config_id in LINUX)}
    assert "→" in (tmp_path / "_site" / "data" / "latest.json").read_text(encoding="utf-8")

    groups = {group["signature"]: group for group in latest["error_groups"]}
    assert (groups["db0467f22081"]["nights_seen"], groups["db0467f22081"]["first_seen"]) == (2, "2026-10-04")
    assert (groups["10b5fae3267c"]["nights_seen"], groups["10b5fae3267c"]["first_seen"], groups["10b5fae3267c"]["last_seen"]) == (5, "2026-10-01", "2026-10-05")
    assert latest["mttr_nights"] == 3.0
    assert latest["infra"] == {"jobs_failed_before_tests": [], "not_run": 0}

    notebooks = {notebook["path"]: notebook for notebook in latest["notebooks"]}
    catalog = catalog_from_report(reports[-1])["notebooks"]
    assert set(notebooks) == set(catalog)
    assert notebooks[ASYNC]["title"] == catalog[ASYNC]["title"] and "**" not in notebooks["3D-point-pillars/pointpillars.ipynb"]["title"]
    assert (notebooks[ASYNC]["worst"], notebooks[ASYNC]["failing"], notebooks[ASYNC]["new_failure"]) == ("F", True, False)
    assert notebooks[ASYNC]["status"][L13] == {"s": "F", "d": 62.39, "f": f"{L13}/{ASYNC}", "r": None}
    assert (notebooks[HELLO_WORLD]["new_failure"], notebooks[YOLO]["worst"]) == (True, "T")
    assert notebooks["gpu-device/gpu-device.ipynb"]["worst"] == "S" and notebooks["gpu-device/gpu-device.ipynb"]["status"][L11]["r"] == "skip_config"
    assert notebooks["3D-point-pillars/pointpillars.ipynb"]["status"] == {} and notebooks["3D-point-pillars/pointpillars.ipynb"]["worst"] == "-"

    history = files["history"]
    assert [night["run_id"] for night in history["nights"]] == [1000, 1001, 1002, 1003, 1004]
    assert history["notebooks"][ASYNC]["cfg"][L13] == "PPPFF" and history["notebooks"][ASYNC]["all"] == "PPPFF"
    assert history["notebooks"][HELLO_DETECTION]["all"] == "PFFFP"
    assert history["notebooks"][YOLO]["all"] == "TTTTT" and history["notebooks"][YOLO]["cfg"][W11] == "PPPPP"
    assert set(history["notebooks"]) == {result["notebook"] for result in reports[-1]["results"]}


def test_trend_and_matrix_windows(tmp_path):
    store(tmp_path / "data", five_nights())
    files = run_build(tmp_path, config(trend=3, matrix=2))
    check_all(files)
    assert [night["run_id"] for night in files["trend"]["nights"]] == [1002, 1003, 1004]
    assert [night["run_id"] for night in files["history"]["nights"]] == [1003, 1004]
    assert files["history"]["notebooks"][ASYNC]["cfg"][L13] == "FF"
    assert files["meta"]["official_nights"] == 5 and files["meta"]["windows"] == {"trend": 3, "matrix": 2, "flaky": 14}
    assert files["latest"]["mttr_nights"] == 3.0


def test_test_data_without_official_nights(tmp_path):
    report = fixture_report()
    cache_test_run(tmp_path / ".cache", report, "no_official_data")
    files = run_build(tmp_path)
    check_all(files)
    meta = files["meta"]
    assert (meta["official_nights"], meta["first_night"], meta["last_night"]) == (0, None, None)
    assert meta["test_data"] == {
        "run_id": 38222478164,
        "url": report["run"]["url"],
        "event": "pull_request",
        "branch": "ci/nightly-data-for-dashboard",
        "nightly_date": "2026-10-12",
        "reason": "no_official_data",
    }
    assert [(night["run_id"], night["test"]) for night in files["trend"]["nights"]] == [(38222478164, True)]
    assert [(night["run_id"], night["test"]) for night in files["history"]["nights"]] == [(38222478164, True)]
    latest = files["latest"]
    assert latest["test"] is True and latest["run"]["id"] == 38222478164 and latest["previous"] is None
    assert all(kpi["delta"] is None for kpi in latest["kpis"].values())
    assert len(latest["attention"]) == 5 and {item["cause"] for item in latest["attention"]} == {"unknown"}
    assert latest["changes"] == {"new_failures": [], "fixed": []} and latest["mttr_nights"] is None
    catalog = catalog_from_report(report)["notebooks"]
    notebooks = {notebook["path"]: notebook for notebook in latest["notebooks"]}
    assert set(notebooks) == set(catalog) and notebooks[ASYNC]["title"] == catalog[ASYNC]["title"]
    assert [group["nights_seen"] for group in latest["error_groups"]] == [1, 1]


def test_requested_test_data_after_official_nights(tmp_path):
    store(tmp_path / "data", five_nights()[:3])
    cache_test_run(tmp_path / ".cache", fixture_report(), "requested")
    files = run_build(tmp_path)
    check_all(files)
    assert files["meta"]["official_nights"] == 3 and files["meta"]["test_data"]["reason"] == "requested"
    assert [night["test"] for night in files["trend"]["nights"]] == [False, False, False, True]
    history = files["history"]
    assert [night["test"] for night in history["nights"]] == [False, False, False, True]
    assert history["notebooks"][ASYNC]["cfg"][L13] == "PPPF"
    latest = files["latest"]
    assert latest["test"] is True and latest["run"]["event"] == "pull_request"
    assert latest["previous"] == {"date": "2026-10-03", "run_id": 1002, "url": run_url(1002)}
    assert {(item["notebook"], item["config_id"]) for item in latest["changes"]["new_failures"]} == {(ASYNC, L13), (ASYNC, W13)}
    assert latest["changes"]["fixed"] == [{"notebook": HELLO_DETECTION, "config_id": config_id} for config_id in [*LINUX, *WINDOWS]]
    assert latest["kpis"]["failing_notebooks"] == {"value": 2, "delta": 0}
    async_item = next(item for item in latest["attention"] if (item["notebook"], item["config_id"]) == (ASYNC, L13))
    assert async_item["new"] is True and async_item["since"]["run_id"] == 38222478164 and async_item["last_pass"]["run_id"] == 1002
    assert async_item["cause"] == "external_or_flaky"


def test_test_run_that_is_already_an_official_night_is_ignored(tmp_path):
    reports = five_nights()[:2]
    store(tmp_path / "data", reports)
    cache_test_run(tmp_path / ".cache", reports[-1], "requested")
    files = run_build(tmp_path)
    check_all(files)
    assert files["meta"]["test_data"] is None and files["latest"]["test"] is False
    assert any("already an official night" in warning for warning in files["meta"]["warnings"])
    assert [night["test"] for night in files["trend"]["nights"]] == [False, False]


def test_empty_data(tmp_path):
    files = run_build(tmp_path)
    check_all(files)
    assert (files["meta"]["official_nights"], files["meta"]["test_data"], files["meta"]["first_night"]) == (0, None, None)
    assert files["trend"] == {"nights": []}
    assert files["history"] == {"nights": [], "notebooks": {}}
    latest = files["latest"]
    assert (latest["test"], latest["run"], latest["summary"], latest["previous"], latest["notebooks"]) == (False, None, None, None, [])
    assert all(kpi == {"value": None, "delta": None} for kpi in latest["kpis"].values())
    assert latest["infra"] == {"jobs_failed_before_tests": [], "not_run": None} and latest["mttr_nights"] is None


def test_catalog_without_runs_lists_the_notebooks(tmp_path):
    write_json(tmp_path / "data" / "catalog.json", catalog_from_report(fixture_report()))
    files = run_build(tmp_path)
    check_all(files)
    notebooks = files["latest"]["notebooks"]
    assert notebooks and all(notebook["status"] == {} and notebook["worst"] == "-" for notebook in notebooks)


def test_unreadable_and_unsupported_run_files_are_skipped(tmp_path):
    store(tmp_path / "data", five_nights()[:2])
    (tmp_path / "data" / "runs" / "2026-10-09_1.json").write_text("{not json", encoding="utf-8")
    write_json(tmp_path / "data" / "runs" / "2026-10-10_2.json", {"format": 2, "run": {"id": 2}})
    write_json(tmp_path / "data" / "catalog.json", ["not", "a", "catalog"])
    files = run_build(tmp_path)
    check_all(files)
    warnings = files["meta"]["warnings"]
    assert len(warnings) == 3
    assert "2026-10-09_1.json" in warnings[0] and "2026-10-10_2.json" in warnings[1] and "catalog.json" in warnings[2]
    assert files["meta"]["official_nights"] == 2
    assert files["latest"]["notebooks"][0]["title"] == files["latest"]["notebooks"][0]["path"]


def test_the_latest_attempt_of_a_night_wins(tmp_path):
    first = fixture_night(1000, "2026-10-01", statuses={ASYNC: "failed"})
    retry = fixture_night(1000, "2026-10-01", attempt=2, statuses={ASYNC: "passed"})
    write_json(tmp_path / "data" / "runs" / "a.json", compact(retry))
    write_json(tmp_path / "data" / "runs" / "b.json", compact(first))
    files = run_build(tmp_path)
    assert files["meta"]["official_nights"] == 1
    assert [night["attempt"] for night in files["trend"]["nights"]] == [2]
    assert files["history"]["notebooks"][ASYNC]["all"] == "P"


def test_catalog_falls_back_to_the_notebooks_of_the_run(tmp_path):
    report = five_nights()[0]
    store(tmp_path / "data", [report], catalog=False)
    files = run_build(tmp_path)
    notebooks = {notebook["path"]: notebook for notebook in files["latest"]["notebooks"]}
    assert set(notebooks) == {notebook["path"] for notebook in report["notebooks"]}
    assert (notebooks[ASYNC]["title"], notebooks[ASYNC]["dir"], notebooks[ASYNC]["links"]) == (ASYNC, "async-api", {})
    assert notebooks[ASYNC]["status"][L11]["s"] == "P"


def test_notebooks_missing_from_the_catalog_are_listed(tmp_path):
    report = five_nights()[0]
    store(tmp_path / "data", [report], catalog=False)
    catalog = catalog_from_report(report)
    del catalog["notebooks"][ASYNC]
    write_json(tmp_path / "data" / "catalog.json", catalog)
    files = run_build(tmp_path)
    notebooks = {notebook["path"]: notebook for notebook in files["latest"]["notebooks"]}
    assert notebooks[ASYNC]["title"] == ASYNC and notebooks[YOLO]["title"] == catalog["notebooks"][YOLO]["title"]


def test_dashboard_repository_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/notebooks_dashboard")
    assert run_build(tmp_path)["meta"]["dashboard_repository"] == "someone/notebooks_dashboard"


def test_timestamp():
    assert build._timestamp("2026-10-12T10:05:00+02:00") == NOW
    assert build._timestamp("2026-10-12T08:05:00Z") == NOW
    assert build._timestamp(None).endswith("Z")


def test_main_writes_the_site_and_replaces_the_previous_build(tmp_path, capsys):
    store(tmp_path / "data", five_nights()[:2])
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "app.js").write_text("// app", encoding="utf-8")
    out = tmp_path / "_site"
    out.mkdir()
    (out / "index.html").write_text("old", encoding="utf-8")
    (out / "stale.txt").write_text("stale", encoding="utf-8")
    write_json(tmp_path / "dashboard.json", config())
    args = ["--config", str(tmp_path / "dashboard.json"), "--data_dir", str(tmp_path / "data"), "--cache_dir", str(tmp_path / ".cache")]
    assert build.main([*args, "--site_dir", str(site), "--out", str(out), "--now", NOW]) == 0
    assert (out / "index.html").read_text(encoding="utf-8") == "<html></html>" and (out / "app.js").exists() and not (out / "stale.txt").exists()
    assert sorted(path.name for path in (out / "data").iterdir()) == ["history.json", "latest.json", "meta.json", "trend.json"]
    assert "2 official night(s)" in capsys.readouterr().out


def test_refuses_to_clean_an_unrelated_directory(tmp_path):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("keep", encoding="utf-8")
    write_json(tmp_path / "dashboard.json", config())
    args = ["--config", str(tmp_path / "dashboard.json"), "--data_dir", str(tmp_path / "data"), "--cache_dir", str(tmp_path / ".cache")]
    assert build.main([*args, "--site_dir", str(tmp_path / "site"), "--out", str(out)]) == 1
    assert (out / "notes.txt").read_text(encoding="utf-8") == "keep"
    with pytest.raises(ValueError):
        build.write_site({}, tmp_path / "site", tmp_path)


def test_sample_history_covers_every_section():
    reports = sample_reports()
    documents = build.compute(config(), [compact(report) for report in reports], catalog_from_report(reports[-1]), now=NOW)
    check_all(documents)
    latest, history = documents["latest"], documents["history"]
    assert len(documents["trend"]["nights"]) == 24 and len(history["nights"]) == 24
    causes = {(item["notebook"], item["config_id"]): item["cause"] for item in latest["attention"]}
    assert causes[(ASYNC, L13)] == "openvino_changed" and causes[(TOKENIZERS, L11)] == "packages_changed"
    assert causes[(YOLO, L11)] == "external_or_flaky" and causes[(BACKGROUND, W13)] == "notebook_changed" and causes[(SD3, L12)] == "unknown"
    assert [(item["notebook"], item["config_id"]) for item in latest["changes"]["new_failures"]] == [(HELLO_SEGMENTATION, W11)]
    assert [(item["notebook"], item["config_id"]) for item in latest["flaky"]] == [(HELLO_WORLD, W12)]
    assert [(item["notebook"], item["config_id"]) for item in latest["duration_regressions"]] == [(BACKGROUND, L11)]
    assert {(item["notebook"], item["config_id"]) for item in latest["timeout_risk"]} == {(YOLO, config_id) for config_id in LINUX}
    assert latest["mttr_nights"] == 2.0
    assert history["notebooks"][SD3]["cfg"][L11][4:6] == "--" and history["notebooks"][HELLO_WORLD]["cfg"][W11][12] == "N"
    assert documents["trend"]["nights"][12]["jobs_failed_before_tests"] == 1 and documents["trend"]["nights"][7]["attempt"] == 2
    assert L12 in history["notebooks"][SD3]["cfg"]
