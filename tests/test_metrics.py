import metrics
import pytest
from compact import compact_report
from history_factory import CFG, KEY, NB, REPOSITORY, fixture_report, job_entry, make_run, result, run_url, runs_from_codes, sha_for

CFG2 = "cpu-windows-2022-py3.12"
OV_OLD = {"openvino": "2026.4.1", "numpy": "2.4.6"}
OV_NEW = {"openvino": "2026.5.0.dev20261007", "numpy": "2.4.6"}


def k(name: str, config_id: str = CFG) -> tuple:
    return (f"{name}/{name}.ipynb", config_id)


def history_of(series: dict, **kwargs) -> metrics.History:
    return metrics.History(runs_from_codes(series, **kwargs))


def point(**fields) -> dict:
    base = {
        "night": 0,
        "status": "passed",
        "code": "P",
        "duration": 100.0,
        "timeout": 1200,
        "packages": dict(OV_OLD),
        "env_id": "env-1",
        "failure_id": None,
        "reason": None,
        "dir_tree_sha": "d1",
    }
    base.update(fields)
    return base


def test_order_runs_keeps_the_latest_attempt_and_orders_by_date_then_id():
    first = make_run(5, "2026-10-02", [result(status="failed")], attempt=1)
    retry = make_run(5, "2026-10-02", [result(status="passed")], attempt=2)
    older = make_run(9, "2026-10-01", [result()])
    same_day = make_run(3, "2026-10-02", [result()])
    ordered = metrics.order_runs([retry, first, older, same_day])
    assert [record["run"]["id"] for record in ordered] == [9, 3, 5]
    assert ordered[-1]["run"]["attempt"] == 2
    assert metrics.order_runs([first, retry])[0]["run"]["attempt"] == 2
    history = metrics.History([first, retry])
    assert history.series[KEY][0]["status"] == "passed"


def test_changes_new_failures_and_fixed():
    history = history_of({k("a"): "PF", k("b"): "FP", k("c"): "FF", k("d"): "-F", k("e"): "PS", k("f"): "PT", k("g"): "PE"})
    changes = metrics.changes(history, history.displayed)
    assert changes["new_failures"] == [
        {"notebook": "a/a.ipynb", "config_id": CFG, "status": "failed", "failure_id": f"{CFG}/a/a.ipynb", "previous_status": "passed"},
        {"notebook": "d/d.ipynb", "config_id": CFG, "status": "failed", "failure_id": f"{CFG}/d/d.ipynb", "previous_status": None},
        {"notebook": "f/f.ipynb", "config_id": CFG, "status": "timeout", "failure_id": f"{CFG}/f/f.ipynb", "previous_status": "passed"},
        {"notebook": "g/g.ipynb", "config_id": CFG, "status": "error", "failure_id": f"{CFG}/g/g.ipynb", "previous_status": "passed"},
    ]
    assert changes["fixed"] == [{"notebook": "b/b.ipynb", "config_id": CFG}]


def test_changes_look_back_over_skipped_not_run_and_absent_nights():
    history = history_of({k("a"): "PS-NF", k("b"): "FN-SP", k("c"): "SSSSF", k("d"): "TS-SP"})
    changes = metrics.changes(history, history.displayed)
    assert [item["notebook"] for item in changes["new_failures"]] == ["a/a.ipynb", "c/c.ipynb"]
    assert changes["fixed"] == [{"notebook": "b/b.ipynb", "config_id": CFG}, {"notebook": "d/d.ipynb", "config_id": CFG}]


def test_changes_without_an_earlier_result_are_new_except_on_the_first_night():
    history = history_of({k("a"): "PP", k("b"): "-F", k("c"): "NT", k("d"): "-P"})
    assert metrics.changes(history, 0) == {"new_failures": [], "fixed": []}
    changes = metrics.changes(history, history.displayed)
    assert [(item["notebook"], item["status"], item["previous_status"]) for item in changes["new_failures"]] == [
        ("b/b.ipynb", "failed", None),
        ("c/c.ipynb", "timeout", None),
    ]
    assert changes["fixed"] == []
    first = history_of({k("a"): "F"})
    assert metrics.changes(first, first.displayed)["new_failures"] == []


