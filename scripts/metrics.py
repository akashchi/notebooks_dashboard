"""Cross-night metrics of the dashboard (docs/DATA.md, section 2, "Details").

Stdlib only, pure functions over compact run records (data/runs/*.json). A `History` holds the official nights ordered
oldest -> newest and, in test-data mode, the test run appended after them as the last (displayed) night.
"""

import statistics
from typing import Iterable, Optional

from compact import ABSENT_CODE, STATUS_CODES, expand_results

EXECUTED = frozenset({"passed", "failed", "timeout", "error"})
FAILING = frozenset({"failed", "timeout", "error"})
FAILING_CODES = frozenset(STATUS_CODES[status] for status in FAILING)
WORST_ORDER = "ETFNPS" + ABSENT_CODE

FLAKY_MIN_SCORE = 0.3
FLAKY_MIN_EXECUTED = 4
REGRESSION_RATIO = 1.5
REGRESSION_MIN_DELTA_S = 60.0
REGRESSION_BASELINE_NIGHTS = 7
TIMEOUT_RISK_RATIO = 0.8
TIMEOUT_RISK_NIGHTS = 7
ERROR_MAX_CHARS = 200
CAUSE_MAX_PACKAGES = 3


def _run(record: Optional[dict]) -> dict:
    return (record or {}).get("run") or {}


def _id_key(run_id) -> tuple:
    return (0, run_id, "") if isinstance(run_id, int) else (1, 0, str(run_id))


def order_runs(records: Iterable[dict]) -> list:
    """Keeps the highest attempt of every run id and orders the nights by (nightly_date, run id)."""
    best: dict = {}
    for record in records:
        run = _run(record)
        run_id = run.get("id")
        if run_id is None:
            continue
        rank = (run.get("attempt") or 0, str(record.get("ingested_at") or ""))
        if run_id not in best or rank > best[run_id][0]:
            best[run_id] = (rank, record)
    ordered = [record for _, record in best.values()]
    return sorted(ordered, key=lambda record: (str(_run(record).get("nightly_date") or ""), _id_key(_run(record).get("id"))))


def run_ref(record: dict) -> dict:
    run = _run(record)
    return {"date": run.get("nightly_date"), "run_id": run.get("id"), "url": run.get("url"), "sha": (run.get("commit") or {}).get("sha")}


def failure_key(failure: dict) -> str:
    return failure.get("id") or f"{failure.get('config_id')}/{failure.get('notebook')}"


def build_series(records: list) -> dict:
    """{(notebook, config_id): [point, ...]} with one point per night that has a result, oldest first."""
    series: dict = {}
    for night, record in enumerate(records):
        dir_shas = record.get("notebooks") or {}
        for row in expand_results(record):
            notebook, config_id, status = row.get("notebook"), row.get("config_id"), row.get("status")
            if not notebook or not config_id or status not in STATUS_CODES:
                continue
            point = {
                "night": night,
                "status": status,
                "code": STATUS_CODES[status],
                "duration": row.get("duration_s"),
                "timeout": row.get("timeout_s"),
                "packages": row.get("packages") or {},
                "env_id": row.get("env_id"),
                "failure_id": row.get("failure_id"),
                "reason": row.get("reason"),
                "dir_tree_sha": dir_shas.get(notebook),
            }
            points = series.setdefault((notebook, config_id), [])
            if points and points[-1]["night"] == night:
                points[-1] = point
            else:
                points.append(point)
    return series


class History:
    """Official nights (deduplicated and ordered) followed by the optional test run."""

    def __init__(self, official: Iterable[dict], test_run: Optional[dict] = None):
        self.official = order_runs(official)
        self.test_run = test_run
        self.runs = self.official + ([test_run] if test_run is not None else [])
        self.refs = [run_ref(record) for record in self.runs]
        self.series = build_series(self.runs)
        self.failures = [{failure_key(failure): failure for failure in record.get("failures") or []} for record in self.runs]

    @property
    def displayed(self) -> Optional[int]:
        return len(self.runs) - 1 if self.runs else None

    def is_test(self, night: int) -> bool:
        return self.test_run is not None and night == len(self.runs) - 1


