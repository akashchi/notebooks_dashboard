import json

import build
import local_history
from test_ingest import CONFIG, FakeApi, artifact, make_zip, official_report


def test_runs_of_any_branch_are_stored_and_built(tmp_path):
    first = official_report(1, "2026-10-13", event="pull_request", branch="feature")
    second = official_report(2, "2026-10-14", event="pull_request", branch="feature")
    api = FakeApi(
        [artifact(10, 1, "2026-10-13T10:00:00Z", branch="feature"), artifact(20, 2, "2026-10-14T10:00:00Z", branch="feature"), artifact(21, 2, "2026-10-14T11:00:00Z", branch="feature")],
        {10: make_zip(first), 21: make_zip(second)},
    )
    data_dir = tmp_path / "local"
    failed = local_history.store_runs(api, CONFIG, [1, 2, 3], data_dir, log=lambda message: None)

    assert failed == 1  # run 3 has no artifact
    assert api.downloads == [10, 21]  # newest artifact of run 2
    assert sorted(path.name for path in (data_dir / "runs").iterdir()) == ["2026-10-13_1.json", "2026-10-14_2.json"]
    assert (data_dir / "catalog.json").is_file()
    documents = build.compute(CONFIG, build.load_runs(data_dir, []), build.load_catalog(data_dir / "catalog.json", []))
    assert [night["run_id"] for night in documents["trend"]["nights"]] == [1, 2]


def test_official_data_dir_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dashboard.json").write_text(json.dumps(CONFIG))
    assert local_history.main(["1", "--data_dir", "data"]) == 2