def test_streak_ignores_skipped_not_run_and_absent_nights():
    shas = [sha_for(night) for night in range(8)]
    history = history_of({KEY: "PPFSF-NF"}, shas=shas)
    [item] = metrics.attention(history, history.displayed, REPOSITORY)
    assert item["streak_nights"] == 3
    assert item["since"] == {"date": "2026-10-03", "run_id": 1002, "url": run_url(1002), "sha": shas[2]}
    assert item["last_pass"] == {"date": "2026-10-02", "run_id": 1001, "url": run_url(1001), "sha": shas[1]}
    assert item["compare_url"] == f"https://github.com/{REPOSITORY}/compare/{shas[1]}...{shas[2]}"
    assert item["new"] is False


def test_compare_url():
    same = sha_for("same")
    history = history_of({KEY: "PF"}, shas=[same, same])
    [item] = metrics.attention(history, 1, REPOSITORY)
    assert item["last_pass"]["sha"] == item["since"]["sha"] == same
    assert item["compare_url"] is None
    assert metrics.compare_url(REPOSITORY, {"sha": "a1"}, {"sha": "b2"}) == f"https://github.com/{REPOSITORY}/compare/a1...b2"
    assert metrics.compare_url(None, {"sha": "a1"}, {"sha": "b2"}) is None
    assert metrics.compare_url(REPOSITORY, None, {"sha": "b2"}) is None
    assert metrics.compare_url(REPOSITORY, {"sha": None}, {"sha": "b2"}) is None


def test_unknown_cause_without_a_previous_pass():
    history = history_of({KEY: "SFF"})
    [item] = metrics.attention(history, history.displayed, REPOSITORY)
    assert item["last_pass"] is None and item["compare_url"] is None
    assert (item["cause"], item["cause_detail"]) == ("unknown", None)
    assert item["streak_nights"] == 2 and item["since"]["run_id"] == 1001


def cause_of(codes: str = "PF", **kwargs) -> tuple:
    history = history_of({KEY: codes}, **kwargs)
    [item] = metrics.attention(history, history.displayed, REPOSITORY)
    return item["cause"], item["cause_detail"]


def test_cause_notebook_changed():
    assert cause_of(dir_shas={NB: ["d1", "d2"]}) == ("notebook_changed", "notebook directory changed")
    detail = "notebook directory changed; openvino 2026.4.1 → 2026.5.0.dev20261007"
    assert cause_of(dir_shas={NB: ["d1", "d2"]}, packages={KEY: [OV_OLD, OV_NEW]}, env_ids={KEY: ["e1", "e2"]}) == ("notebook_changed", detail)


def test_cause_openvino_changed():
    expected = ("openvino_changed", "openvino 2026.4.1 → 2026.5.0.dev20261007")
    assert cause_of(packages={KEY: [OV_OLD, OV_NEW]}, env_ids={KEY: ["e1", "e2"]}) == expected
    torch_old, torch_new = {**OV_OLD, "torch": "2.8.0+cpu"}, {**OV_NEW, "torch": "2.9.0+cpu"}
    assert cause_of(packages={KEY: [torch_old, torch_new]}) == ("openvino_changed", "openvino 2026.4.1 → 2026.5.0.dev20261007; torch 2.8.0+cpu → 2.9.0+cpu")


def test_cause_packages_changed():
    old = {**OV_OLD, "transformers": "4.57.6", "torch": "2.8.0+cpu"}
    new = {**OV_OLD, "transformers": "5.10.4", "torch": "2.9.0+cpu"}
    assert cause_of(packages={KEY: [old, new]}) == ("packages_changed", "torch 2.8.0+cpu → 2.9.0+cpu, transformers 4.57.6 → 5.10.4")
    assert cause_of(env_ids={KEY: ["e1", "e2"]}) == ("packages_changed", "environment changed")