def point_at(points: list, night: int) -> Optional[dict]:
    for point in reversed(points):
        if point["night"] == night:
            return point
        if point["night"] < night:
            return None
    return None


def worst_code(codes: Iterable[str]) -> str:
    worst = ABSENT_CODE
    for code in codes:
        if code in WORST_ORDER and WORST_ORDER.index(code) < WORST_ORDER.index(worst):
            worst = code
    return worst


def previous_executed(points: list, night: int) -> Optional[dict]:
    for point in reversed(points):
        if point["night"] < night and point["status"] in EXECUTED:
            return point
    return None


def changes(history: History, night: int) -> dict:
    """New failures and fixes of `night` against the previous executed status of each (notebook, config).

    A failure without any earlier executed result (newly added or previously only skipped/not run) is new too,
    with `previous_status` null, unless `night` is the first night of the history.
    """
    new_failures, fixed = [], []
    for (notebook, config_id), points in sorted(history.series.items()):
        current = point_at(points, night)
        if current is None or current["status"] not in EXECUTED:
            continue
        previous = previous_executed(points, night)
        if previous is None:
            if current["status"] in FAILING and night > 0:
                new_failures.append(
                    {"notebook": notebook, "config_id": config_id, "status": current["status"], "failure_id": current["failure_id"], "previous_status": None}
                )
            continue
        if current["status"] in FAILING and previous["status"] == "passed":
            new_failures.append(
                {"notebook": notebook, "config_id": config_id, "status": current["status"], "failure_id": current["failure_id"], "previous_status": "passed"}
            )
        elif current["status"] == "passed" and previous["status"] in FAILING:
            fixed.append({"notebook": notebook, "config_id": config_id})
    return {"new_failures": new_failures, "fixed": fixed}


def failing_streak(points: list, night: int) -> tuple:
    """Executed failing points of the streak that includes `night` (oldest first) and the passed point right before it.

    Skipped, not-run and absent nights neither break nor extend a streak.
    """
    streak, last_pass = [], None
    for point in reversed(points):
        if point["night"] > night or point["status"] not in EXECUTED:
            continue
        if point["status"] in FAILING:
            streak.append(point)
        else:
            last_pass = point
            break
    streak.reverse()
    return streak, last_pass


def _version(packages: dict, name: str) -> str:
    return packages.get(name) or "none"


def _package_change(name: str, old: dict, new: dict) -> str:
    return f"{name} {_version(old, name)} → {_version(new, name)}"


def classify_cause(first_fail: dict, last_pass: Optional[dict]) -> tuple:
    """Likely cause of a failing streak: compares its first failing point with the last passed point before it.

    Returns (cause, detail). The detail lists every detected change; the cause is the first matching rule:
    notebook_changed > openvino_changed > packages_changed > external_or_flaky, or "unknown" without a previous pass.
    Packages are compared only when both points have key packages (they may be missing, e.g. for setup errors).
    """
    if last_pass is None:
        return "unknown", None
    parts = []
    old_sha, new_sha = last_pass.get("dir_tree_sha"), first_fail.get("dir_tree_sha")
    notebook_changed = bool(old_sha and new_sha and old_sha != new_sha)
    if notebook_changed:
        parts.append("notebook directory changed")
    old, new = last_pass.get("packages") or {}, first_fail.get("packages") or {}
    comparable = bool(old) and bool(new)
    openvino_changed = comparable and old.get("openvino") != new.get("openvino")
    if openvino_changed:
        parts.append(_package_change("openvino", old, new))
    others = sorted(name for name in set(old) | set(new) if name != "openvino" and old.get(name) != new.get(name)) if comparable else []
    if others:
        listed = ", ".join(_package_change(name, old, new) for name in others[:CAUSE_MAX_PACKAGES])
        if len(others) > CAUSE_MAX_PACKAGES:
            listed += f", +{len(others) - CAUSE_MAX_PACKAGES}"
        parts.append(listed)
    old_env, new_env = last_pass.get("env_id"), first_fail.get("env_id")
    env_changed = bool(old_env and new_env and old_env != new_env)
    if env_changed and not others and not openvino_changed:
        parts.append("environment changed")
    if notebook_changed:
        cause = "notebook_changed"
    elif openvino_changed:
        cause = "openvino_changed"
    elif others or env_changed:
        cause = "packages_changed"
    else:
        cause = "external_or_flaky"
    return cause, "; ".join(parts) or None


