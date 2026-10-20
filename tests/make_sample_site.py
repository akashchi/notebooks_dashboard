"""Writes sample site data built from a synthetic official history (tests/history_factory.sample_reports).

Usage: python tests/make_sample_site.py OUT_DIR [--test]

OUT_DIR receives the files of site/ (if any) and data/{meta,trend,latest,history}.json; with --test, the fixture report
(a pull_request run) is appended as requested test data.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import build  # noqa: E402
import history_factory  # noqa: E402
from compact import catalog_from_report  # noqa: E402

GENERATED_AT = "2026-10-12T08:05:00Z"


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[1])
    reports = history_factory.sample_reports()
    records = [history_factory.compact(report) for report in reports]
    catalog = catalog_from_report(reports[-1])
    test_run = test_data = None
    if "--test" in argv[2:]:
        report = history_factory.fixture_report()
        test_run = history_factory.compact(report)
        run = report["run"]
        test_data = {
            "run_id": run["id"],
            "url": run["url"],
            "event": run["event"],
            "branch": run["branch"],
            "nightly_date": run["nightly_date"],
            "reason": "requested",
        }
        catalog = catalog_from_report(report)
    config = build.load_config(ROOT / "dashboard.json")
    documents = build.compute(config, records, catalog, test_run, test_data, [], GENERATED_AT, "akashchi/notebooks_dashboard")
    build.write_site(documents, ROOT / "site", out)
    print("\n".join(build.summary_lines(documents, out)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