def test_cause_detail_lists_at_most_three_packages():
    old = {"openvino": "2026.4.1", "huggingface-hub": "0.36.2", "nncf": "3.4.0", "numpy": "2.4.6", "torch": "2.8.0+cpu", "transformers": "4.57.6"}
    new = {
        "openvino": "2026.4.1",
        "huggingface-hub": "1.2.0",
        "nncf": "3.5.0",
        "numpy": "2.5.4",
        "torch": "2.9.0+cpu",
        "transformers": "5.10.4",
        "diffusers": "0.39.0",
    }
    cause, detail = metrics.classify_cause(point(status="failed", packages=new, env_id="env-2"), point(packages=old))
    assert cause == "packages_changed"
    assert detail == "diffusers none → 0.39.0, huggingface-hub 0.36.2 → 1.2.0, nncf 3.4.0 → 3.5.0, +3"


def test_cause_external_or_flaky_and_unknown_inputs():
    passed = point()
    assert metrics.classify_cause(point(status="failed"), passed) == ("external_or_flaky", None)
    assert metrics.classify_cause(point(status="failed", dir_tree_sha=None), passed) == ("external_or_flaky", None)
    assert metrics.classify_cause(point(status="failed", packages={}, env_id=None), passed) == ("external_or_flaky", None)
    assert metrics.classify_cause(point(status="failed", dir_tree_sha="d2"), passed) == ("notebook_changed", "notebook directory changed")
    assert metrics.classify_cause(point(status="failed"), None) == ("unknown", None)
    assert cause_of() == ("external_or_flaky", None)


def test_cause_compares_the_first_failing_night_with_the_last_pass():
    assert cause_of("PFF", packages={KEY: [OV_OLD, OV_OLD, OV_NEW]}, env_ids={KEY: ["e1", "e1", "e2"]}) == ("external_or_flaky", None)
    assert cause_of("PFF", packages={KEY: [OV_OLD, OV_NEW, OV_NEW]}) == ("openvino_changed", "openvino 2026.4.1 → 2026.5.0.dev20261007")
    assert cause_of("PPSF", dir_shas={NB: ["d1", "d1", "d2", "d2"]}) == ("notebook_changed", "notebook directory changed")


def test_error_text():
    assert (
        metrics.error_text({"exception_type": "ModuleNotFoundError", "exception_message": "No module named 'x'\nmore"})
        == "ModuleNotFoundError: No module named 'x'"
    )
    assert (
        metrics.error_text({"exception_type": None, "exception_message": "Timeout reached (1200s), process killed"})
        == "Timeout reached (1200s), process killed"
    )
    assert metrics.error_text({"exception_type": "DeadKernelError", "exception_message": None}) == "DeadKernelError"
    assert metrics.error_text({}) is None and metrics.error_text(None) is None
    long = metrics.error_text({"exception_type": "ValueError", "exception_message": "x" * 500})
    assert len(long) == 200 and long.startswith("ValueError: xxx") and long.endswith("…")


def test_attention_details_and_order():
    failures = {
        k("a"): {"exception_type": "ModuleNotFoundError", "exception_message": "No module named 'x'", "category": "cell_error", "error_class": "dependency"}
    }
    history = history_of({k("a"): "PPF", k("b"): "FFF", k("c"): "PFF", k("d"): "PPT", k("e"): "PPP"}, failures=failures)
    changes = metrics.changes(history, 2)
    new_keys = {(item["notebook"], item["config_id"]) for item in changes["new_failures"]}
    items = metrics.attention(history, 2, REPOSITORY, new_keys)
    assert [(item["notebook"], item["new"], item["streak_nights"]) for item in items] == [
        ("a/a.ipynb", True, 1),
        ("d/d.ipynb", True, 1),
        ("b/b.ipynb", False, 3),
        ("c/c.ipynb", False, 2),
    ]
    first, timeout = items[0], items[1]
    assert (first["status"], first["failure_id"]) == ("failed", f"{CFG}/a/a.ipynb")
    assert (first["category"], first["error_class"], first["error"]) == ("cell_error", "dependency", "ModuleNotFoundError: No module named 'x'")
    assert (timeout["status"], timeout["category"], timeout["error"]) == ("timeout", "timeout", "Timeout reached (1200s), process killed")