def compare_url(repository: Optional[str], last_pass: Optional[dict], since: Optional[dict]) -> Optional[str]:
    base, head = (last_pass or {}).get("sha"), (since or {}).get("sha")
    if not repository or not base or not head or base == head:
        return None
    return f"https://github.com/{repository}/compare/{base}...{head}"


def error_text(failure: Optional[dict]) -> Optional[str]:
    """'Type: message' with the first line of the message, at most ERROR_MAX_CHARS characters."""
    if not failure:
        return None
    exception_type = failure.get("exception_type")
    message = (failure.get("exception_message") or "").strip()
    first_line = message.splitlines()[0].strip() if message else ""
    if exception_type and first_line:
        text = f"{exception_type}: {first_line}"
    else:
        text = exception_type or first_line or None
    if text and len(text) > ERROR_MAX_CHARS:
        text = text[: ERROR_MAX_CHARS - 1] + "…"
    return text


def attention(history: History, night: int, repository: Optional[str], new_keys: Optional[set] = None) -> list:
    """Every failing (notebook, config) of `night` with its streak, last pass and likely cause."""
    new_keys = new_keys or set()
    failures = history.failures[night]
    items = []
    for (notebook, config_id), points in history.series.items():
        current = point_at(points, night)
        if current is None or current["status"] not in FAILING:
            continue
        streak, last_pass = failing_streak(points, night)
        since = dict(history.refs[streak[0]["night"]])
        last_pass_ref = dict(history.refs[last_pass["night"]]) if last_pass else None
        cause, detail = classify_cause(streak[0], last_pass)
        failure = failures.get(current["failure_id"]) if current["failure_id"] else None
        items.append(
            {
                "notebook": notebook,
                "config_id": config_id,
                "status": current["status"],
                "failure_id": current["failure_id"],
                "new": (notebook, config_id) in new_keys,
                "since": since,
                "streak_nights": len(streak),
                "last_pass": last_pass_ref,
                "compare_url": compare_url(repository, last_pass_ref, since),
                "cause": cause,
                "cause_detail": detail,
                "category": (failure or {}).get("category"),
                "error_class": (failure or {}).get("error_class"),
                "error": error_text(failure),
            }
        )
    items.sort(key=lambda item: (not item["new"], -item["streak_nights"], item["notebook"], item["config_id"]))
    return items


def flaky(history: History, night: int, window: int) -> list:
    """Series of the night's results that flip between pass and fail over their last `window` executed nights."""
    items = []
    for (notebook, config_id), points in history.series.items():
        if window <= 0 or point_at(points, night) is None:
            continue
        executed = [point for point in points if point["night"] <= night and point["status"] in EXECUTED][-window:]
        if len(executed) < FLAKY_MIN_EXECUTED:
            continue
        if len({point["dir_tree_sha"] for point in executed if point["dir_tree_sha"]}) > 1:
            continue
        transitions = sum(1 for a, b in zip(executed, executed[1:]) if (a["status"] in FAILING) != (b["status"] in FAILING))
        score = transitions / len(executed)
        if score < FLAKY_MIN_SCORE:
            continue
        items.append(
            {
                "notebook": notebook,
                "config_id": config_id,
                "score": round(score, 2),
                "transitions": transitions,
                "executed": len(executed),
                "history": "".join(point["code"] for point in executed),
            }
        )
    items.sort(key=lambda item: (-item["score"], item["notebook"], item["config_id"]))
    return items


