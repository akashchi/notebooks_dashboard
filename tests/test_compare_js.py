"""Cross-checks site/compare.js (the page's night comparison) against metrics.changes() and the built night documents."""

import json
import shutil
import subprocess
from pathlib import Path

import build
import metrics
import pytest
from compact import catalog_from_report
from history_factory import compact, sample_reports

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
SCRIPT = """
const fs = require("fs");
const compare = require(process.argv[2]);
const nights = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const ids = Object.keys(nights);
const pairs = {};
const previous = {};
ids.forEach((night, a) => {
  ids.slice(0, a).forEach((base) => (pairs[`${night}:${base}`] = compare.changes(nights[night], nights[base])));
  if (a > 0) previous[night] = compare.withBaseline(nights[night], nights[ids[a - 1]]);
});
process.stdout.write(JSON.stringify({ pairs, previous }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_compare_js_matches_the_python_metrics(tmp_path):
    reports = sample_reports()
    records = [compact(report) for report in reports]
    documents = build.compute(build.load_config(ROOT / "dashboard.json"), records, catalog_from_report(reports[-1]))
    nights = documents["nights"]
    (tmp_path / "nights.json").write_text(json.dumps(nights), encoding="utf-8")
    (tmp_path / "check.js").write_text(SCRIPT, encoding="utf-8")
    output = subprocess.run(
        [NODE, str(tmp_path / "check.js"), str(ROOT / "site" / "compare.js"), str(tmp_path / "nights.json")], capture_output=True, text=True, check=True
    ).stdout
    result = json.loads(output)

    history = metrics.History(records)
    ids = list(nights)
    assert ids == [str(ref["run_id"]) for ref in history.refs]
    compared = 0
    for a, night in enumerate(ids):
        for b, base in enumerate(ids[:a]):
            assert result["pairs"][f"{night}:{base}"] == metrics.changes(history, a, b), (night, base)
            compared += 1
    assert compared == len(ids) * (len(ids) - 1) // 2
    assert any(result["pairs"][f"{ids[-1]}:{base}"]["fixed"] for base in ids[:-1])

    # Compared with the night before it, a night document stays as built.
    for night, shown in result["previous"].items():
        expected = nights[night]
        for key in ("previous", "kpis", "changes", "attention", "notebooks"):
            assert shown[key] == expected[key], (night, key)
