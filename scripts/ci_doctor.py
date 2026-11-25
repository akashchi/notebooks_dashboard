"""Refresh open CI Doctor issues/PRs and match their explicit references to nightly failures."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from github_api import GitHubApi, GitHubApiError
from ingest import utc_now, write_json

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_FILE = "ci_doctor.json"
ASSIGNEE = "akashchi"
WORKFLOW_MARKER = "<!-- gh-aw-workflow-id: nightly-issue-remediator -->"


def parse_item(item: dict, repository: str) -> dict:
    body = item.get("body") or ""
    signature = re.search(r"^Error signature:\s*`?([0-9a-f]{12,64})\b", body, re.MULTILINE | re.IGNORECASE)
    nightly = re.search(r"^Nightly run:[^\n]*", body, re.MULTILINE | re.IGNORECASE)
    run = re.search(rf"https://github\.com/{re.escape(repository)}/actions/runs/(\d+)", nightly[0]) if nightly else None
    section = re.search(r"^## Affected notebooks\s*\n(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    affected = []
    for line in section[1].splitlines() if section else []:
        cells = line.split("|")
        if len(cells) < 4:
            continue
        notebook = re.search(r"`(?:notebooks/)?([^`]+\.ipynb)`", cells[1])
        if notebook:
            affected.append({"notebook": notebook[1], "configs": re.findall(r"`([^`]+)`", cells[2])})
    return {
        "number": item["number"],
        "title": item["title"],
        "url": item["html_url"],
        "kind": "pr" if "pull_request" in item else "issue",
        "signature": signature[1].lower() if signature else None,
        "run_id": run[1] if run else None,
        "affected": affected,
    }


def refresh(api: GitHubApi, repository: str, path: Path, now: Optional[str] = None) -> dict:
    prs = api.search_issues(f"repo:{repository} is:pr is:open label:automated-fix")
    issues = api.search_issues(f"repo:{repository} is:issue is:open assignee:{ASSIGNEE}")
    issues = [
        item for item in issues
        if WORKFLOW_MARKER in (item.get("body") or "")
        or {"nightly-remediator", "nightly-failure"} & {label["name"] for label in item.get("labels") or []}
    ]
    items = sorted((parse_item(item, repository) for item in [*prs, *issues]), key=lambda item: item["number"], reverse=True)
    warnings = [
        f"CI Doctor {item['kind']} #{item['number']} has no explicit nightly run or error signature; it cannot be linked."
        for item in items if not item["run_id"] and not item["signature"]
    ]
    snapshot = {"repository": repository, "updated_at": now or utc_now(), "items": items, "warnings": warnings}
    write_json(path, snapshot)
    return snapshot


def match_night(night: dict, snapshot: Optional[dict]) -> dict:
    """Match signatures within declared notebook/config scopes; never match by notebook name alone."""
    snapshot = snapshot or {}
    failures = night.get("failures") or {}
    linked = {failure_id: [] for failure_id in failures}
    matches = []
    for item in snapshot.get("items") or []:
        same_run = item["run_id"] is not None and str(item["run_id"]) == str((night.get("run") or {}).get("id"))
        matched_ids = []
        for failure_id, failure in failures.items():
            scopes = item["affected"]
            in_scope = any(
                scope["notebook"] == failure.get("notebook")
                and (not scope["configs"] or failure.get("config_id") in scope["configs"])
                for scope in scopes
            )
            signature_match = bool(item["signature"]) and item["signature"] == failure.get("signature")
            if (signature_match and (not scopes or in_scope)) or (not item["signature"] and same_run and in_scope):
                matched_ids.append(failure_id)
                linked[failure_id].append({key: item[key] for key in ("number", "title", "url", "kind")})
        if same_run or matched_ids:
            matches.append({**item, "failure_ids": matched_ids})
    return {
        **night,
        "failures": {failure_id: {**failure, "ci_doctor": linked[failure_id]} for failure_id, failure in failures.items()},
        "ci_doctor": {
            "repository": snapshot.get("repository"),
            "updated_at": snapshot.get("updated_at"),
            "open_total": len(snapshot.get("items") or []),
            "items": matches,
            "warnings": snapshot.get("warnings") or [],
        },
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "dashboard.json")
    parser.add_argument("--data_dir", type=Path, default=ROOT / "data")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    try:
        snapshot = refresh(
            GitHubApi(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")),
            config["source"]["repository"],
            args.data_dir / SNAPSHOT_FILE,
        )
    except (GitHubApiError, OSError, ValueError) as error:
        print(f"ERROR: CI Doctor refresh failed: {error}", file=sys.stderr)
        return 1
    for warning in snapshot["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"CI Doctor: refreshed {len(snapshot['items'])} open issue(s)/PR(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