def flaky_of(codes: str, window: int = 14, **kwargs) -> list:
    history = history_of({KEY: codes}, **kwargs)
    return metrics.flaky(history, history.displayed, window)


def test_flaky_detection():
    assert flaky_of("PFPFPF") == [{"notebook": NB, "config_id": CFG, "score": 0.83, "transitions": 5, "executed": 6, "history": "PFPFPF"}]
    [item] = flaky_of("PFPS-NPF")
    assert (item["history"], item["transitions"], item["executed"], item["score"]) == ("PFPPF", 3, 5, 0.6)
    assert flaky_of("PTPE")[0]["transitions"] == 3


def test_flaky_needs_four_executed_nights_and_the_score_threshold():
    assert flaky_of("PFP") == []
    assert flaky_of("PFSSP") == []
    assert flaky_of("PPPPPPPF") == []
    assert flaky_of("PPPPFFFFPP") == []
    assert flaky_of("PPPPFFFPPF")[0]["score"] == 0.3


def test_flaky_window():
    assert flaky_of("PFPFPF" + "P" * 14) == []
    [item] = flaky_of("PFPFPF" + "P" * 4, window=8)
    assert (item["history"], item["score"]) == ("PFPFPPPP", 0.5)


def test_flaky_excludes_notebook_changes_within_the_window():
    assert flaky_of("PFPFPF", dir_shas={NB: ["d1", "d1", "d1", "d2", "d2", "d2"]}) == []
    assert flaky_of("PPPFPFPF", window=6, dir_shas={NB: ["d1", "d2", "d2", "d2", "d2", "d2", "d2", "d2"]}) != []


def test_flaky_only_lists_results_of_the_night_sorted_by_score():
    assert flaky_of("PFPFPF-") == []
    history = history_of({k("a"): "PPPFPPFP", k("b"): "PFPFPFPF", k("c"): "PPPPPPPP"})
    items = metrics.flaky(history, history.displayed, 14)
    assert [(item["notebook"], item["score"]) for item in items] == [("b/b.ipynb", 0.88), ("a/a.ipynb", 0.5)]


def regressions_of(codes: str, durations: list) -> list:
    history = history_of({KEY: codes}, durations={KEY: durations})
    return metrics.duration_regressions(history, history.displayed)


def test_duration_regression():
    assert regressions_of("P" * 8, [100.0] * 7 + [400.0]) == [{"notebook": NB, "config_id": CFG, "duration_s": 400.0, "baseline_s": 100.0, "ratio": 4.0}]
    assert regressions_of("P" * 8, [100.0] * 7 + [161.0]) != []


def test_no_duration_regression_below_the_thresholds():
    assert regressions_of("P" * 8, [100.0] * 7 + [155.0]) == []
    assert regressions_of("P" * 8, [200.0] * 7 + [290.0]) == []
    assert regressions_of("PPPPF", [100.0] * 4 + [900.0]) == []
    assert regressions_of("FP", [100.0, 900.0]) == []


def test_duration_baseline_is_the_median_of_the_previous_seven_passes():
    [item] = regressions_of("P" * 9, [1000.0] * 4 + [100.0] * 4 + [400.0])
    assert (item["baseline_s"], item["ratio"]) == (100.0, 4.0)
    [item] = regressions_of("PPPFFFFP", [100.0] * 3 + [900.0] * 4 + [400.0])
    assert item["baseline_s"] == 100.0