def _ratio_key(item: dict) -> tuple:
    ratio = item["ratio"]
    return (-(ratio if ratio is not None else float("inf")), item["notebook"], item["config_id"])


def duration_regressions(history: History, night: int) -> list:
    """Passed results that took > 1.5x the median of the previous 7 passed durations and more than 60 s longer."""
    items = []
    for (notebook, config_id), points in history.series.items():
        current = point_at(points, night)
        if current is None or current["status"] != "passed" or current["duration"] is None:
            continue
        previous = [point["duration"] for point in points if point["night"] < night and point["status"] == "passed" and point["duration"] is not None]
        previous = previous[-REGRESSION_BASELINE_NIGHTS:]
        if not previous:
            continue
        baseline = statistics.median(previous)
        duration = current["duration"]
        if duration > REGRESSION_RATIO * baseline and duration - baseline > REGRESSION_MIN_DELTA_S:
            items.append(
                {
                    "notebook": notebook,
                    "config_id": config_id,
                    "duration_s": round(duration, 1),
                    "baseline_s": round(baseline, 1),
                    "ratio": round(duration / baseline, 2) if baseline > 0 else None,
                }
            )
    items.sort(key=_ratio_key)
    return items


def timeout_risk(history: History, night: int) -> list:
    """Executed results whose median duration over the last 7 executed nights (including this one) is >= 80% of the timeout."""
    items = []
    for (notebook, config_id), points in history.series.items():
        current = point_at(points, night)
        if current is None or current["status"] not in EXECUTED or current["duration"] is None or not current["timeout"]:
            continue
        durations = [point["duration"] for point in points if point["night"] <= night and point["status"] in EXECUTED and point["duration"] is not None]
        median = statistics.median(durations[-TIMEOUT_RISK_NIGHTS:])
        ratio = median / current["timeout"]
        if ratio >= TIMEOUT_RISK_RATIO:
            items.append(
                {
                    "notebook": notebook,
                    "config_id": config_id,
                    "median_s": round(median, 1),
                    "timeout_s": current["timeout"],
                    "ratio": round(ratio, 2),
                }
            )
    items.sort(key=_ratio_key)
    return items


def error_group_history(records: list) -> dict:
    """{signature: {"nights", "first_seen", "last_seen"}} over the given nights (ordered oldest first)."""
    seen: dict = {}
    for night, record in enumerate(records):
        date = _run(record).get("nightly_date")
        for group in record.get("error_groups") or []:
            signature = group.get("signature")
            if not signature:
                continue
            entry = seen.setdefault(signature, {"nights": set(), "first_seen": None, "last_seen": None})
            entry["nights"].add(night)
            if date:
                entry["first_seen"] = entry["first_seen"] or date
                entry["last_seen"] = date
    for entry in seen.values():
        entry["nights"] = len(entry["nights"])
    return seen


def error_groups(history: History, night: int) -> list:
    """The night's error groups (producer order) enriched with their recurrence over the nights up to and including it."""
    seen = error_group_history(history.runs[: night + 1])
    groups = []
    for group in history.runs[night].get("error_groups") or []:
        stats = seen.get(group.get("signature")) or {}
        groups.append({**group, "nights_seen": stats.get("nights", 0), "first_seen": stats.get("first_seen"), "last_seen": stats.get("last_seen")})
    return groups


