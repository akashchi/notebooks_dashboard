import copy
import io
import json
import zipfile
from pathlib import Path

import pytest

import ingest
from compact import expand_results
from conftest import FIXTURES_DIR, ROOT
from github_api import GitHubApiError

CONFIG = json.loads((ROOT / "dashboard.json").read_text(encoding="utf-8"))
REPORT = json.loads((FIXTURES_DIR / "nightly-report.json").read_text(encoding="utf-8"))


def official_report(run_id: int, nightly_date: str, attempt: int = 1, **run_overrides) -> dict:
    report = copy.deepcopy(REPORT)
    report["run"].update(id=run_id, attempt=attempt, nightly_date=nightly_date, event="schedule", branch="latest", workflow="treon_nightly")
    report["run"].update(run_overrides)
    return report


def make_zip(report=None, raw: bytes = None, name: str = "nightly-report.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, raw if raw is not None else json.dumps(report))
        archive.writestr("summary.md", "# summary")
    return buffer.getvalue()


def artifact(artifact_id: int, run_id: int, created_at: str, branch: str = "latest", expired: bool = False) -> dict:
    return {"id": artifact_id, "created_at": created_at, "expired": expired, "workflow_run": {"id": run_id, "head_branch": branch}}


class FakeApi:
    def __init__(self, artifacts, payloads):
        self.artifacts = artifacts
        self.payloads = payloads
        self.downloads = []

    def list_artifacts(self, repository, name):
        assert repository == CONFIG["source"]["repository"] and name == CONFIG["source"]["artifact"]
        return list(self.artifacts)

    def download_artifact_zip(self, repository, artifact_id):
        self.downloads.append(artifact_id)
        payload = self.payloads[artifact_id]
        if isinstance(payload, Exception):
            raise payload
        return payload


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "data", tmp_path / "cache"


def run_ingest(api, dirs, **kwargs):
    data_dir, cache_dir = dirs
    return ingest.ingest(api, CONFIG, data_dir, cache_dir, now="2026-10-12T08:00:00Z", log=lambda message: None, **kwargs)


def index(data_dir: Path) -> dict:
    return json.loads((data_dir / "ingested.json").read_text(encoding="utf-8"))["runs"]


def test_new_official_nights_are_stored(dirs):
    data_dir, cache_dir = dirs
    api = FakeApi(
        [artifact(11, 101, "2026-10-10T05:00:00Z"), artifact(12, 102, "2026-10-11T05:00:00Z")],
        {11: make_zip(official_report(101, "2026-10-10")), 12: make_zip(official_report(102, "2026-10-11"))},
    )
    summary = run_ingest(api, dirs)
    assert summary["new"] == [101, 102] and not summary["warnings"] and summary["test_run"] is None
    record = json.loads((data_dir / "runs" / "2026-10-11_102.json").read_text(encoding="utf-8"))
    assert record["artifact"] == {"id": 12, "created_at": "2026-10-11T05:00:00Z"} and record["ingested_at"] == "2026-10-12T08:00:00Z"
    assert len(expand_results(record)) == len(REPORT["results"])
    assert index(data_dir)["102"] == {
        "attempt": 1,
        "artifact_id": 12,
        "artifact_created_at": "2026-10-11T05:00:00Z",
        "file": "runs/2026-10-11_102.json",
        "nightly_date": "2026-10-11",
    }
    catalog = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["run_id"] == 102 and len(catalog["notebooks"]) == len(REPORT["notebooks"])
    assert not list(cache_dir.glob("*")) if cache_dir.exists() else True

    # Idempotent: nothing is downloaded again.
    api.downloads.clear()
    summary = run_ingest(api, dirs)
    assert api.downloads == [] and summary["skipped"] == [101, 102] and summary["new"] == []


def test_filters_expired_and_other_branches(dirs):
    api = FakeApi(
        [artifact(1, 100, "2026-10-10T05:00:00Z", expired=True), artifact(2, 200, "2026-10-11T05:00:00Z", branch="feature")],
        {2: make_zip(REPORT)},
    )
    summary = run_ingest(api, dirs)
    # Only the test-data fallback downloads (the newest non-expired artifact of any branch).
    assert api.downloads == [2] and summary["new"] == [] and summary["test_run"]["reason"] == "no_official_data"


def test_rerun_with_newer_attempt_replaces_record(dirs):
    data_dir, _ = dirs
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: make_zip(official_report(100, "2026-10-10"))})
    run_ingest(api, dirs)
    api.artifacts = [artifact(2, 100, "2026-10-11T09:00:00Z")]
    api.payloads[2] = make_zip(official_report(100, "2026-10-11", attempt=2))
    summary = run_ingest(api, dirs)
    assert summary["updated"] == [100]
    assert not (data_dir / "runs" / "2026-10-10_100.json").exists() and (data_dir / "runs" / "2026-10-11_100.json").exists()
    assert index(data_dir)["100"]["attempt"] == 2 and index(data_dir)["100"]["artifact_id"] == 2


def test_older_attempt_is_ignored(dirs):
    data_dir, _ = dirs
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: make_zip(official_report(100, "2026-10-10", attempt=3))})
    run_ingest(api, dirs)
    api.artifacts = [artifact(2, 100, "2026-10-11T05:00:00Z")]
    api.payloads[2] = make_zip(official_report(100, "2026-10-10", attempt=2))
    summary = run_ingest(api, dirs)
    assert summary["rejected"] == [100] and index(data_dir)["100"]["attempt"] == 3 and index(data_dir)["100"]["rejected_artifact_ids"] == [2]
    api.downloads.clear()
    run_ingest(api, dirs)
    assert api.downloads == []


