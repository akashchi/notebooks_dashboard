"""Builds a local history from chosen runs of any branch/event, for previewing the dashboard with several nights.

Downloads the nightly-dashboard-data artifact of each given run and stores it as a compact run record in
<data_dir>/runs/, like ingest.py does for official nights, but without the branch/event checks. The default data
directory is inside .cache/ and is never committed. Preview it with:

    GH_TOKEN=$(gh auth token) python scripts/local_history.py 38426658429 38530761413
    python scripts/build.py --data_dir .cache/local --cache_dir .cache/local --out _site
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from compact import catalog_from_report, compact_report, schema_major
from github_api import GitHubApi, GitHubApiError
from ingest import CATALOG_FILE, RUNS_DIR, InvalidArtifact, read_report, utc_now, write_json


def store_runs(api, config: dict, run_ids: list, data_dir: Path, log=print) -> int:
    """Stores the newest artifact of every run; returns the number of runs that could not be stored."""
    source = config["source"]
    artifacts = [artifact for artifact in api.list_artifacts(source["repository"], source["artifact"]) if not artifact.get("expired")]
    newest: Optional[tuple] = None
    failed = 0
    for run_id in run_ids:
        matching = [artifact for artifact in artifacts if (artifact.get("workflow_run") or {}).get("id") == run_id]
        if not matching:
            log(f"WARNING: run {run_id} has no non-expired '{source['artifact']}' artifact")
            failed += 1
            continue
        artifact = max(matching, key=lambda item: (item.get("created_at") or "", item.get("id") or 0))
        try:
            report = read_report(api.download_artifact_zip(source["repository"], artifact["id"]))
        except (GitHubApiError, InvalidArtifact) as error:
            log(f"WARNING: artifact {artifact.get('id')} of run {run_id} is unusable: {error}")
            failed += 1
            continue
        run = report.get("run") or {}
        if schema_major(report) != 1 or not run.get("nightly_date"):
            log(f"WARNING: run {run_id} has an unsupported report (schema_version {report.get('schema_version')!r})")
            failed += 1
            continue
        record = compact_report(report, {"id": artifact.get("id"), "created_at": artifact.get("created_at")}, utc_now())
        write_json(Path(data_dir) / RUNS_DIR / f"{run['nightly_date']}_{run_id}.json", record)
        log(f"Stored run {run_id} ({run['nightly_date']}, {run.get('event')}, branch {run.get('branch')})")
        key = (run["nightly_date"], run_id)
        if newest is None or key > newest[0]:
            newest = (key, report)
    if newest:
        write_json(Path(data_dir) / CATALOG_FILE, catalog_from_report(newest[1]))
    return failed


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_ids", type=int, nargs="+", help="workflow run ids of openvino_notebooks")
    parser.add_argument("--config", type=Path, default=Path("dashboard.json"))
    parser.add_argument("--data_dir", type=Path, default=Path(".cache/local"), help="output history (default: .cache/local)")
    args = parser.parse_args(argv)

    if args.data_dir.resolve() == Path("data").resolve():
        print("ERROR: data/ holds the official history; use another --data_dir", file=sys.stderr)
        return 2
    config = json.loads(args.config.read_text(encoding="utf-8"))
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    try:
        failed = store_runs(GitHubApi(token), config, args.run_ids, args.data_dir)
    except GitHubApiError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