def mttr_nights(history: History, window: int) -> Optional[float]:
    """Mean number of official nights from the first failing night of a streak to the night that fixed it,
    over the streaks fixed within the last `window` official nights; None if there are none."""
    official = len(history.official)
    if not official or window <= 0:
        return None
    window_start = max(0, official - window)
    lengths = []
    for points in history.series.values():
        streak_start = None
        for point in points:
            if point["night"] >= official:
                break
            if point["status"] not in EXECUTED:
                continue
            if point["status"] in FAILING:
                if streak_start is None:
                    streak_start = point["night"]
            else:
                if streak_start is not None and point["night"] >= window_start:
                    lengths.append(point["night"] - streak_start)
                streak_start = None
    return round(sum(lengths) / len(lengths), 2) if lengths else None


def failing_notebooks(record: dict) -> int:
    return len({row.get("notebook") for row in expand_results(record) if row.get("status") in FAILING})


def jobs_failed_before_tests(record: dict) -> list:
    return [
        {"name": job.get("name"), "url": job.get("url"), "config_id": job.get("config_id"), "batch": job.get("batch"), "failed_step": job.get("failed_step")}
        for job in record.get("jobs") or []
        if job.get("tests_started") is False
    ]


def night_entry(record: dict, test: bool = False) -> dict:
    """One trend.json night: run identity, rates and counts (producer summary; counted from the results when missing)."""
    run = _run(record)
    commit = run.get("commit") or {}
    summary = record.get("summary") or {}
    results = summary.get("results") or {}
    counted = {status: 0 for status in STATUS_CODES}
    for row in expand_results(record):
        if row.get("status") in counted:
            counted[row["status"]] += 1

    def count(name: str) -> Optional[int]:
        value = results.get(name)
        if value is not None:
            return value
        if name == "executed":
            return sum(counted[status] for status in EXECUTED)
        return counted.get(name)

    executed, passed = count("executed"), count("passed")
    pass_rate = summary.get("pass_rate", results.get("pass_rate"))
    if pass_rate is None and executed:
        pass_rate = round(passed / executed, 4)
    sha = commit.get("sha")
    return {
        "date": run.get("nightly_date"),
        "run_id": run.get("id"),
        "attempt": run.get("attempt"),
        "url": run.get("url"),
        "sha": sha,
        "short_sha": commit.get("short_sha") or (sha[:7] if sha else None),
        "conclusion": run.get("conclusion"),
        "test": test,
        "pass_rate": pass_rate,
        "notebook_pass_rate": summary.get("notebook_pass_rate"),
        "coverage": summary.get("coverage"),
        "executed": executed,
        "passed": passed,
        "failed": count("failed"),
        "timeout": count("timeout"),
        "error": count("error"),
        "not_run": count("not_run"),
        "skipped": count("skipped"),
        "untested": (summary.get("notebooks") or {}).get("untested"),
        "failing_notebooks": failing_notebooks(record),
        "jobs_failed_before_tests": len(jobs_failed_before_tests(record)),
        "wall_clock_s": run.get("wall_clock_s"),
        "compute_s": results.get("compute_s"),
    }


def kpi(value, previous, has_previous: bool = True) -> dict:
    """{"value", "delta"}: delta is value - previous when there is a previous night and both values are numbers."""
    if not has_previous or not _is_number(value) or not _is_number(previous):
        return {"value": value, "delta": None}
    delta = value - previous
    return {"value": value, "delta": delta if isinstance(delta, int) else round(delta, 4)}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def status_matrix(history: History, first_night: int) -> dict:
    """history.json "notebooks": status strings over nights first_night..last, per configuration and worst ("all")."""
    width = len(history.runs) - first_night
    notebooks: dict = {}
    if width <= 0:
        return notebooks
    for (notebook, config_id), points in sorted(history.series.items()):
        window = [point for point in points if point["night"] >= first_night]
        if not window:
            continue
        chars = [ABSENT_CODE] * width
        for point in window:
            chars[point["night"] - first_night] = point["code"]
        notebooks.setdefault(notebook, {"all": "", "cfg": {}})["cfg"][config_id] = "".join(chars)
    for entry in notebooks.values():
        entry["all"] = "".join(worst_code(column) for column in zip(*entry["cfg"].values()))
    return notebooks