@pytest.mark.parametrize(
    "report, reason",
    [
        (official_report(100, "2026-10-10") | {"schema_version": "2.0.0"}, "unsupported schema_version"),
        (official_report(100, "2026-10-10", event="workflow_dispatch"), "run.event"),
        (official_report(100, "2026-10-10", workflow="other"), "run.workflow"),
        (official_report(999, "2026-10-10"), "run.id"),
    ],
)
def test_invalid_reports_are_rejected_once(dirs, report, reason):
    data_dir, _ = dirs
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: make_zip(report)})
    summary = run_ingest(api, dirs)
    assert summary["rejected"] == [100] and reason in summary["warnings"][0]
    assert not list((data_dir / "runs").glob("*")) if (data_dir / "runs").exists() else True
    # Not downloaded again as an official night (the test-data fallback may still show it).
    summary = run_ingest(api, dirs)
    assert summary["skipped"] == [100] and summary["rejected"] == []


def test_bad_artifacts_do_not_stop_ingestion(dirs):
    data_dir, _ = dirs
    api = FakeApi(
        [
            artifact(1, 100, "2026-10-08T05:00:00Z"),
            artifact(2, 101, "2026-10-09T05:00:00Z"),
            artifact(3, 102, "2026-10-10T05:00:00Z"),
            artifact(4, 103, "2026-10-11T05:00:00Z"),
        ],
        {
            1: b"not a zip",
            2: make_zip(raw=b"{broken"),
            3: GitHubApiError("HTTP 500", 500),
            4: make_zip(official_report(103, "2026-10-11")),
        },
    )
    summary = run_ingest(api, dirs)
    assert summary["new"] == [103] and summary["rejected"] == [101] and len(summary["warnings"]) == 3
    # Download problems are retried, unusable content is not.
    api.downloads.clear()
    run_ingest(api, dirs)
    assert sorted(api.downloads) == [1, 3]


def test_missing_report_in_zip_is_rejected(dirs):
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: make_zip(REPORT, name="other.json")})
    assert run_ingest(api, dirs)["rejected"] == [100]


def test_authorization_errors_stop_ingestion(dirs):
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: GitHubApiError("HTTP 403", 403)})
    with pytest.raises(GitHubApiError):
        run_ingest(api, dirs)


def test_catalog_is_not_regressed_by_backfill(dirs):
    data_dir, _ = dirs
    newer = official_report(200, "2026-10-11")
    api = FakeApi([artifact(2, 200, "2026-10-11T05:00:00Z")], {2: make_zip(newer)})
    run_ingest(api, dirs)
    older = official_report(100, "2026-10-01")
    older["notebooks"] = older["notebooks"][:3]
    api.artifacts.append(artifact(1, 100, "2026-10-12T05:00:00Z"))
    api.payloads[1] = make_zip(older)
    run_ingest(api, dirs)
    catalog = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["run_id"] == 200 and len(catalog["notebooks"]) == len(REPORT["notebooks"])


def test_test_data_without_official_nights(dirs):
    _, cache_dir = dirs
    api = FakeApi(
        [artifact(1, 100, "2026-10-10T05:00:00Z", branch="feature"), artifact(2, 101, "2026-10-11T05:00:00Z", branch="other")],
        {2: make_zip(REPORT)},
    )
    summary = run_ingest(api, dirs)
    assert api.downloads == [2]
    meta = json.loads((cache_dir / "test_run_meta.json").read_text(encoding="utf-8"))
    assert meta["reason"] == "no_official_data" and meta["run_id"] == REPORT["run"]["id"] and meta["artifact_id"] == 2
    assert summary["test_run"] == meta
    assert json.loads((cache_dir / "test_run.json").read_text(encoding="utf-8"))["format"] == 1
    assert json.loads((cache_dir / "test_catalog.json").read_text(encoding="utf-8"))["notebooks"]


def test_test_data_requested_and_stale_cache_removed(dirs):
    _, cache_dir = dirs
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: make_zip(official_report(100, "2026-10-10"))})
    summary = run_ingest(api, dirs, use_latest_artifact=True)
    assert summary["new"] == [100] and summary["test_run"]["reason"] == "requested" and (cache_dir / "test_run.json").exists()
    summary = run_ingest(api, dirs)
    assert summary["test_run"] is None and not (cache_dir / "test_run.json").exists() and not (cache_dir / "test_run_meta.json").exists()


def test_max_new_limits_downloads(dirs):
    api = FakeApi(
        [artifact(1, 100, "2026-10-10T05:00:00Z"), artifact(2, 101, "2026-10-11T05:00:00Z")],
        {1: make_zip(official_report(100, "2026-10-10")), 2: make_zip(official_report(101, "2026-10-11"))},
    )
    summary = run_ingest(api, dirs, max_new=1)
    assert summary["new"] == [100] and "limit" in summary["warnings"][0]


def test_main_exit_codes_and_step_summary(tmp_path, monkeypatch, capsys):
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    monkeypatch.setenv("GH_TOKEN", "t")
    api = FakeApi([artifact(1, 100, "2026-10-10T05:00:00Z")], {1: make_zip(official_report(100, "2026-10-10"))})
    monkeypatch.setattr(ingest, "GitHubApi", lambda token: api)
    args = ["--data_dir", str(tmp_path / "data"), "--cache_dir", str(tmp_path / "cache"), "--config", str(ROOT / "dashboard.json")]
    assert ingest.main(args) == 0
    assert "New nights: 100" in step_summary.read_text(encoding="utf-8")

    class Failing(FakeApi):
        def list_artifacts(self, repository, name):
            raise GitHubApiError("HTTP 403", 403)

    monkeypatch.setattr(ingest, "GitHubApi", lambda token: Failing([], {}))
    assert ingest.main(args) == 1
    assert "NIGHTLY_ARTIFACTS_TOKEN" in capsys.readouterr().err
