"""Downloads new nightly reports from openvino_notebooks and stores them as compact run records in data/.

Official nights (scheduled runs of the configured workflow on the configured branch) are stored in data/runs/ and
indexed in data/ingested.json; data/catalog.json is refreshed from the newest night. While no official night exists,
or when --use_latest_artifact is given, the most recent artifact of any branch/event is saved to the cache directory as
test data (never committed). A single bad artifact never stops the ingestion; see docs/DATA.md for the formats.
"""

import argparse
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from compact import catalog_from_report, compact_report, schema_major
from github_api import GitHubApi, GitHubApiError

REPORT_FILE = "nightly-report.json"
INDEX_FILE = "ingested.json"
CATALOG_FILE = "catalog.json"
RUNS_DIR = "runs"
TEST_RUN_FILE = "test_run.json"
TEST_CATALOG_FILE = "test_catalog.json"
TEST_META_FILE = "test_run_meta.json"
AUTH_HINT = (
    "The token cannot download artifacts of the source repository. Store a fine-grained personal access token with read-only "
    "access to public repositories as the repository secret NIGHTLY_ARTIFACTS_TOKEN."
)


class InvalidArtifact(Exception):
    """The artifact content is unusable (missing or invalid report); it is not downloaded again."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) if pretty else json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def read_report(zip_bytes: bytes) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as error:
        # A truncated download looks the same as a corrupt archive: retried on the next ingestion.
        raise GitHubApiError(f"invalid zip archive: {error}") from error
    names = [name for name in archive.namelist() if name.rsplit("/", 1)[-1] == REPORT_FILE]
    if not names:
        raise InvalidArtifact(f"{REPORT_FILE} is missing in the artifact")
    try:
        report = json.loads(archive.read(min(names, key=len)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidArtifact(f"{REPORT_FILE} is not valid JSON: {error}") from error
    if not isinstance(report, dict):
        raise InvalidArtifact(f"{REPORT_FILE} is not a JSON object")
    return report


def validate(report: dict, artifact: dict, source: dict) -> Optional[str]:
    """Reason why the report is not an official night, or None."""
    if schema_major(report) != 1:
        return f"unsupported schema_version {report.get('schema_version')!r}"
    run = report.get("run") or {}
    expected = {
        "id": (artifact.get("workflow_run") or {}).get("id"),
        "event": source["event"],
        "workflow": source["workflow_name"],
        "branch": source["branch"],
    }
    for key, value in expected.items():
        if run.get(key) != value:
            return f"run.{key} is {run.get(key)!r}, expected {value!r}"
    if not run.get("nightly_date"):
        return "run.nightly_date is missing"
    return None


def is_new(artifact: dict, entry: Optional[dict]) -> bool:
    """Whether the artifact may hold a newer report than the stored (or rejected) one of its run."""
    if not entry:
        return True
    if artifact.get("id") == entry.get("artifact_id") or artifact.get("id") in entry.get("rejected_artifact_ids", []):
        return False
    stored = entry.get("artifact_created_at")
    return not stored or (artifact.get("created_at") or "") > stored


class Ingestion:
    def __init__(self, api: GitHubApi, config: dict, data_dir: Path, cache_dir: Path, now: Optional[str] = None, log: Callable[[str], None] = print) -> None:
        self.api = api
        self.source = config["source"]
        self.data_dir = Path(data_dir)
        self.cache_dir = Path(cache_dir)
        self.now = now or utc_now()
        self.log = log
        self.summary: dict = {"new": [], "updated": [], "skipped": [], "rejected": [], "warnings": [], "test_run": None}

    def warn(self, message: str) -> None:
        self.log(f"WARNING: {message}")
        self.summary["warnings"].append(message)

    def download_report(self, artifact: dict) -> dict:
        return read_report(self.api.download_artifact_zip(self.source["repository"], artifact["id"]))

    def reject(self, runs: dict, artifact: dict, reason: str) -> None:
        run_id = str((artifact.get("workflow_run") or {}).get("id"))
        self.warn(f"Artifact {artifact.get('id')} of run {run_id} rejected: {reason}")
        self.summary["rejected"].append(int(run_id))
        entry = runs.setdefault(run_id, {})
        entry.setdefault("rejected_artifact_ids", []).append(artifact.get("id"))
        if not entry.get("file"):
            entry.update({"rejected": reason, "artifact_created_at": artifact.get("created_at")})

    def store(self, runs: dict, artifact: dict, report: dict) -> Optional[tuple]:
        run = report["run"]
        run_id = str(run["id"])
        entry = runs.get(run_id) or {}
        attempt = int(run.get("attempt") or 1)
        if entry.get("file") and int(entry.get("attempt") or 0) > attempt:
            self.reject(runs, artifact, f"attempt {attempt} is older than the stored attempt {entry['attempt']}")
            return None
        relative = f"{RUNS_DIR}/{run['nightly_date']}_{run_id}.json"
        record = compact_report(report, {"id": artifact.get("id"), "created_at": artifact.get("created_at")}, self.now)
        write_json(self.data_dir / relative, record)
        if entry.get("file") and entry["file"] != relative:
            (self.data_dir / entry["file"]).unlink(missing_ok=True)
        self.summary["updated" if entry.get("file") else "new"].append(int(run_id))
        runs[run_id] = {
            "attempt": attempt,
            "artifact_id": artifact.get("id"),
            "artifact_created_at": artifact.get("created_at"),
            "file": relative,
            "nightly_date": run["nightly_date"],
            **({"rejected_artifact_ids": entry["rejected_artifact_ids"]} if entry.get("rejected_artifact_ids") else {}),
        }
        self.log(f"Stored run {run_id} ({run['nightly_date']}, attempt {attempt}) from artifact {artifact.get('id')}")
        return (run["nightly_date"], int(run_id))

    def update_catalog(self, key: tuple, report: dict) -> None:
        path = self.data_dir / CATALOG_FILE
        current = load_json(path, {}) or {}
        current_key = (current.get("nightly_date") or "", int(current.get("run_id") or 0))
        if key >= current_key:
            write_json(path, catalog_from_report(report), pretty=False)
            self.log(f"Catalog refreshed from run {key[1]}")

    def ingest_official(self, artifacts: list, max_new: Optional[int]) -> None:
        index_path = self.data_dir / INDEX_FILE
        index = load_json(index_path, {"runs": {}})
        runs = index.setdefault("runs", {})
        newest: Optional[tuple] = None
        downloads = 0
        for artifact in artifacts:
            run_id = (artifact.get("workflow_run") or {}).get("id")
            if (artifact.get("workflow_run") or {}).get("head_branch") != self.source["branch"] or run_id is None:
                continue
            if not is_new(artifact, runs.get(str(run_id))):
                self.summary["skipped"].append(int(run_id))
                continue
            if max_new is not None and downloads >= max_new:
                self.warn(f"Download limit of {max_new} reached; remaining artifacts are ingested next time.")
                break
            downloads += 1
            try:
                report = self.download_report(artifact)
            except GitHubApiError as error:
                if error.status in (401, 403):
                    raise
                self.warn(f"Artifact {artifact.get('id')} of run {run_id} could not be downloaded (retried next time): {error}")
                continue
            except InvalidArtifact as error:
                self.reject(runs, artifact, str(error))
                continue
            reason = validate(report, artifact, self.source)
            if reason:
                self.reject(runs, artifact, reason)
                continue
            key = self.store(runs, artifact, report)
            if key and (newest is None or key > newest[0]):
                newest = (key, report)
        write_json(index_path, index, pretty=True)
        if newest:
            self.update_catalog(*newest)

    def has_official_nights(self) -> bool:
        runs = load_json(self.data_dir / INDEX_FILE, {"runs": {}}).get("runs", {})
        return any(entry.get("file") and (self.data_dir / entry["file"]).exists() for entry in runs.values())

    def ingest_test_run(self, artifacts: list, reason: str) -> None:
        if not artifacts:
            self.warn("No artifact is available for test data.")
            return
        artifact = artifacts[-1]
        try:
            report = self.download_report(artifact)
        except GitHubApiError as error:
            if error.status in (401, 403):
                raise
            self.warn(f"Test artifact {artifact.get('id')} could not be downloaded: {error}")
            return
        except InvalidArtifact as error:
            self.warn(f"Test artifact {artifact.get('id')} is unusable: {error}")
            return
        if schema_major(report) != 1:
            self.warn(f"Test artifact {artifact.get('id')} has unsupported schema_version {report.get('schema_version')!r}")
            return
        run = report.get("run") or {}
        write_json(self.cache_dir / TEST_RUN_FILE, compact_report(report, {"id": artifact.get("id"), "created_at": artifact.get("created_at")}, self.now))
        write_json(self.cache_dir / TEST_CATALOG_FILE, catalog_from_report(report))
        meta = {
            "run_id": run.get("id"),
            "url": run.get("url"),
            "event": run.get("event"),
            "branch": run.get("branch"),
            "nightly_date": run.get("nightly_date"),
            "reason": reason,
            "artifact_id": artifact.get("id"),
        }
        write_json(self.cache_dir / TEST_META_FILE, meta, pretty=True)
        self.summary["test_run"] = meta
        self.log(f"Test data: run {run.get('id')} ({run.get('event')}, branch {run.get('branch')}), reason: {reason}")

    def clear_test_cache(self) -> None:
        for name in (TEST_RUN_FILE, TEST_CATALOG_FILE, TEST_META_FILE):
            (self.cache_dir / name).unlink(missing_ok=True)

    def run(self, use_latest_artifact: bool = False, max_new: Optional[int] = None) -> dict:
        artifacts = [artifact for artifact in self.api.list_artifacts(self.source["repository"], self.source["artifact"]) if not artifact.get("expired")]
        artifacts.sort(key=lambda artifact: (artifact.get("created_at") or "", artifact.get("id") or 0))
        self.log(f"{len(artifacts)} non-expired '{self.source['artifact']}' artifact(s) in {self.source['repository']}")
        self.ingest_official(artifacts, max_new)
        self.clear_test_cache()
        if use_latest_artifact:
            self.ingest_test_run(artifacts, "requested")
        elif not self.has_official_nights():
            self.ingest_test_run(artifacts, "no_official_data")
        return self.summary


def ingest(api, config: dict, data_dir: Path, cache_dir: Path, use_latest_artifact: bool = False, max_new: Optional[int] = None, now=None, log=print) -> dict:
    return Ingestion(api, config, data_dir, cache_dir, now=now, log=log).run(use_latest_artifact, max_new)


def write_step_summary(summary: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["### Nightly data ingestion", ""]
    lines.append(f"- New nights: {', '.join(map(str, summary['new'])) or 'none'}")
    lines.append(f"- Updated nights: {', '.join(map(str, summary['updated'])) or 'none'}")
    lines.append(f"- Already ingested: {len(summary['skipped'])}")
    if summary["rejected"]:
        lines.append(f"- Rejected artifacts of runs: {', '.join(map(str, summary['rejected']))}")
    test = summary.get("test_run")
    if test:
        lines.append(f"- Test data: run {test['run_id']} ({test['event']}, branch {test['branch']}), reason: {test['reason']}")
    lines += [f"- Warning: {warning}" for warning in summary["warnings"]]
    with open(path, "a", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path("dashboard.json"))
    parser.add_argument("--data_dir", type=Path, default=Path("data"))
    parser.add_argument("--cache_dir", type=Path, default=Path(".cache"))
    parser.add_argument("--use_latest_artifact", action="store_true", help="Also save the most recent artifact of any branch/event as test data.")
    parser.add_argument("--max_new", type=int, default=None, help="Maximum number of artifacts to download in this execution.")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("WARNING: neither GH_TOKEN nor GITHUB_TOKEN is set; artifact downloads require a token.")
    try:
        summary = ingest(GitHubApi(token), config, args.data_dir, args.cache_dir, args.use_latest_artifact, args.max_new)
    except GitHubApiError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if error.status in (401, 403):
            print(f"ERROR: {AUTH_HINT}", file=sys.stderr)
        return 1
    write_step_summary(summary)
    print(
        f"Ingestion finished: {len(summary['new'])} new, {len(summary['updated'])} updated, {len(summary['skipped'])} already ingested, "
        f"{len(summary['rejected'])} rejected, {len(summary['warnings'])} warning(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