def risk_of(codes: str, durations: list, timeout_s: int = 1200) -> list:
    history = history_of({KEY: codes}, durations={KEY: durations}, timeout_s=timeout_s)
    return metrics.timeout_risk(history, history.displayed)


def test_timeout_risk():
    assert risk_of("P" * 7, [1000.0] * 7) == [{"notebook": NB, "config_id": CFG, "median_s": 1000.0, "timeout_s": 1200, "ratio": 0.83}]
    [item] = risk_of("PPPPPPT", [960.0] * 6 + [1204.4])
    assert (item["median_s"], item["ratio"]) == (960.0, 0.8)
    assert risk_of("P" * 7, [900.0] * 7) == []
    assert risk_of("P" * 7, [500.0] * 7, timeout_s=600) != []


def test_timeout_risk_uses_the_last_seven_executed_nights_including_this_one():
    assert risk_of("P" * 17, [100.0] * 10 + [1000.0] * 7) != []
    assert risk_of("P" * 17, [1000.0] * 10 + [100.0] * 7) == []
    assert risk_of("PPPPPPS", [1000.0] * 6 + [None]) == []


def test_mttr_nights():
    history = history_of({KEY: "PFFPPFP"})
    assert metrics.mttr_nights(history, 90) == 1.5
    assert metrics.mttr_nights(history, 2) == 1.0
    assert metrics.mttr_nights(history_of({KEY: "PFF"}), 90) is None
    assert metrics.mttr_nights(history_of({KEY: "PFSNP"}), 90) == 3.0
    assert metrics.mttr_nights(history_of({k("a"): "PFP", k("b"): "FFFP"}), 90) == 2.0


def test_mttr_ignores_the_test_run():
    history = metrics.History(runs_from_codes({KEY: "PF"}), make_run(2000, "2026-10-05", [result(status="passed")]))
    assert metrics.mttr_nights(history, 90) is None


def test_error_group_recurrence():
    failures = {KEY: {"signature": "sig-a", "exception_type": "ValueError", "exception_message": "bad"}}
    history = history_of({KEY: "FFPF", k("b"): "PPPT"}, failures=failures)
    groups = {group["signature"]: group for group in metrics.error_groups(history, history.displayed)}
    group = groups.pop("sig-a")
    assert (group["nights_seen"], group["first_seen"], group["last_seen"]) == (3, "2026-10-01", "2026-10-04")
    assert (group["occurrences"], group["notebooks"], group["exception_type"]) == (1, [NB], "ValueError")
    [timeout] = groups.values()
    assert (timeout["category"], timeout["nights_seen"], timeout["first_seen"], timeout["last_seen"]) == ("timeout", 1, "2026-10-04", "2026-10-04")


def test_error_group_recurrence_includes_the_test_run():
    failures = {KEY: {"signature": "sig-a"}}
    official = runs_from_codes({KEY: "FP"}, failures=failures)
    history = metrics.History(official, make_run(2000, "2026-10-09", [result(status="failed", failure=failures[KEY])]))
    [group] = metrics.error_groups(history, history.displayed)
    assert (group["nights_seen"], group["first_seen"], group["last_seen"]) == (2, "2026-10-01", "2026-10-09")


@pytest.mark.parametrize("codes, worst", [("ETFNPS-", "E"), ("TFNPS-", "T"), ("SFNP-", "F"), ("NPS-", "N"), ("-SP", "P"), ("S-", "S"), ("-", "-"), ("", "-")])
def test_worst_code(codes, worst):
    assert metrics.worst_code(codes) == worst


