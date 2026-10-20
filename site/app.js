"use strict";

// OpenVINO notebooks nightly dashboard. Renders data/{meta,trend,latest,history}.json (see docs/DATA.md).
(function () {
  const STATUS = {
    P: "passed",
    F: "failed",
    T: "timeout",
    E: "error",
    S: "skipped",
    N: "not run",
    "-": "absent",
  };
  const CODE = { passed: "P", failed: "F", timeout: "T", error: "E", skipped: "S", not_run: "N" };
  const CAUSES = {
    notebook_changed: "Notebook changed",
    openvino_changed: "OpenVINO changed",
    packages_changed: "Packages changed",
    external_or_flaky: "External or flaky",
    unknown: "Unknown (no earlier pass)",
  };
  const SVG_NS = "http://www.w3.org/2000/svg";
  let configsById = {};

  // ---------------------------------------------------------------- DOM helpers
  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    return append(node, children);
  }

  function svg(tag, attrs, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value !== null && value !== undefined) node.setAttribute(key, String(value));
    }
    return append(node, children);
  }

  function safeUrl(url) {
    if (typeof url !== "string" || !url) return null;
    try {
      const parsed = new URL(url, window.location.href);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch (error) {
      return null;
    }
  }

  function link(url, text, attrs) {
    const href = safeUrl(url);
    if (!href) return text === null || text === undefined ? null : el("span", attrs, text);
    return el("a", Object.assign({ href, target: "_blank", rel: "noopener" }, attrs || {}), text);
  }

  function links(pairs) {
    const items = pairs.map(([text, url]) => (safeUrl(url) ? link(url, text) : null)).filter(Boolean);
    return items.length ? el("span", { class: "links" }, items) : el("span", { class: "muted" }, "—");
  }

  function show(id, visible) {
    const node = document.getElementById(id);
    if (node) node.hidden = !visible;
    return node;
  }

  function table(headers, rows, options) {
    const head = el(
      "tr",
      null,
      headers.map((header) => (typeof header === "string" ? el("th", { scope: "col" }, header) : el("th", Object.assign({ scope: "col" }, header.attrs), header.text)))
    );
    return el("div", { class: "table-wrap" + (options && options.tall ? " tall" : "") }, el("table", null, el("thead", null, head), el("tbody", null, rows)));
  }

  // ---------------------------------------------------------------- formatting
  function isNum(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function pct(value) {
    return isNum(value) ? (value * 100).toFixed(1) + "%" : "n/a";
  }

  function duration(seconds) {
    if (!isNum(seconds)) return "n/a";
    const total = Math.round(Math.abs(seconds));
    if (total < 60) return total + "s";
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes}m ${total % 60}s`;
    return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  }

  function date(value) {
    return typeof value === "string" && value ? value.slice(0, 10) : "n/a";
  }

  function dateTime(value) {
    const time = Date.parse(value);
    return Number.isNaN(time) ? "n/a" : new Date(time).toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }

  function relative(value) {
    const time = Date.parse(value);
    if (Number.isNaN(time)) return "n/a";
    const seconds = (Date.now() - time) / 1000;
    if (seconds < 90) return "just now";
    if (seconds < 5400) return Math.round(seconds / 60) + " min ago";
    if (seconds < 172800) return Math.round(seconds / 3600) + " h ago";
    return Math.round(seconds / 86400) + " days ago";
  }

  function plural(count, word) {
    return `${count} ${word}${count === 1 ? "" : "s"}`;
  }

  function firstLine(text, max) {
    const line = String(text || "").split("\n")[0].trim();
    return max && line.length > max ? line.slice(0, max - 1) + "…" : line;
  }

  function errorText(failure) {
    if (!failure) return "";
    if (failure.error) return String(failure.error);
    const parts = [failure.exception_type, firstLine(failure.exception_message)].filter(Boolean);
    return parts.join(": ");
  }

  function code(status) {
    if (typeof status !== "string") return "-";
    if (STATUS[status]) return status;
    return CODE[status] || "-";
  }

  function configLabel(configId) {
    const config = configsById[configId] || {};
    const match = /^(\w+)-(.+)-py([\d.]+)$/.exec(configId || "");
    const os = config.os || (match && match[2]) || configId || "?";
    const family = config.os_family || (os.startsWith("ubuntu") ? "linux" : os.startsWith("windows") ? "windows" : os.startsWith("macos") ? "macos" : "");
    const osLabel = { linux: "Linux", windows: "Windows", macos: "macOS" }[family] || os;
    const python = config.python || (match && match[3]);
    const device = config.device || (match && match[1]);
    const deviceLabel = { igpu: "iGPU", dgpu: "dGPU" }[device] || (device || "").toUpperCase();
    return `${osLabel}${python ? " py" + python : ""}${device && device !== "cpu" ? " " + deviceLabel : ""}`;
  }

  function configCell(configId) {
    return el("span", { title: configId || "" }, configLabel(configId));
  }

  // ---------------------------------------------------------------- status visuals
  function badge(status, title) {
    const c = code(status);
    const label = title || STATUS[c];
    return el("span", { class: `st st-${c === "-" ? "x" : c}`, title: label, "aria-label": label }, c === "-" ? "·" : c);
  }

  function strip(codes, nights, label) {
    const text = String(codes || "");
    const box = el("span", { class: "strip", role: "img", "aria-label": `${label || "history"}: ${text}` });
    for (let i = 0; i < text.length; i++) {
      const c = code(text[i]);
      const night = (nights && nights[i]) || {};
      const title = (night.date ? date(night.date) + ": " : "") + STATUS[c] + (night.test ? " (test run)" : "");
      const attrs = { class: `sq st-${c === "-" ? "x" : c}${night.test ? " test" : ""}`, title };
      const href = safeUrl(night.url);
      box.append(href ? el("a", Object.assign(attrs, { href, target: "_blank", rel: "noopener", tabindex: "-1" })) : el("span", attrs));
    }
    return box;
  }

  function notebookCell(path, catalog, extra) {
    const notebook = catalog[path] || {};
    const title = notebook.title && notebook.title !== path ? notebook.title : null;
    return [
      link((notebook.links || {}).source, title || path, { class: "nb-title" }),
      title ? el("span", { class: "nb-path" }, path) : null,
      extra || null,
    ];
  }

  // ---------------------------------------------------------------- sections
  function renderHeader(meta, latest) {
    const info = document.getElementById("run-info");
    info.textContent = "";
    const run = latest && latest.run;
    const source = meta.source || {};
    if (run) {
      const commit = run.commit || {};
      append(info, [
        el("span", null, "Nightly ", el("strong", null, date(run.nightly_date))),
        el("span", null, link(run.url, run.number ? `run #${run.number}` : `run ${run.id}`)),
        commit.sha
          ? el("span", null, "commit ", link(commit.url, commit.short_sha || String(commit.sha).slice(0, 7), { class: "mono" }), commit.message ? " " + firstLine(commit.message, 80) : "")
          : null,
        run.conclusion ? el("span", null, el("span", { class: `badge ${run.conclusion}` }, run.conclusion)) : null,
      ]);
    } else {
      append(info, el("span", null, "No nightly data yet"));
    }
    append(info, [
      meta.generated_at ? el("span", { title: dateTime(meta.generated_at) }, "data updated " + relative(meta.generated_at)) : null,
      el("span", null, link(source.workflow_url, "source workflow")),
    ]);
  }

  function renderBanner(meta, latest) {
    const test = meta.test_data || (latest && latest.test ? { run_id: latest.run && latest.run.id } : null);
    const banner = show("banner", Boolean(test));
    if (!test) return;
    const reason = { no_official_data: "no official nightly data yet", requested: "requested manually" }[test.reason] || "not an official nightly";
    banner.textContent = "";
    append(banner, [
      el("strong", null, "Test data: "),
      "showing ",
      link(test.url, `run ${test.run_id}`),
      test.event || test.branch ? ` (${[test.event, test.branch && "branch " + test.branch].filter(Boolean).join(", ")})` : "",
      ` — ${reason}. Official nightlies are scheduled runs of the "${(meta.source || {}).branch || "latest"}" branch.`,
    ]);
  }

  const KPIS = [
    ["pass_rate", "Pass rate", "rate", 1, "Passed ÷ executed notebook × configuration results (executed = passed + failed + timeout + error)."],
    ["notebook_pass_rate", "Notebook pass rate", "rate", 1, "Notebooks that passed on every configuration they ran on ÷ notebooks that ran on at least one configuration."],
    ["coverage", "Coverage", "rate", 1, "Executed ÷ (planned − skipped): the share of planned notebook runs that actually ran."],
    ["failing_notebooks", "Failing notebooks", "count", -1, "Notebooks with at least one failed, timed-out or errored configuration."],
    ["new_failures", "New failures", "count", -1, "Notebook × configuration results failing now that passed the previous time they ran, or that have no earlier result."],
    ["fixed", "Fixed", "count", 1, "Notebook × configuration results passing now that failed the previous time they ran."],
    ["not_run", "Not run", "count", -1, "Planned notebook runs that never executed (test job failed before the tests, was cancelled or interrupted)."],
    ["wall_clock_s", "Wall clock", "duration", -1, "Time from the start of the nightly run to the end of its last test job."],
  ];

  function formatKpi(kind, value) {
    if (kind === "rate") return pct(value);
    if (kind === "duration") return duration(value);
    return isNum(value) ? String(value) : "n/a";
  }

  function renderKpis(kpis) {
    const box = show("kpis", true);
    box.textContent = "";
    for (const [key, label, kind, direction, help] of KPIS) {
      const kpi = kpis[key] || {};
      let delta = "";
      let cls = "kpi-delta";
      if (isNum(kpi.delta)) {
        if (kpi.delta === 0) {
          delta = "= no change";
          cls += " same";
        } else {
          const magnitude = kind === "rate" ? `${Math.abs(kpi.delta * 100).toFixed(1)} pp` : kind === "duration" ? duration(kpi.delta) : String(Math.abs(kpi.delta));
          delta = `${kpi.delta > 0 ? "▲" : "▼"} ${magnitude}`;
          cls += kpi.delta * direction > 0 ? " good" : " bad";
        }
      }
      box.append(
        el(
          "div",
          { class: "kpi", title: help, tabindex: "0" },
          el("div", { class: "kpi-label" }, label),
          el("div", { class: "kpi-value" }, formatKpi(kind, kpi.value)),
          el("div", { class: cls, title: delta ? "change since the previous night" : null }, delta)
        )
      );
    }
  }

  // ---------------------------------------------------------------- charts
  const CHART = { width: 640, height: 220, left: 44, right: 12, top: 12, bottom: 26 };

  function xScale(count) {
    const span = CHART.width - CHART.left - CHART.right;
    return (index) => (count === 1 ? CHART.left + span / 2 : CHART.left + (index * span) / (count - 1));
  }

  function xLabels(root, nights, x) {
    const step = Math.max(1, Math.ceil(nights.length / 6));
    nights.forEach((night, index) => {
      if (index % step === 0 || index === nights.length - 1) {
        root.append(svg("text", { x: x(index), y: CHART.height - 8, class: "axis", "text-anchor": "middle" }, date(night.date).slice(5)));
      }
    });
  }

  function lineChart(nights) {
    const series = [
      ["pass_rate", "Pass rate", "s1"],
      ["notebook_pass_rate", "Notebook pass rate", "s2"],
      ["coverage", "Coverage", "s3"],
    ];
    const values = nights.flatMap((night) => series.map(([key]) => night[key]).filter(isNum));
    const low = values.length ? Math.max(0, Math.floor((Math.min(...values) - 0.02) * 10) / 10) : 0;
    const y = (value) => CHART.top + (1 - (value - low) / (1 - low || 1)) * (CHART.height - CHART.top - CHART.bottom);
    const x = xScale(nights.length);
    const root = svg("svg", { viewBox: `0 0 ${CHART.width} ${CHART.height}`, class: "chart", role: "img", "aria-label": "Pass rate, notebook pass rate and coverage per night" });
    for (let i = 0; i <= 4; i++) {
      const value = low + ((1 - low) * i) / 4;
      root.append(
        svg("line", { x1: CHART.left, x2: CHART.width - CHART.right, y1: y(value), y2: y(value), class: "grid" }),
        svg("text", { x: CHART.left - 6, y: y(value) + 4, class: "axis", "text-anchor": "end" }, Math.round(value * 100) + "%")
      );
    }
    for (const [key, label, cls] of series) {
      let path = "";
      let pen = "M";
      nights.forEach((night, index) => {
        if (isNum(night[key])) {
          path += `${pen}${x(index).toFixed(1)},${y(night[key]).toFixed(1)} `;
          pen = "L";
        } else {
          pen = "M";
        }
      });
      if (path) root.append(svg("path", { d: path.trim(), class: `series ${cls}` }));
      nights.forEach((night, index) => {
        if (!isNum(night[key])) return;
        const point = svg("circle", { cx: x(index), cy: y(night[key]), r: nights.length > 60 ? 2 : 3, class: `${cls}${night.test ? " test" : ""}` });
        point.append(svg("title", null, `${date(night.date)}${night.test ? " (test run)" : ""} — ${label}: ${pct(night[key])}`));
        const href = safeUrl(night.url);
        root.append(href ? svg("a", { href, target: "_blank", rel: "noopener" }, point) : point);
      });
    }
    xLabels(root, nights, x);
    const legend = el(
      "div",
      { class: "legend" },
      series.map(([, label, cls]) => el("span", null, el("span", { class: "swatch", style: `background: var(--${cls === "s1" ? "P" : cls === "s2" ? "line-2" : "line-3"})` }), label))
    );
    return el("div", { class: "chart-card" }, el("h3", null, "Pass rates and coverage"), legend, root);
  }

  function barChart(nights) {
    const kinds = [
      ["failed", "Failed", "F"],
      ["timeout", "Timeout", "T"],
      ["error", "Error", "E"],
      ["not_run", "Not run", "N"],
    ];
    const totals = nights.map((night) => kinds.reduce((sum, [key]) => sum + (isNum(night[key]) ? night[key] : 0), 0));
    const max = Math.max(1, ...totals);
    const plot = CHART.height - CHART.top - CHART.bottom;
    const y = (value) => CHART.top + plot - (value / max) * plot;
    const x = xScale(nights.length);
    const slot = (CHART.width - CHART.left - CHART.right) / Math.max(nights.length, 1);
    const width = Math.max(2, Math.min(24, slot * 0.7));
    const root = svg("svg", { viewBox: `0 0 ${CHART.width} ${CHART.height}`, class: "chart", role: "img", "aria-label": "Failed, timed-out, errored and not run results per night" });
    for (const value of [0, Math.round(max / 2), max]) {
      root.append(
        svg("line", { x1: CHART.left, x2: CHART.width - CHART.right, y1: y(value), y2: y(value), class: "grid" }),
        svg("text", { x: CHART.left - 6, y: y(value) + 4, class: "axis", "text-anchor": "end" }, value)
      );
    }
    nights.forEach((night, index) => {
      const group = svg("g", { class: night.test ? "test" : null });
      let base = 0;
      for (const [key, , cls] of kinds) {
        const value = isNum(night[key]) ? night[key] : 0;
        if (!value) continue;
        group.append(svg("rect", { x: x(index) - width / 2, y: y(base + value), width, height: y(base) - y(base + value), class: `bar-${cls}` }));
        base += value;
      }
      if (!base) group.append(svg("rect", { x: x(index) - width / 2, y: y(0) - 1, width, height: 1, class: "bar-N", opacity: 0.4 }));
      if (night.test) group.append(svg("text", { x: x(index), y: y(base) - 3, class: "test-mark", "text-anchor": "middle" }, "test"));
      const summary = kinds.map(([key, label]) => `${label.toLowerCase()} ${isNum(night[key]) ? night[key] : 0}`).join(", ");
      group.append(svg("title", null, `${date(night.date)}${night.test ? " (test run)" : ""} — ${summary}`));
      const href = safeUrl(night.url);
      root.append(href ? svg("a", { href, target: "_blank", rel: "noopener" }, group) : group);
    });
    xLabels(root, nights, x);
    const legend = el(
      "div",
      { class: "legend" },
      kinds.map(([, label, cls]) => el("span", null, el("span", { class: `swatch st-${cls}` }), label))
    );
    return el("div", { class: "chart-card" }, el("h3", null, "Failing and lost results"), legend, root);
  }

  function renderTrends(nights) {
    const box = document.getElementById("trends");
    show("sec-trends", true);
    box.textContent = "";
    if (!nights.length) {
      box.append(el("p", { class: "none" }, "No nights yet."));
      return;
    }
    box.append(lineChart(nights), barChart(nights));
  }

  // ---------------------------------------------------------------- tables
  function renderAttention(latest, catalog) {
    const box = document.getElementById("attention");
    show("sec-attention", true);
    box.textContent = "";
    const items = latest.attention || [];
    if (!items.length) {
      box.append(el("p", { class: "none" }, "Nothing is failing 🎉"));
      return;
    }
    const failures = latest.failures || {};
    const rows = items.map((item) => {
      const failure = failures[item.failure_id] || {};
      const failureLinks = failure.links || {};
      const since = item.since;
      return el(
        "tr",
        null,
        el("td", null, notebookCell(item.notebook, catalog)),
        el("td", { class: "nowrap" }, configCell(item.config_id)),
        el("td", { class: "nowrap" }, badge(item.status), item.new ? el("span", { class: "tag" }, "NEW") : null),
        el(
          "td",
          { class: "nowrap" },
          since ? link(since.url, date(since.date)) : "—",
          isNum(item.streak_nights) ? el("div", { class: "muted small" }, plural(item.streak_nights, "night")) : null
        ),
        el(
          "td",
          null,
          CAUSES[item.cause] || item.cause || "—",
          item.cause_detail ? el("div", { class: "muted small" }, item.cause_detail) : null,
          item.compare_url ? el("div", { class: "small" }, link(item.compare_url, "diff since last pass")) : null
        ),
        el(
          "td",
          { class: "err" },
          el("div", { class: "muted small" }, [item.category || failure.category, item.error_class || failure.error_class].filter(Boolean).join(" / ")),
          el("span", { title: errorText(item) || errorText(failure) }, firstLine(errorText(item) || errorText(failure), 160) || "—"),
          failure.log_excerpt ? el("details", { class: "log" }, el("summary", { class: "small" }, "failure log"), el("pre", null, failure.log_excerpt)) : null
        ),
        el(
          "td",
          { class: "nowrap" },
          links([
            failureLinks.test_start
              ? ["test start", failureLinks.test_start]
              : ["log", failureLinks.log_excerpt || failureLinks.treon_step || failureLinks.job],
            ["logs", failureLinks.logs_artifact],
            ["source", failureLinks.notebook_source],
          ])
        )
      );
    });
    box.append(table(["Notebook", "Config", "Status", "Since", "Probable cause", "Error", "Links"], rows));
  }

  function renderChanges(latest, catalog) {
    const box = document.getElementById("changes");
    show("sec-changes", true);
    box.textContent = "";
    const changes = latest.changes || {};
    const column = (title, items, render) =>
      el(
        "div",
        null,
        el("h3", null, `${title} (${items.length})`),
        items.length ? el("ul", null, items.map((item) => el("li", null, render(item)))) : el("p", { class: "none" }, "None.")
      );
    const name = (path) => ((catalog[path] || {}).title && catalog[path].title !== path ? catalog[path].title : path);
    box.append(
      column("New failures", changes.new_failures || [], (item) => [badge(item.status || "failed"), " ", link((catalog[item.notebook] || {}).links ? catalog[item.notebook].links.source : null, name(item.notebook)), el("span", { class: "muted" }, " — " + configLabel(item.config_id) + (item.previous_status === null ? " (no previous result)" : ""))]),
      column("Fixed", changes.fixed || [], (item) => [badge("passed"), " ", link((catalog[item.notebook] || {}).links ? catalog[item.notebook].links.source : null, name(item.notebook)), el("span", { class: "muted" }, " — " + configLabel(item.config_id))])
    );
  }

  function failureDetails(failure, configId) {
    const failureLinks = failure.links || {};
    const excerpt = (label, text) => (text ? el("details", null, el("summary", null, label), el("pre", null, text)) : null);
    return el(
      "div",
      { class: "failure" },
      el(
        "div",
        null,
        badge(failure.status || "failed"),
        " ",
        el("strong", null, configLabel(configId)),
        el("span", { class: "muted small" }, ` ${[failure.category, failure.error_class].filter(Boolean).join(" / ")}${isNum(failure.cell_index) ? ` · cell ${failure.cell_index}` : ""}`)
      ),
      errorText(failure) ? el("pre", null, errorText(failure)) : null,
      excerpt("Traceback", failure.traceback_excerpt),
      excerpt("Failing cell", failure.cell_source_excerpt),
      excerpt("Failure log", failure.log_excerpt),
      failure.log_excerpt ? null : excerpt("Log tail", failure.log_tail),
      el(
        "div",
        { class: "small" },
        links([
          ["job", failureLinks.job],
          ["test step", failureLinks.treon_step],
          ["test start", failureLinks.test_start],
          ["failure log", failureLinks.log_excerpt],
          ["logs artifact", failureLinks.logs_artifact],
          ["notebook source", failureLinks.notebook_source],
        ])
      )
    );
  }

  function renderHealth(latest, history, catalog) {
    const box = document.getElementById("health");
    show("sec-health", true);
    box.textContent = "";
    const configs = latest.configs || [];
    const notebooks = latest.notebooks || [];
    const historyNights = history.nights || [];
    const historyNotebooks = history.notebooks || {};
    const failures = latest.failures || {};

    const search = el("input", { type: "search", placeholder: "Search notebooks by title, path or tag…", "aria-label": "Search notebooks" });
    const filters = [
      ["all", "All"],
      ["failing", "Failing"],
      ["flaky", "Flaky"],
      ["new", "New failures"],
      ["untested", "Untested"],
    ];
    let activeFilter = "all";
    const chips = filters.map(([key, label]) =>
      el("button", { type: "button", class: "chip", "aria-pressed": key === activeFilter ? "true" : "false", "data-filter": key }, label)
    );
    const count = el("span", { class: "count", "aria-live": "polite" });
    box.append(el("div", { class: "controls" }, search, chips, count));

    const entries = [];
    const body = el("tbody");
    // Failing notebooks first, then the other tested ones, then the untested ones (alphabetical within each group).
    const rank = (notebook) => {
      const codes = Object.values(notebook.status || {}).map((s) => code(s && s.s));
      if (notebook.failing) return 0;
      return codes.some((c) => ["P", "F", "T", "E"].includes(c)) ? 1 : 2;
    };
    const ordered = notebooks
      .map((notebook, index) => [rank(notebook), index, notebook])
      .sort((a, b) => a[0] - b[0] || a[1] - b[1])
      .map((item) => item[2]);
    for (const notebook of ordered) {
      const statuses = notebook.status || {};
      const executed = Object.values(statuses).some((s) => ["P", "F", "T", "E"].includes(code(s && s.s)));
      const hist = historyNotebooks[notebook.path] || {};
      const expand = el("button", { type: "button", class: "expand", "aria-expanded": "false", "aria-label": `Details of ${notebook.path}` }, el("span", { class: "chev" }, "▸"));
      const title = notebook.title && notebook.title !== notebook.path ? notebook.title : null;
      const nameCell = el(
        "td",
        null,
        expand,
        link((notebook.links || {}).source, title || notebook.path, { class: "nb-title" }),
        notebook.new_failure ? el("span", { class: "tag" }, "NEW") : null,
        notebook.flaky ? el("span", { class: "tag flaky" }, "FLAKY") : null,
        title ? el("span", { class: "nb-path" }, notebook.path) : null
      );
      const statusCells = configs.map((config) => {
        const status = statuses[config.id];
        if (!status) return el("td", { class: "c" }, badge("-", `${config.id}: not in this night`));
        const parts = [`${config.id}: ${STATUS[code(status.s)]}`];
        if (isNum(status.d)) parts.push(duration(status.d));
        if (status.r) parts.push(status.r);
        return el("td", { class: "c" }, badge(status.s, parts.join(" · ")));
      });
      const modified = notebook.last_modified || null;
      const row = el(
        "tr",
        null,
        nameCell,
        statusCells,
        el("td", { class: "nowrap" }, hist.all ? strip(hist.all, historyNights, `${notebook.path} history`) : el("span", { class: "muted" }, "—")),
        el("td", { class: "nowrap small" }, modified ? link(modified.url, date(modified.date)) : "—")
      );
      const detail = el("tr", { class: "detail", hidden: true });
      let detailBuilt = false;
      expand.addEventListener("click", () => {
        const open = expand.getAttribute("aria-expanded") !== "true";
        expand.setAttribute("aria-expanded", String(open));
        if (open && !detailBuilt) {
          detailBuilt = true;
          const grid = el("div", { class: "detail-grid" });
          for (const [configId, codes] of Object.entries(hist.cfg || {})) {
            grid.append(el("span", { class: "small", title: configId }, configLabel(configId)), strip(codes, historyNights, `${configId} history`));
          }
          const failing = configs
            .map((config) => [config.id, statuses[config.id]])
            .filter(([, status]) => status && status.f && failures[status.f])
            .map(([configId, status]) => failureDetails(failures[status.f], configId));
          const tags = Object.entries(notebook.tags || {})
            .flatMap(([group, values]) => (values || []).map((value) => `${group}: ${value}`))
            .join(", ");
          detail.append(
            el(
              "td",
              { colspan: String(configs.length + 3) },
              Object.keys(hist.cfg || {}).length ? grid : el("p", { class: "none" }, "No history."),
              failing.length ? failing : null,
              tags ? el("div", { class: "muted small" }, "Tags: " + tags) : null,
              el("div", { class: "small" }, links([["source", (notebook.links || {}).source], ["latest", (notebook.links || {}).latest], ["history", (notebook.links || {}).history]]))
            )
          );
        }
        detail.hidden = !open || row.hidden;
      });
      body.append(row, detail);
      const tagText = Object.values(notebook.tags || {}).flat().join(" ");
      entries.push({
        row,
        detail,
        expand,
        text: `${notebook.title || ""} ${notebook.path} ${tagText}`.toLowerCase(),
        failing: Boolean(notebook.failing),
        flaky: Boolean(notebook.flaky),
        new: Boolean(notebook.new_failure),
        untested: !executed,
      });
    }

    const headers = [
      "Notebook",
      ...configs.map((config) => ({ text: configLabel(config.id), attrs: { class: "c", title: config.id } })),
      { text: `Last ${historyNights.length} nights`, attrs: { title: "Worst status across configurations per night (oldest → newest)" } },
      "Modified",
    ];
    box.append(
      el(
        "div",
        { class: "table-wrap tall" },
        el("table", null, el("thead", null, el("tr", null, headers.map((h) => (typeof h === "string" ? el("th", { scope: "col" }, h) : el("th", Object.assign({ scope: "col" }, h.attrs), h.text))))), body)
      )
    );

    function apply() {
      const query = search.value.trim().toLowerCase();
      let visible = 0;
      for (const entry of entries) {
        const match = (!query || entry.text.includes(query)) && (activeFilter === "all" || entry[activeFilter]);
        entry.row.hidden = !match;
        entry.detail.hidden = !match || entry.expand.getAttribute("aria-expanded") !== "true";
        if (match) visible++;
      }
      count.textContent = `${visible} of ${entries.length} notebooks`;
    }
    search.addEventListener("input", apply);
    for (const chip of chips) {
      chip.addEventListener("click", () => {
        activeFilter = chip.getAttribute("data-filter");
        chips.forEach((other) => other.setAttribute("aria-pressed", String(other === chip)));
        apply();
      });
    }
    apply();
  }

  function renderSlow(latest, catalog) {
    const box = document.getElementById("slow");
    show("sec-slow", true);
    box.textContent = "";
    const block = (title, items, headers, render) =>
      el("div", null, el("h3", null, title), items.length ? table(headers, items.map((item) => el("tr", null, render(item)))) : el("p", { class: "none" }, "None."));
    box.append(
      block("Flaky", latest.flaky || [], ["Notebook", "Config", { text: "Score", attrs: { class: "num" } }, "Recent executions"], (item) => [
        el("td", null, notebookCell(item.notebook, catalog)),
        el("td", { class: "nowrap" }, configCell(item.config_id)),
        el("td", { class: "num", title: `${item.transitions} pass/fail changes in ${item.executed} executed nights` }, isNum(item.score) ? item.score.toFixed(2) : "n/a"),
        el("td", null, strip(item.history, null, "recent executions")),
      ]),
      block(
        "Timeout risk",
        latest.timeout_risk || [],
        ["Notebook", "Config", { text: "Median", attrs: { class: "num" } }, { text: "Timeout", attrs: { class: "num" } }, { text: "Of timeout", attrs: { class: "num" } }],
        (item) => [
          el("td", null, notebookCell(item.notebook, catalog)),
          el("td", { class: "nowrap" }, configCell(item.config_id)),
          el("td", { class: "num" }, duration(item.median_s)),
          el("td", { class: "num" }, duration(item.timeout_s)),
          el("td", { class: "num" }, pct(item.ratio)),
        ]
      ),
      block(
        "Duration regressions",
        latest.duration_regressions || [],
        ["Notebook", "Config", { text: "Duration", attrs: { class: "num" } }, { text: "Baseline", attrs: { class: "num" } }, { text: "Ratio", attrs: { class: "num" } }],
        (item) => [
          el("td", null, notebookCell(item.notebook, catalog)),
          el("td", { class: "nowrap" }, configCell(item.config_id)),
          el("td", { class: "num" }, duration(item.duration_s)),
          el("td", { class: "num", title: "median of the previous passed runs" }, duration(item.baseline_s)),
          el("td", { class: "num" }, isNum(item.ratio) ? `×${item.ratio.toFixed(2)}` : "n/a"),
        ]
      )
    );
  }

  function renderErrors(latest) {
    const box = document.getElementById("errors");
    show("sec-errors", true);
    box.textContent = "";
    const groups = latest.error_groups || [];
    if (!groups.length) {
      box.append(el("p", { class: "none" }, "No failures in this night."));
      return;
    }
    const rows = groups.map((group) => {
      const notebooks = group.notebooks || [];
      return el(
        "tr",
        null,
        el("td", null, el("code", { title: "Same signature = same error (paths, numbers and hashes masked)" }, group.signature || "")),
        el("td", { class: "num" }, isNum(group.occurrences) ? group.occurrences : "—"),
        el("td", { class: "num" }, isNum(group.nights_seen) ? group.nights_seen : "—"),
        el("td", { class: "nowrap" }, date(group.first_seen)),
        el("td", { class: "nowrap" }, date(group.last_seen)),
        el("td", { class: "err" }, el("div", { class: "muted small" }, [group.category, group.error_class].filter(Boolean).join(" / ")), firstLine(errorText(group), 160) || "—"),
        el("td", { class: "small" }, notebooks.slice(0, 5).join(", "), notebooks.length > 5 ? ` +${notebooks.length - 5}` : "")
      );
    });
    box.append(
      table(
        [
          "Signature",
          { text: "Occurrences", attrs: { class: "num", title: "Failing notebook × configuration results in this night" } },
          { text: "Nights seen", attrs: { class: "num", title: "Nights with this signature in the history" } },
          "First seen",
          "Last seen",
          "Error",
          "Notebooks",
        ],
        rows
      )
    );
  }

  function renderInfra(latest) {
    const infra = latest.infra || {};
    const jobs = infra.jobs_failed_before_tests || [];
    const notRun = isNum(infra.not_run) ? infra.not_run : 0;
    const box = document.getElementById("infra");
    show("sec-infra", Boolean(jobs.length || notRun));
    box.textContent = "";
    if (!jobs.length && !notRun) return;
    box.append(
      el("p", { class: "muted" }, "Test jobs that stopped before running notebooks; their notebooks are counted as not run."),
      jobs.length
        ? el(
            "ul",
            { class: "infra" },
            jobs.map((job) => el("li", null, link(job.url, job.name || job.config_id || "job"), job.failed_step ? ` — failed step: ${job.failed_step}` : ""))
          )
        : null,
      el("p", null, `Notebook runs not run: ${notRun}`)
    );
  }

  function renderFooter(meta) {
    const foot = document.getElementById("footer");
    foot.textContent = "";
    const source = meta.source || {};
    const windows = meta.windows || {};
    const warnings = meta.warnings || [];
    append(foot, [
      el(
        "p",
        null,
        "Data: ",
        link(source.workflow_url, `${source.repository || "openvino_notebooks"} · ${source.workflow || "treon_nightly.yml"}`),
        source.artifact ? ` (artifact ${source.artifact}, ${source.event || "schedule"} runs of ${source.branch || "latest"})` : "",
        " · ",
        link(meta.producer_readme, "metric definitions"),
        meta.dashboard_repository ? [" · ", link(`https://github.com/${meta.dashboard_repository}`, "dashboard source")] : null
      ),
      el(
        "p",
        null,
        `Generated ${dateTime(meta.generated_at)} · ${plural(meta.official_nights || 0, "official night")}` +
          (meta.first_night ? ` (${date(meta.first_night)} – ${date(meta.last_night)})` : "") +
          ` · trends: ${windows.trend || "?"} nights · history: ${windows.matrix || "?"} nights · flakiness window: ${windows.flaky || "?"} nights`
      ),
      warnings.length ? el("details", null, el("summary", null, plural(warnings.length, "data warning")), el("ul", null, warnings.map((w) => el("li", null, w)))) : null,
    ]);
  }

  // ---------------------------------------------------------------- main
  function guarded(name, fn) {
    try {
      fn();
    } catch (error) {
      console.error(`Rendering ${name} failed`, error);
      const message = document.getElementById("message");
      message.hidden = false;
      message.classList.add("error");
      message.textContent = `Some sections could not be rendered (${name}: ${error.message}).`;
    }
  }

  function render(data) {
    const meta = data.meta || {};
    const latest = data.latest || {};
    const trend = data.trend || {};
    const history = data.history || {};
    configsById = Object.fromEntries((latest.configs || []).map((config) => [config.id, config]));
    const catalog = Object.fromEntries((latest.notebooks || []).map((notebook) => [notebook.path, notebook]));

    guarded("header", () => renderHeader(meta, latest));
    guarded("banner", () => renderBanner(meta, latest));
    guarded("footer", () => renderFooter(meta));
    if (!latest.run) {
      const message = show("message", true);
      message.textContent = "No nightly data yet. The dashboard updates daily after the nightly tests.";
      return;
    }
    guarded("key metrics", () => renderKpis(latest.kpis || {}));
    guarded("trends", () => renderTrends(trend.nights || []));
    guarded("needs attention", () => renderAttention(latest, catalog));
    guarded("changes", () => renderChanges(latest, catalog));
    guarded("notebook health", () => renderHealth(latest, history, catalog));
    guarded("flaky and slow", () => renderSlow(latest, catalog));
    guarded("error groups", () => renderErrors(latest));
    guarded("infrastructure", () => renderInfra(latest));
  }

  async function load() {
    const names = ["meta", "trend", "latest", "history"];
    const files = await Promise.all(
      names.map(async (name) => {
        const response = await fetch(`data/${name}.json`, { cache: "no-cache" });
        if (!response.ok) throw new Error(`data/${name}.json: HTTP ${response.status}`);
        return response.json();
      })
    );
    return Object.fromEntries(names.map((name, index) => [name, files[index]]));
  }

  load()
    .then(render)
    .catch((error) => {
      console.error(error);
      document.getElementById("run-info").textContent = "";
      const message = show("message", true);
      message.classList.add("error");
      message.textContent = `Could not load the dashboard data (${error.message}).`;
    });
})();
