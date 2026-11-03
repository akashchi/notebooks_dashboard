"use strict";

// Compares a night document (data/nights/<run_id>.json) with any earlier night document, using the rules of
// metrics.changes() in scripts/metrics.py: each result of the night is compared with the last executed status of the
// same notebook × configuration up to and including the baseline night ("last_executed", docs/DATA.md).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NightCompare = api;
})(typeof self !== "undefined" ? self : this, function () {
  const NAMES = { P: "passed", F: "failed", T: "timeout", E: "error" };
  const FAILING = new Set(["F", "T", "E"]);
  const KPI_VALUES = ["notebook_pass_rate", "failing_notebooks", "executed", "skipped", "wall_clock_s"];

  const byText = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  function lastExecutedLookup(baseline) {
    const last = (baseline && baseline.last_executed) || {};
    const configs = last.configs || [];
    const notebooks = last.notebooks || {};
    return (notebook, configId) => {
      const index = configs.indexOf(configId);
      const codes = notebooks[notebook];
      const value = index >= 0 && codes ? codes[index] : "-";
      return NAMES[value] ? value : null;
    };
  }

  function changes(night, baseline) {
    const newFailures = [];
    const fixed = [];
    const previousOf = lastExecutedLookup(baseline);
    const notebooks = (night.notebooks || []).slice().sort((a, b) => byText(a.path, b.path));
    for (const notebook of notebooks) {
      const statuses = notebook.status || {};
      for (const configId of Object.keys(statuses).sort(byText)) {
        const current = statuses[configId] || {};
        if (!NAMES[current.s]) continue;
        const previous = previousOf(notebook.path, configId);
        const failing = FAILING.has(current.s);
        if (failing && (previous === null || previous === "P")) {
          newFailures.push({
            notebook: notebook.path,
            config_id: configId,
            status: NAMES[current.s],
            failure_id: current.f === undefined ? null : current.f,
            previous_status: previous === null ? null : "passed",
          });
        } else if (current.s === "P" && previous !== null && FAILING.has(previous)) {
          fixed.push({ notebook: notebook.path, config_id: configId });
        }
      }
    }
    return { new_failures: newFailures, fixed };
  }

  function kpi(value, previous) {
    const isNum = (x) => typeof x === "number" && Number.isFinite(x);
    if (!isNum(value) || !isNum(previous)) return { value, delta: null };
    const delta = value - previous;
    return { value, delta: Number.isInteger(delta) ? delta : Math.round(delta * 1e4) / 1e4 };
  }

  // A copy of `night` whose kpis, changes, "previous" and NEW flags refer to `baseline` (an earlier night document).
  function withBaseline(night, baseline) {
    if (!night || !night.run || !baseline || !baseline.run) return night;
    const found = changes(night, baseline);
    const newKeys = new Set(found.new_failures.map((item) => `${item.notebook}\n${item.config_id}`));
    const newNotebooks = new Set(found.new_failures.map((item) => item.notebook));
    const value = (doc, name) => ((doc.kpis || {})[name] || {}).value;
    const kpis = {};
    for (const name of ["notebook_pass_rate", "failing_notebooks"]) kpis[name] = kpi(value(night, name), value(baseline, name));
    kpis.new_failures = { value: found.new_failures.length, delta: null };
    kpis.fixed = { value: found.fixed.length, delta: null };
    for (const name of KPI_VALUES.slice(2)) kpis[name] = kpi(value(night, name), value(baseline, name));
    const attention = (night.attention || [])
      .map((item) => Object.assign({}, item, { new: newKeys.has(`${item.notebook}\n${item.config_id}`) }))
      .sort((a, b) => (a.new === b.new ? 0 : a.new ? -1 : 1) || b.streak_nights - a.streak_nights || byText(a.notebook, b.notebook) || byText(a.config_id, b.config_id));
    return Object.assign({}, night, {
      previous: { date: baseline.run.nightly_date, run_id: baseline.run.id, url: baseline.run.url },
      kpis,
      changes: found,
      attention,
      notebooks: (night.notebooks || []).map((notebook) => Object.assign({}, notebook, { new_failure: newNotebooks.has(notebook.path) })),
    });
  }

  return { changes, withBaseline };
});