def test_status_matrix():
    history = history_of({k("a"): "PPF-", k("a", CFG2): "PSPP", k("b"): "--SP", k("c"): "PP--"})
    matrix = metrics.status_matrix(history, 0)
    assert matrix["a/a.ipynb"] == {"all": "PPFP", "cfg": {CFG: "PPF-", CFG2: "PSPP"}}
    assert matrix["b/b.ipynb"] == {"all": "--SP", "cfg": {CFG: "--SP"}}
    window = metrics.status_matrix(history, 2)
    assert window["a/a.ipynb"]["cfg"] == {CFG: "F-", CFG2: "PP"}
    assert "c/c.ipynb" not in window
    assert metrics.status_matrix(history, 4) == {}


def test_test_run_is_compared_with_the_official_history():
    official = runs_from_codes({KEY: "PPP", k("b"): "FFF"})
    test_run = make_run(99, "2026-09-01", [result(status="failed"), result("b/b.ipynb", CFG, "passed")])
    history = metrics.History(official, test_run)
    night = history.displayed
    assert night == 3 and history.is_test(3) and not history.is_test(2)
    changes = metrics.changes(history, night)
    assert [item["notebook"] for item in changes["new_failures"]] == [NB]
    assert changes["fixed"] == [{"notebook": "b/b.ipynb", "config_id": CFG}]
    [item] = metrics.attention(history, night, REPOSITORY)
    assert (item["since"]["run_id"], item["last_pass"]["run_id"]) == (99, 1002)


def test_night_entry_from_the_producer_report():
    entry = metrics.night_entry(compact_report(fixture_report()))
    assert (entry["run_id"], entry["date"], entry["attempt"], entry["test"]) == (38222478164, "2026-10-12", 1, False)
    assert (entry["executed"], entry["passed"], entry["failed"], entry["timeout"], entry["error"], entry["skipped"], entry["not_run"]) == (
        44,
        39,
        2,
        3,
        0,
        16,
        0,
    )
    assert (entry["pass_rate"], entry["notebook_pass_rate"], entry["coverage"]) == (0.8864, 0.8864, 1.0)
    assert (entry["failing_notebooks"], entry["jobs_failed_before_tests"], entry["untested"]) == (2, 0, 161)
    assert (entry["short_sha"], entry["conclusion"], entry["wall_clock_s"], entry["compute_s"]) == ("0a92f12", "failure", 3753.0, 11712.0)


def test_night_entry_counts_results_without_a_summary():
    record = make_run(1, "2026-10-01", [result(), result("b/b.ipynb", status="failed"), result("c/c.ipynb", status="skipped")])
    record["summary"] = {}
    entry = metrics.night_entry(record, test=True)
    assert (entry["executed"], entry["passed"], entry["failed"], entry["skipped"], entry["not_run"]) == (2, 1, 1, 1, 0)
    assert (entry["pass_rate"], entry["failing_notebooks"], entry["test"]) == (0.5, 1, True)


def test_jobs_failed_before_tests():
    jobs = [job_entry(1, CFG, "batch_0", 0), job_entry(1, CFG2, "batch_1", 1, failed_step="Install python dependencies")]
    record = make_run(1, "2026-10-01", [result(), result(config_id=CFG2, status="not_run")], jobs=jobs)
    expected = {"name": jobs[1]["name"], "url": jobs[1]["url"], "config_id": CFG2, "batch": "batch_1", "failed_step": "Install python dependencies"}
    assert metrics.jobs_failed_before_tests(record) == [expected]
    assert metrics.night_entry(record)["jobs_failed_before_tests"] == 1


def test_kpi_delta():
    assert metrics.kpi(0.98, 0.99) == {"value": 0.98, "delta": -0.01}
    assert metrics.kpi(4, 3) == {"value": 4, "delta": 1}
    assert metrics.kpi(12634.0, 12934.0) == {"value": 12634.0, "delta": -300.0}
    assert metrics.kpi(None, 3) == {"value": None, "delta": None}
    assert metrics.kpi(3, None) == {"value": 3, "delta": None}
    assert metrics.kpi(3, 2, has_previous=False) == {"value": 3, "delta": None}
