(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const num = (v, digits = 1, unit = "") =>
    v == null || !Number.isFinite(Number(v))
      ? "Unavailable"
      : Number(v).toFixed(digits) + unit;
  const wh = (v) => num(v, 2, " Wh");
  const signed = (v, unit = "%") =>
    v == null ? "Insufficient data" : (v > 0 ? "+" : "") + num(v, 1, unit);
  const duration = (m) =>
    m == null
      ? "Unavailable"
      : `${Math.floor(Math.round(m) / 60)}h ${Math.round(m) % 60}m`;
  const tsLabel = (ts) =>
    ts == null ? "Unavailable" : new Date(ts * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  const dayLabel = (day) =>
    new Date(day + "T12:00:00").toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  let historyCursor = null;
  const colors = ["#9dd7bf", "#e0bd87", "#9dbdd8", "#c1aad5"];
  if (window.Chart) {
    Chart.register({
      id: "sharedCursor",
      afterDraw(chart) {
        if (tab !== "history" || historyCursor == null || !chart.scales.x) return;
        const x = chart.scales.x.getPixelForValue(historyCursor), area = chart.chartArea;
        if (x < area.left || x > area.right) return;
        const ctx = chart.ctx;
        ctx.save(); ctx.strokeStyle = "#9dd7bf"; ctx.lineWidth = 1;
        ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom); ctx.stroke(); ctx.restore();
      },
    });
    Chart.defaults.color = "#a8b6b4";
    Chart.defaults.font.family =
      '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  }
  const saved = (key, fallback) => {
    try { return JSON.parse(localStorage.getItem("batmon." + key)) ?? fallback; }
    catch (_) { return fallback; }
  };
  const save = (key, value) => {
    try { localStorage.setItem("batmon." + key, JSON.stringify(value)); return true; }
    catch (_) { return false; /* Browsing with storage disabled still supports this session. */ }
  };
  let reserve = [10, 20, 30].includes(saved("reserve", 20)) ? saved("reserve", 20) : 20;
  let goalMinutes = Number(saved("goalMinutes", 120)) || 120;
  let goalUnit = saved("goalUnit", "hours") === "minutes" ? "minutes" : "hours";
  let purpose = saved("purpose", "care") === "runtime" ? "runtime" : "care";
  let appSource = ["all", "battery", "ac"].includes(saved("appSource", "all")) ? saved("appSource", "all") : "all", experiment = saved("experiment", null), experimentReady = false, lastNow;
  const storedObject = key => {
    const value = saved(key, {});
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  };
  const journal = storedObject("journal"), acknowledged = storedObject("acknowledged");
  if (!experiment || typeof experiment !== "object" || !Number.isFinite(experiment.start)) experiment = null;
  let reportData, reportExport = null;
  const ranges = {
    health: "24h",
    history: "24h",
    apps: "24h",
    energy: "24h",
    anomalies: "24h",
    report: "7d",
  };
  const rangeOptions = {
    health: ["24h", "7d", "30d", "90d", "1y"],
    history: ["24h", "7d", "30d"],
    apps: ["1h", "8h", "24h", "7d", "30d"],
    energy: ["24h", "7d", "30d"],
    anomalies: ["24h", "7d", "all"],
    report: ["7d", "30d"],
  };
  let tab = "now",
    renderId = 0,
    timer,
    charts = [],
    includeSystem = saved("includeSystem", false) === true,
    appSearch = "",
    showAllApps = saved("showAllApps", false) === true,
    hideShort = saved("hideShort", false) === true;
  for (const key of Object.keys(ranges)) {
    const value = saved("range." + key, ranges[key]);
    if (rangeOptions[key].includes(value)) ranges[key] = value;
  }
  const dismissed = new Set();
  const attributionNote =
    "Estimated chip energy, allocated from relative Energy Impact during 5-second samples each minute, on both AC and battery. This excludes much of the rest of the Mac and cannot be compared directly with battery discharge.";
  const coverageNote =
    "Coverage is observed awake collection, not device uptime. Unobserved time includes sleep and missing telemetry. Windows end at the last completed UTC hour; labels use local time.";
  async function json(url, options) {
    const r = await fetch(url, options);
    if (!r.ok) {
      const error = new Error(`Request failed (${r.status})`);
      error.status = r.status;
      throw error;
    }
    return r.json();
  }
  function destroyCharts() {
    charts.forEach((c) => c.destroy());
    charts = [];
  }
  function paint(id, html) {
    if (id !== renderId) return false;
    const active = document.activeElement;
    const focusId = $("#content").contains(active) ? active.id : null;
    const focusData = $("#content").contains(active) && active.tagName === "BUTTON" ? {...active.dataset} : null;
    const openDetails = [...document.querySelectorAll("#content details")].map((d,i) => d.open ? i : -1);
    destroyCharts();
    $("#content").innerHTML = html;
    document.querySelectorAll("#content details").forEach((d,i) => { d.open = openDetails.includes(i); });
    if (focusId) document.getElementById(focusId)?.focus({preventScroll:true});
    else if (focusData) [...document.querySelectorAll("#content button")].find(b => Object.entries(focusData).every(([k,v]) => b.dataset[k] === v))?.focus({preventScroll:true});
    $("#content").setAttribute("aria-busy", "false");
    return true;
  }
  const heading = (title, sub) =>
    `<p class="eyebrow">Battery insights</p><h1>${title}</h1><p class="subtitle">${sub}</p>`;
  const card = (label, value, note = "") =>
    `<div class="card"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div>${note ? `<p class="note">${esc(note)}</p>` : ""}</div>`;
  const empty = (text) => `<p class="empty">${esc(text)}</p>`;
  // Filled by the status poll: which sensors this macOS version never reports.
  let sensors = {};
  const unsupported = (key) => sensors[key] === "unsupported";
  const osLabel = () => `macOS ${sensors.os_version || "version"}`;
  const sensorValue = (value, key, digits, unit) =>
    value == null && unsupported(key) ? "Not supported" : num(value, digits, unit);
  const sensorInline = (value, key, digits, unit) =>
    value == null && unsupported(key) ? `not supported on ${osLabel()}` : num(value, digits, unit);
  const sensorNote = (value, key, fallback) =>
    value == null && unsupported(key) ? `Not reported by ${osLabel()}. Other readings are unaffected.` : fallback;
  const table = (headers, rows, caption = "") =>
    `<div class="table-scroll"><table>${caption ? `<caption>${esc(caption)}</caption>` : ""}<thead><tr>${headers.map((h) => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}">No observations in this range.</td></tr>`}</tbody></table></div>`;
  const rangebar = (name) =>
    `<div class="rangebar" aria-label="Time range">${rangeOptions[name].map((r) => `<button data-range="${r}" aria-pressed="${ranges[name] === r}" class="${ranges[name] === r ? "active" : ""}">${r === "all" ? "All history" : r}</button>`).join("")}</div>`;
  const canvas = (id, label) =>
    `<div class="chart-wrap"><canvas id="${id}" role="img" aria-label="${esc(label)}">${esc(label)}</canvas></div>`;
  function chart(id, datasets, options = {}, type = "line", labels) {
    if (!window.Chart) return;
    charts.push(
      new Chart($("#" + id), {
        type,
        data: {
          labels,
          datasets: datasets.map((d, i) => ({
            borderColor: colors[i % colors.length],
            backgroundColor: colors[i % colors.length],
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
            tension: 0,
            spanGaps: false,
            ...d,
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: { labels: { color: "#a8b6b4", boxWidth: 12 } },
            tooltip: { mode: "nearest", intersect: false },
          },
          ...options,
        },
      }),
    );
  }
  function series(rows, key, gap, divisor = 1) {
    const points = [];
    let prev;
    for (const r of rows) {
      if (prev != null && r.ts - prev > gap)
        points.push({ x: prev + 1, y: null });
      points.push({ x: r.ts, y: r[key] == null ? null : r[key] / divisor });
      prev = r.ts;
    }
    return points;
  }
  function timeOptions(unit, extra = {}) {
    return {
      scales: {
        x: {
          type: "linear",
          ticks: { maxTicksLimit: 7, callback: tsLabel },
          grid: { color: "#253235" },
        },
        y: {
          title: { display: true, text: unit },
          grid: { color: "#253235" },
          ...extra,
        },
      },
      plugins: {
        legend: { labels: { color: "#a8b6b4", boxWidth: 12 } },
        tooltip: {
          callbacks: { title: (items) => tsLabel(items[0].parsed.x) },
        },
      },
    };
  }
  function processNote(name) {
    if (/windowserver/i.test(name))
      return "Display compositing for apps, windows and monitors.";
    if (/^node(?:\b|$)/i.test(name))
      return "Runtime used by development tools and other applications.";
    if (
      /daemon|^(__|kernel|launchd|airportd|mds|cloudd|bird|mDNSResponder|powerd)/i.test(
        name,
      )
    )
      return "Background system service; review related activity rather than terminating it.";
    return "";
  }
  function appRows(rows, showShare = true) {
    return rows
      .map(
        (a) =>
          `<tr><td>${esc(a.app)}<span class="process-note">${esc(processNote(a.app))}</span></td><td class="num">${wh(a.attributed_wh)}</td>${showShare ? `<td class="num"><span class="bar-track"><span class="bar" style="width:${Math.max(0, Math.min(100, a.share_pct || 0))}%"></span></span>${num(a.share_pct, 1, "%")}</td>` : ""}</tr>`,
      )
      .join("");
  }
  function recommendations(rows, actions = false) {
    if (!rows?.length)
      return empty(
        "No specific action identified from available observations. This is not a battery health assessment.",
      );
    return rows
      .map((r) => {
        const url = /^https:\/\//.test(r.source_url || "")
          ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Read the guidance</a>`
          : "";
        const target = Object.hasOwn(renderers, r.target_tab)
          ? `<button data-go="${esc(r.target_tab)}">View ${esc(r.target_tab)}</button>`
          : "";
        const key = r.title;
        const action = actions ? `<button data-journal="${esc(key)}" aria-pressed="${!!journal[key]}">${journal[key] ? "Undo completion" : "Mark complete"}</button>${journal[key] ? `<span class="muted">Completed ${tsLabel(journal[key])}</span>` : ""}` : "";
        return `<article class="recommendation"><h3>${esc(r.title)}</h3><p class="note">${esc(r.body)}</p>${r.action ? `<p>${esc(r.action)}</p>` : ""}${url}${target}${action}</article>`;
      })
      .join("");
  }
  function updateControls(d) {
    if (!$("#awake").disabled) $("#awake").checked = !!d.awake;
    const c = d.charge_limit || {};
    $("#cl-status").textContent =
      c.holding === true
        ? c.level != null ? `Charge limit ${c.level}% active` : "Charge-holding policy active"
        : c.holding === false
          ? "Charge-holding policy inactive"
          : c.source === "on_battery"
            ? "Charge limit reported only on AC"
            : "Charging policy unavailable";
    $("#cl-status").title =
      c.level != null ? `Native macOS charge limit: ${c.level}%.` : "Configured charge percentage is not available. Open Battery settings to check it.";
  }
  function warningCards(d) {
    const rows = [
      ...(d.radio_warnings || []).map((r) => ({
        ...r,
        key: `radio-${r.ts}`,
        label: "Radio observation",
        text: r.reason,
      })),
      ...(d.dark_wakes || []).map((r) => ({
        ...r,
        key: `wake-${r.ts}`,
        label: "Sleep-gap drain",
        text: `${r.reason || "Observed battery use during a sleep gap"}. ${wh(r.wh_drained)} estimated gap energy from charge-level drop and assumed battery voltage${r.duration_sec >= 60 ? ` over ${duration(r.duration_sec / 60)}` : ""}. ${(r.culprits || []).map((c) => `${c.proc}: ${c.why}`).join("; ")}`,
      })),
    ];
    return rows
      .filter((r) => !dismissed.has(r.key))
      .slice(0, 5)
      .map(
        (r) =>
          `<div class="notice"><button data-dismiss="${esc(r.key)}" aria-label="Dismiss ${esc(r.label)}">Dismiss</button><strong>${r.label}</strong><p>${esc(r.text)}</p><span class="muted">${tsLabel(r.ts)}. Associated activity is evidence, not proof of cause.</span></div>`,
      )
      .join("");
  }
  async function renderNow(id) {
    const d = await json("/api/now?reserve=" + reserve);
    if (id !== renderId) return;
    lastNow = d;
    updateControls(d);
    const s = d.sample || {},
      c = d.component || {},
      h = d.health || {},
      r = d.runtime || {};
    const componentFresh =
      c.ts_minute != null && Date.now() / 1000 - c.ts_minute <= 180;
    const componentNote =
      c.ts_minute == null
        ? "No chip reading is available."
        : `Chip reading ${tsLabel(c.ts_minute)}${componentFresh ? "" : "; older than 3 minutes, not current"}.`;
    const direction =
      s.watts == null
        ? "Battery power unavailable"
        : s.watts > 0
          ? `${num(s.watts, 1, " W")} entering the battery`
          : s.watts < 0
            ? `${num(Math.abs(s.watts), 1, " W")} leaving the battery`
            : "No net battery power";
    const states = {
      warming_up: "Collecting a continuous 5-minute battery window",
      stale: "Recent battery readings are unavailable",
      on_ac: "Runtime estimate pauses while connected to AC",
      at_reserve: `At or below the ${reserve}% reserve`,
      unavailable: "Not enough usable discharge data",
    };
    const runtime =
      r.status === "ok"
        ? `${duration(r.minutes_low)} - ${duration(r.minutes_high)}`
        : states[r.status] || "Runtime unavailable";
    paint(
      id,
      heading(
        "Right now",
        `${s.ts == null ? "Awaiting first battery reading" : s.on_ac ? (s.is_charging ? "Connected to AC, charging" : "Connected to AC") : "Running on battery"} · Last reading ${tsLabel(s.ts)}`,
      ) +
        `<section class="hero"><div class="charge-visual" role="img" aria-label="Battery ${num(s.soc_pct, 0, "%")}, reserve ${reserve}%"><div class="battery-track"><div class="battery-fill" style="height:${Math.max(0, Math.min(100, s.soc_pct || 0))}%"></div><div class="reserve-line" style="bottom:${reserve}%"></div></div><div class="charge-value"><div class="number">${num(s.soc_pct, 0, "%")}</div><p class="muted">${direction}</p></div></div><div class="runtime-panel"><div class="k">Estimated time to ${reserve}% reserve</div><div class="runtime">${runtime}</div><p class="note">${r.status === "ok" ? `Recent-load scenarios over ${num(r.window_minutes, 1)} minutes; median ${duration(r.minutes_median)}. This is not a confidence interval.` : "An estimate appears after enough uninterrupted battery use."}</p><div class="rangebar compact"><span class="k">Keep in reserve</span>${[10,20,30].map(v => `<button data-reserve="${v}" aria-pressed="${v === reserve}" class="${v === reserve ? "active" : ""}">${v}%</button>`).join("")}</div></div></section><div class="goal-strip"><label for="goal-duration">I need to keep going for</label><input id="goal-duration" type="number" min="${goalUnit === "hours" ? .25 : 15}" max="${goalUnit === "hours" ? 24 : 1440}" step="${goalUnit === "hours" ? .25 : 15}" value="${goalUnit === "hours" ? goalMinutes / 60 : goalMinutes}"><select id="goal-unit" aria-label="Duration unit"><option value="hours" ${goalUnit === "hours" ? "selected" : ""}>hours</option><option value="minutes" ${goalUnit === "minutes" ? "selected" : ""}>minutes</option></select><p id="goal-result" class="note">${goalResult(d)}</p></div>` +
        `<div class="grid">${card("Current raw capacity", num(h.max_capacity_pct, 1, "%"), "Relative to design capacity; readings fluctuate.")}${card("Battery temperature", sensorValue(s.temp_c, "battery_temp", 1, " °C"), s.temp_c == null ? sensorNote(null, "battery_temp", "This sensor is not currently reported.") : "Battery sensor, not ambient temperature.")}${card(componentFresh ? "Chip package power" : "Last chip package power", num(c.package_mw == null ? null : c.package_mw / 1000, 1, " W"), componentNote + " Chip only; excludes display and other hardware.")}${card("Display brightness", num(s.brightness_pct, 0, "%"))}${cellCard(h.cell_voltage_mv)}</div>` +
        `<p class="note">${esc(componentNote)} Chip temperature: ${sensorInline(c.soc_temp_c, "chip_temp", 1, " °C")}. SSD: ${sensorInline(c.ssd_temp_c, "ssd_temp", 1, " °C")}. Thermal pressure: ${esc(c.thermal_pressure || "unavailable")}.</p>` +
        (d.devices?.length
          ? `<p class="note">Connected devices: ${d.devices.map((x) => `${esc(x.name)} ${num(x.battery_pct, 0, "%")}`).join("; ")}.</p>`
          : "") +
        warningCards(d) +
        `<h2>App activity in the last hour</h2><p class="note">${attributionNote}</p>${table(["App or process", "Estimated chip energy"], appRows(d.top_apps || [], false))}<button data-go="apps">Explore app activity</button>`,
    );
  }
  function goalResult(d) {
    const r = d?.runtime || {};
    if (r.status !== "ok") return "A goal comparison needs a current battery runtime estimate. No guaranteed runtime.";
    if (goalMinutes <= r.minutes_low) return `Recent-load scenarios cover your ${duration(goalMinutes)} goal before the ${reserve}% reserve. Workload can change; this is not a guarantee.`;
    if (goalMinutes <= r.minutes_high) return `Your ${duration(goalMinutes)} goal is within the scenario range. Lighter use may help; runtime is not guaranteed.`;
    return `Your ${duration(goalMinutes)} goal exceeds the recent-load range. Reduce activity or plan a charge; runtime is not guaranteed.`;
  }
  function experimentMarkup() {
    const x = experiment;
    const phase = !x ? 0 : !x.split ? 1 : !x.end ? 2 : 3;
    const elapsed = x ? Math.max(0, (Date.now() / 1000 - (x.split || x.start)) / 60) : 0;
    return `<section class="experiment"><div><p class="eyebrow">A small experiment</p><h2>Does one change make a difference?</h2><p class="note">Stay on battery. Keep the same workload and display brightness in both intervals; change just one thing between them. Each interval needs at least 5 minutes. Observed differences do not prove cause.</p></div><ol class="steps">${["Baseline", "Make one change", "Observe after", "Compare"].map((v,i) => `<li class="${i <= (phase === 1 ? 0 : phase) ? "current" : ""}">${v}</li>`).join("")}</ol>${phase === 0 ? `<label class="check-line"><input id="experiment-ready" type="checkbox" ${experimentReady ? "checked" : ""}> I can keep the workload and brightness comparable</label><button id="experiment-start" ${experimentReady ? "" : "disabled"}>Start baseline</button>` : `<p class="note">Baseline started ${tsLabel(x.start)}${x.split ? `. After-change interval started ${tsLabel(x.split)}` : ""}. ${phase < 3 ? `${num(elapsed, 1)} minutes in this interval.` : "Both intervals complete."}</p>${phase === 1 ? `<button data-experiment="split" ${elapsed < 5 ? "disabled" : ""}>I made one change - start after interval</button>` : phase === 2 ? `<button data-experiment="finish" ${elapsed < 5 ? "disabled" : ""}>Finish and compare</button>` : `<div id="experiment-result" role="status">Loading comparison...</div>`}<button data-experiment="cancel">${phase === 3 ? "Start over" : "Cancel experiment"}</button>`}<p class="local-note">Boundaries are saved only in this browser. Telemetry is retained for 48 hours. Leave this tab and return at any time.</p></section>`;
  }
  async function loadExperimentResult(id) {
    if (!experiment?.end) return;
    const x = experiment;
    if (Date.now()/1000 - x.start > 48*3600) {
      if (id === renderId && $("#experiment-result")) $("#experiment-result").textContent = "This experiment is outside the 48-hour telemetry retention window. Start over to collect a new comparison.";
      return;
    }
    try {
      const d = await json(`/api/experiment?start=${x.start}&split=${x.split}&end=${x.end}`);
      if (id !== renderId || !$("#experiment-result")) return;
      $("#experiment-result").innerHTML = `<div class="grid">${card("Before", num(d.before?.average_watts, 1, " W"), `${num(d.before?.observed_h * 60, 1)} observed minutes`)}${card("After", num(d.after?.average_watts, 1, " W"), `${num(d.after?.observed_h * 60, 1)} observed minutes`)}${card("Observed power change", d.status === "comparable" ? signed(d.power_change_pct) : "Comparison withheld", "Different activity may explain any difference.")}</div><p class="note">${esc((d.reasons || []).join(" "))} Brightness: ${num(d.before?.avg_brightness, 0, "%")} before / ${num(d.after?.avg_brightness, 0, "%")} after.</p>`;
    } catch (e) {
      if (id === renderId && $("#experiment-result")) $("#experiment-result").textContent = "Comparison unavailable. Refresh this view to retry; saved boundaries are intact.";
    }
  }
  async function renderAdvisor(id) {
    const d = await json("/api/advisor");
    paint(
      id,
      heading(
        "A guide to your habits",
        "Last 30 days. A heuristic about observed charging habits, not a health score or remaining lifespan.",
      ) +
        `<div class="grid">${card("Habits score", d.score == null ? "Not ready" : `${num(d.score, 0)} / 100`, d.score == null ? "Requires at least 24 observed hours." : "Based only on available habit factors.")}${card("Observed collection", num(d.observed_h ?? d.habits?.observed_h, 1, " h"))}${card("Available factor weight", num(d.available_weight, 0, " / 100"), "Unavailable factors are omitted, not assumed healthy.")}</div>` +
        table(
          ["Habit factor", "Points", "Observed evidence"],
          (d.components || [])
            .map(
              (c) =>
                `<tr><td>${esc(c.name)}</td><td>${c.points == null ? "Unavailable" : `${num(c.points, 0)} / ${num(c.max, 0)}`}</td><td>${esc(c.why)}</td></tr>`,
            )
            .join(""),
        ) +
        `<div class="rangebar"><span class="k">My priority</span><button data-purpose="runtime" aria-pressed="${purpose === "runtime"}" class="${purpose === "runtime" ? "active" : ""}">Longer today</button><button data-purpose="care" aria-pressed="${purpose === "care"}" class="${purpose === "care" ? "active" : ""}">Battery care</button></div><h2>${purpose === "runtime" ? "Make room for the work ahead" : "Useful next steps"}</h2>${purpose === "runtime" ? `<article class="recommendation"><h3>Reduce the load, then observe</h3><p class="note">Review active apps, lower brightness if practical and check your reserve in Now. Use the experiment below to compare one change under similar conditions.</p><button data-go="now">Check runtime</button><button data-go="apps">Review app activity</button></article>` : ""}${recommendations(d.recommendations, true)}<details class="evidence-details"><summary>Completed action journal (${Object.keys(journal).length})</summary>${Object.entries(journal).sort((a,b) => b[1]-a[1]).map(([key,ts]) => `<p class="note"><strong>${esc(key)}</strong> · ${tsLabel(ts)} <button data-journal="${esc(key)}">Undo</button></p>`).join("") || empty("No completed actions yet.")}</details><p class="local-note">Action journal is saved only in this browser. Marking an action complete does not change system settings.</p>${experimentMarkup()}`,
    );
    await loadExperimentResult(id);
  }
  async function renderHistory(id) {
    const d = await json("/api/history?range=" + ranges.history);
    if (id !== renderId) return;
    const b = d.battery || [],
      c = d.components || [],
      t = d.temperature || [],
      gap = ranges.history === "24h" ? 90 : 3600;
    paint(
      id,
      heading(
        "Power over time",
        "Time is proportional. Gaps mark missing observations; lines do not interpolate across them.",
      ) +
        rangebar("history") +
        `<p class="note">Move across any chart to align all four views. <span id="history-cursor" aria-live="off">No time selected.</span></p>${b.length ? `<label class="history-scrub" for="history-time">Inspect a time <input id="history-time" type="range" aria-label="Inspect the same time on all history charts"></label>` : ""}<details class="evidence-details"><summary>Power-state changes (${(d.events || []).length})</summary>${table(["When", "Changed to"], (d.events || []).slice().reverse().map(e => `<tr><td>${tsLabel(e.ts)}</td><td>${esc({battery:"Battery",charging:"AC, charging",full:"AC, not charging"}[e.kind] || e.kind)}</td></tr>`).join(""))}<p class="note">State changes observed between adjacent segments; gaps are not interpreted as plug events. Display attachment history is not collected.</p></details>` +
        (b.length
          ? `<h2>Battery charge</h2>${canvas("charge", "Battery charge percentage over time")}<h2>Battery power</h2><p class="note">Positive power charges the battery; negative power discharges it. This is not power at the wall socket.</p>${canvas("power", "Signed battery watts over time")}<h2>Chip components</h2>${canvas("components", "CPU, GPU, ANE and chip package power in watts")}<h2>Temperature</h2><p class="note">Missing sensors remain blank. Chip temperature is not battery temperature.${unsupported("battery_temp") ? ` Battery temperature is not reported by ${osLabel()}.` : ""}</p>${canvas("temperature", "Chip, SSD and battery temperature in degrees Celsius")}`
          : empty(
              "No history in this range. Minute readings appear first; longer ranges need completed hourly summaries.",
            )),
    );
    if (!b.length) return;
    const fields =
      ranges.history === "24h"
        ? [["soc_pct", "Charge"]]
        : [
            ["soc_min", "Hourly minimum"],
            ["soc_max", "Hourly maximum"],
          ];
    chart(
      "charge",
      fields.map(([key, label]) => ({ label, data: series(b, key, gap) })),
      timeOptions("%", { min: 0, max: 100 }),
    );
    chart(
      "power",
      [{ label: "Battery W", data: series(b, "watts", gap) }],
      timeOptions("W"),
    );
    chart(
      "components",
      [
        ["cpu_mw", "CPU"],
        ["gpu_mw", "GPU"],
        ["ane_mw", "ANE"],
        ["package_mw", "Package"],
      ].map(([key, label]) => ({ label, data: series(c, key, gap, 1000) })),
      timeOptions("W"),
    );
    chart(
      "temperature",
      [
        ["soc_temp_c", "Chip"],
        ["ssd_temp_c", "SSD"],
        ["temp_c", "Battery"],
      ].map(([key, label]) => ({ label, data: series(t, key, gap) })),
      timeOptions("°C"),
    );
    const times = [...b, ...c, ...t].map(r => r.ts).filter(Number.isFinite);
    const min = Math.min(...times), max = Math.max(...times);
    const scrub = $("#history-time");
    scrub.min = min; scrub.max = max; scrub.step = 60; scrub.value = historyCursor == null ? max : Math.max(min, Math.min(max, historyCursor));
    scrub.setAttribute("aria-valuetext", tsLabel(Number(scrub.value)));
    scrub.addEventListener("input", () => {
      historyCursor = Number(scrub.value);
      $("#history-cursor").textContent = tsLabel(historyCursor);
      scrub.setAttribute("aria-valuetext", tsLabel(historyCursor));
      charts.forEach(other => other.draw());
    });
    charts.forEach(ch => {
      ch.options.scales.x.min = min; ch.options.scales.x.max = max;
      ch.options.layout = { padding: { left: 0, right: 12 } };
      ch.options.scales.y.afterFit = axis => { axis.width = 62; };
      ch.update("none");
      ch.canvas.addEventListener("mousemove", event => {
        const rect = ch.canvas.getBoundingClientRect();
        const px = (event.clientX - rect.left) * ch.width / rect.width;
        historyCursor = Math.max(min, Math.min(max, ch.scales.x.getValueForPixel(px)));
        $("#history-cursor").textContent = tsLabel(historyCursor);
        scrub.value = historyCursor; scrub.setAttribute("aria-valuetext", tsLabel(historyCursor));
        charts.forEach(other => other.draw());
      });
      ch.canvas.addEventListener("mouseleave", () => {
        historyCursor = null; charts.forEach(other => other.draw());
      });
    });
  }
  async function renderApps(id) {
    const recent = ranges.apps === "24h";
    const response = await json(recent ? "/api/workbench?hours=24" : `/api/apps?range=${ranges.apps}&include_system=${includeSystem}`);
    const d = recent ? (response.apps?.[appSource] || []) : response;

    if (
      !paint(
        id,
        heading(
          "Where chip energy goes",
          "Use this ranking to identify activity worth reviewing in the app or in Activity Monitor.",
        ) +
          rangebar("apps") +
          (recent ? `<div class="rangebar compact"><span class="k">Recent observations</span>${[["all","All sources"],["battery","Battery"],["ac","AC"]].map(([v,label]) => `<button data-source="${v}" aria-pressed="${appSource === v}" class="${appSource === v ? "active" : ""}">${label}</button>`).join("")}</div><p class="note">${esc(response.apps_note || "Source-separated estimates use retained samples from the last 24 hours; uncertain source remains in All sources.")}</p>` : `<p class="note">Historical summaries combine AC and battery. Select 24h for a verified recent power-source filter.</p>`) +
          `<div class="rangebar"><label for="app-search">Find an app</label><input id="app-search" type="search" value="${esc(appSearch)}" placeholder="App or process name">${recent ? "" : `<label><input id="system-toggle" type="checkbox" ${includeSystem ? "checked" : ""}> Include system categories</label>`}</div><p class="note">${recent && appSource !== "all" ? attributionNote.replace("on both AC and battery", appSource === "battery" ? "in estimated battery-only sample windows" : "in estimated AC sample windows") : attributionNote}</p><p class="note">Share is of estimated chip energy in the selected source and range. The 24h view includes all reported processes. Search does not change that denominator. System services are not safe targets for force-stopping.</p><div id="app-results"></div>`,
      )
    )
      return;
    const update = () => {
      const rows = d.filter((a) =>
        a.app.toLowerCase().includes(appSearch.toLowerCase()),
      );
      const visible = showAllApps ? rows : rows.slice(0, 25);
      $("#app-results").innerHTML =
        table(
          [
            "App or process",
            "Estimated chip energy",
            "Share of selected total",
          ],
          appRows(visible),
          `Showing ${visible.length} of ${rows.length} matching processes (${d.length} total).`,
        ) +
        (rows.length > 25
          ? `<button id="app-count-toggle" aria-expanded="${showAllApps}">${showAllApps ? "Show top 25" : "Show all"}</button>`
          : "");
    };
    update();
    $("#app-results").addEventListener("click", (e) => {
      if (!e.target.closest("#app-count-toggle")) return;
      showAllApps = !showAllApps; save("showAllApps", showAllApps);
      update();
    });
    $("#app-search").addEventListener("input", (e) => {
      appSearch = e.target.value;
      update();
    });
    $("#system-toggle")?.addEventListener("change", (e) => {
      includeSystem = e.target.checked; save("includeSystem", includeSystem);
      switchTab("apps");
    });
  }
  function periodCards(p) {
    return `<div class="grid">${card("Battery energy out", wh(p.wh_out))}${card("Battery energy in", wh(p.wh_in))}${card("Observed on battery", num(p.on_battery_h, 1, " h"))}${card("Observed on AC", num(p.on_ac_h, 1, " h"))}${card("Observed coverage", num(p.coverage_pct, 1, "%"), `${num(p.observed_h, 1)} hours collected`)}</div>`;
  }
  function dailyChart(rows) {
    chart(
      "daily",
      [
        {
          label: "Battery out (Wh)",
          data: rows.map((r) => (r.observed_h > 0 ? r.wh_out : null)),
        },
        {
          label: "Battery in (Wh)",
          data: rows.map((r) => (r.observed_h > 0 ? r.wh_in : null)),
        },
      ],
      {
        scales: {
          y: { beginAtZero: true, title: { display: true, text: "Wh" } },
        },
      },
      "bar",
      rows.map((r) => dayLabel(r.day)),
    );
  }
  async function renderEnergy(id) {
    const [d, recent] = await Promise.all([json("/api/insights?range=" + ranges.energy), json("/api/workbench?hours=24")]);
    const p = d.current, m = recent.matched || {};
    if (
      !paint(
        id,
        heading(
          "Energy through the battery",
          "Accumulated battery energy, not total Mac consumption or electricity from the wall.",
        ) +
          rangebar("energy") +
          periodCards(p) +
          `<p class="note">${coverageNote} ${esc(p.power_method || "")}</p><section class="evidence-panel"><p class="eyebrow">Recent paired observations · 24 hours</p><h2>Two views of power</h2><div class="grid">${card("Battery discharge", num(m.battery_watts, 1, " W"), "Observed at the battery.")}${card("Chip package estimate", num(m.chip_watts, 1, " W"), "Sampled chip activity in corresponding windows.")}${card("Paired battery window", num(m.observed_h, 2, " h"), "Conservatively matched battery-only collection.")}</div><p class="note">${esc(m.note || "Battery and chip sensors use different sampling cadences. These estimates are not an energy balance.")} Display, fans, radios and connected devices are not separately metered; subtraction cannot identify their power.</p></section>` +
          (p.has_data
            ? `<h2>Daily observed energy</h2>${canvas("daily", "Daily battery charge and discharge energy in watt-hours")}<p class="note">Blank days have no observations. Low bars may reflect less collection, not lower power.</p>`
            : empty("No completed hourly observations in this range.")),
      )
    )
      return;
    if (p.has_data) dailyChart(p.daily);
  }
  const volts = (mv) => num(mv == null ? null : mv / 1000, 3, " V");
  const amps = (ma) => num(ma == null ? null : Math.abs(ma) / 1000, 2, " A");
  function cellCard(cells) {
    if (!cells?.length) return card("Cell balance", sensorValue(null, "cells"), sensorNote(null, "cells", "Cell voltages are not reported yet."));
    const spread = Math.max(...cells) - Math.min(...cells);
    return card("Cell balance", num(spread, 0, " mV spread"), `Cells ${cells.map(volts).join(" / ")}. Instant reading; the spread widens under load and while charging.`);
  }
  function batteryRecord(h) {
    const c = h.cells || {}, l = h.lifetime;
    const cells = c.voltages_mv?.length
      ? c.voltages_mv.map((mv, i) => card(`Cell ${i + 1}`, volts(mv))).join("") +
        card("Cell spread", num(c.spread_mv, 0, " mV"), "Highest minus lowest cell right now.")
      : card("Cell voltages", sensorValue(null, "cells"), sensorNote(null, "cells", "Not reported yet."));
    const years = l?.operating_time_hours == null ? null : l.operating_time_hours / 24 / 365.25;
    const life = l
      ? [
          card("Temperature range", `${num(l.lifetime_temp_min, 0, " °C")} to ${num(l.lifetime_temp_max, 0, " °C")}`, `Average ${num(l.lifetime_temp_avg, 1, " °C")}.`),
          card("Gauge operating time", num(l.operating_time_hours, 0, " h"), years == null ? "Counts every hour, including sleep." : `About ${num(years, 1)} years; counts every hour, including sleep.`),
          card("Highest currents", `${amps(l.lifetime_max_charge_ma)} in / ${amps(l.lifetime_max_discharge_ma)} out`, "Peak charge and discharge current."),
          card("Pack voltage range", `${volts(l.lifetime_pack_min_mv)} to ${volts(l.lifetime_pack_max_mv)}`),
          card("Cell voltage range", `${volts(l.lifetime_cell_min_mv)} to ${volts(l.lifetime_cell_max_mv)}`, "Lowest and highest single-cell voltage."),
        ].join("")
      : card("Lifetime record", sensorValue(null, "lifetime"), sensorNote(null, "lifetime", "Not reported yet."));
    return `<h2>Battery cells</h2><div class="grid">${cells}</div><p class="note">Instant cell voltages from the battery controller (SMC). A single reading is not a diagnosis.</p><h2>Lifetime record</h2><div class="grid">${life}</div><p class="note">Extremes reported by the battery gauge, including time before batmon. They are not dated events and may change after gauge reset or battery service.</p>`;
  }
  function healthCards(h) {
    const checked =
      h.macos_checked_ts == null
        ? "macOS assessment check time unavailable."
        : `macOS assessment checked ${tsLabel(h.macos_checked_ts)}.`;
    const macos = [
      card(
        "macOS maximum capacity",
        num(h.macos_capacity_pct, 0, "%"),
        "Maximum-capacity assessment reported by macOS.",
      ),
      card(
        "macOS battery condition",
        h.macos_condition || "Unavailable",
        "Condition reported by macOS Battery Health.",
      ),
    ].join("");
    const observed = [
      card(
        "Current raw sensor capacity",
        num(h.current_raw_pct, 1, "%"),
        "Raw reported capacity relative to design.",
      ),
      card(
        "7-day raw sensor median",
        num(h.median_7d_pct, 1, "%"),
        "Smooths daily raw measurement fluctuation.",
      ),
      card(
        "Raw change vs previous week",
        signed(h.change_pp, " pp"),
        "Percentage points; an increase can be recalibration.",
      ),
      card(
        "Cycle count",
        num(h.cycle_count, 0),
        "Not a replacement countdown.",
      ),
    ].join("");
    return `<div class="grid">${macos}</div>
      <p class="note">${esc(checked)} Raw sensor capacity and its historical medians are separate measurements, not Apple's maximum-capacity diagnostic. They may differ from the macOS assessment because of calibration and measurement methods.</p>
      <div class="grid">${observed}</div>`;
  }
  function cellHistoryMarkup(d) {
    const rows = d.points || [], valid = rows.filter(r => r.spread_avg_mv != null);
    const last = valid.at(-1);
    return `<section class="evidence-panel"><h2>Cell history</h2>${rangebar("health")}
      <p class="note">${esc(d.note || "History starts after the updated collector records its first samples. Earlier cell readings cannot be reconstructed.")}</p>
      <div class="grid">${card("Latest bucket mean", num(last?.spread_avg_mv, 1, " mV"), last ? `Bucket starts ${tsLabel(last.ts)}. Highest minus lowest cell in each sample.` : "Waiting for validated cell readings.")}${card("Cell readings", num(d.cell_samples, 0), `Of ${num(d.sample_count, 0)} recorded samples in this view.`)}${card("Low-load readings", num(d.low_load_samples, 0), "Subset with known charge, temperature and current.")}${card("Internal resistance", "Not verified", d.resistance?.note || "SMC BR00..BR14 and B0R1..B0R3 are withheld until their units and meaning are cross-checked on this Mac.")}</div>
      ${d.status === "awaiting_collector" ? `<div class="notice">The collector needs the updated version before it can store cell history.</div>` : ""}
      ${valid.length ? canvas("cell-spread", "Cell voltage spread: mean, sampled maximum and low-load subset in millivolts") : empty("No cell history in this range. Missing readings are not zero imbalance.")}
      <p class="note">${esc(d.filter_note || "Low-load observations are a comparison subset, not proof of rest or a battery fault test.")} Even this subset can vary with recent load and balancing. Follow persistent changes under similar conditions; a single peak does not diagnose a bad cell.</p>
      ${rows.some(r => r.cell1_mv != null) ? `<h3>Individual cell voltages</h3>${canvas("cell-volts", "Average voltage of each reported cell")}` : ""}
      ${rows.some(r => r.c_rate_avg != null) ? `<h3>Load and charge context</h3>${canvas("cell-load", "Design-normalized current in C and state of charge in percent")}<p class="note">Design C-rate = absolute battery current / design capacity. 1 C means a current numerically equal to the rated amp-hour capacity, not a runtime prediction or a safety limit. Charge and discharge are combined; SOC uses the right axis.</p>` : ""}
      ${rows.some(r => r.temp_avg_c != null) ? `<h3>Battery sensor temperature</h3>${canvas("cell-temp", "Mean and sampled maximum battery temperature")}<p class="note">Internal battery temperature is not ambient temperature. Apple's 10-35 °C MacBook operating guidance concerns the surrounding air, so it is not used as a sensor fault threshold.</p>` : ""}
      <details class="evidence-details"><summary>How to interpret these measurements</summary><p class="note">Voltage differences depend on state of charge, current, temperature and balancing. Cell voltages are retained only when all reported cells are plausible and their sum is within 5% of pack voltage; this rejects incomplete reads and is not a health tolerance. The 24h view groups samples into 5-minute buckets; longer views use hourly or daily summaries. Means are weighted by valid sample count. Peaks describe recorded samples, not continuous monitoring.</p><p class="note">Resistance requires a verified controller mapping or a controlled measurement with known timing, load and state of charge. Voltage divided by current is not internal resistance. TI manuals explain the principles, but do not identify the controller or SMC mapping in this Mac.</p><p class="note"><a href="https://www.ti.com/lit/an/slua450/slua450.pdf" target="_blank" rel="noopener noreferrer">TI fuel-gauge algorithm</a> · <a href="https://www.ti.com/lit/an/slua433/slua433.pdf" target="_blank" rel="noopener noreferrer">TI cell imbalance guidance</a> · <a href="https://www.apple.com/batteries/maximizing-performance/" target="_blank" rel="noopener noreferrer">Apple temperature and battery care</a></p></details></section>`;
  }
  function cellHistoryCharts(d) {
    const rows = d.points || [], gap = d.resolution_sec || 300;
    const draw = (id, specs, unit, opts) => chart(id, specs.map(([label,key]) => ({label,data:series(rows,key,gap),pointRadius:2})), opts || timeOptions(unit));
    if (rows.some(r => r.spread_avg_mv != null)) draw("cell-spread", [["Mean spread","spread_avg_mv"],["Sampled maximum","spread_max_mv"],["Low-load mean","low_load_avg_mv"]], "mV", timeOptions("mV", {beginAtZero:true}));
    if (rows.some(r => r.cell1_mv != null)) {
      const specs = [1,2,3,4].filter(i => rows.some(r => r[`cell${i}_mv`] != null)).map(i => [`Cell ${i}`,`cell${i}_mv`]);
      draw("cell-volts", specs, "mV");
    }
    if (rows.some(r => r.c_rate_avg != null)) {
      const options = timeOptions("Design C-rate", {beginAtZero:true});
      options.scales.soc = {position:"right",min:0,max:100,title:{display:true,text:"Charge %"},grid:{drawOnChartArea:false}};
      chart("cell-load", [{label:"Mean |current| / design",data:series(rows,"c_rate_avg",gap),pointRadius:2},{label:"Sampled maximum C-rate",data:series(rows,"c_rate_max",gap),pointRadius:2},{label:"Mean charge %",data:series(rows,"soc_avg_pct",gap),yAxisID:"soc",borderDash:[4,4],pointRadius:2}], options);
    }
    if (rows.some(r => r.temp_avg_c != null)) draw("cell-temp", [["Mean battery temperature","temp_avg_c"],["Sampled maximum","temp_max_c"]], "°C");
  }
  async function renderHealth(id) {
    const [d, advice, cells] = await Promise.all([
      json("/api/insights?range=7d"),
      json("/api/advisor"),
      json("/api/health/cells?range=" + ranges.health),
    ]);
    const h = d.health;
    if (
      !paint(
        id,
        heading(
          "Battery condition",
          "Start with the macOS assessment, then explore the separately measured raw capacity trend.",
        ) +
          healthCards(h) +
          `<p class="note">${num(h.points, 0)} daily observations spanning ${num(h.span_days, 0)} days. Each 7-day median needs at least four observations.</p>` +
          batteryRecord(h) +
          cellHistoryMarkup(cells) +
          (h.weekly?.length
            ? `<h2>Weekly raw sensor median</h2>${canvas("health", "Weekly median raw battery capacity relative to design")}`
            : empty(
                "Weekly capacity medians will appear as daily snapshots accumulate.",
              )) +
          `<div class="notice"><strong>Long-term forecast: ${h.forecast_status === "ok" ? "trend passes the current quality gate" : "withheld"}</strong><p>${esc(h.forecast_reason || "Not enough stable observations to extrapolate reliably.")}</p><span class="muted">A withheld prediction does not erase an observed decline. Capacity estimates also move with calibration.</span></div><p class="note">For this MacBook Pro generation, Apple lists a 1,000-cycle design reference. Cycle count alone does not determine service need. Check macOS Battery Health for its assessment. <a href="https://support.apple.com/en-ie/102888" target="_blank" rel="noopener noreferrer">Apple cycle guidance</a> · <a href="https://support.apple.com/en-in/108376" target="_blank" rel="noopener noreferrer">Battery service guidance</a></p>${healthDiagnostics(h.diagnostics)}<h2>Actions independent of a forecast</h2>${recommendations(advice.recommendations)}`,
      )
    )
      return;
    cellHistoryCharts(cells);
    if (h.weekly?.length)
      chart(
        "health",
        [
          {
            label: "Weekly raw sensor median %",
            data: series(
              h.weekly.map((r) => ({
                ts: Date.parse(r.day + "T12:00:00") / 1000,
                capacity_pct: r.capacity_pct,
              })),
              "capacity_pct",
              8 * 86400,
            ),
          },
        ],
        timeOptions("% of design"),
      );
  }
  function healthDiagnostics(d = {}) {
    const bt = d.backtest || {};
    return `<details class="evidence-details"><summary>Capacity changes and forecast validation</summary><p class="note">${esc(d.note || "Capacity shifts are observations, not a diagnosis of recalibration or replacement.")}</p>${table(["Observed shift", "Raw change", "Context"], (d.shifts || []).map(s => `<tr><td>${esc(s.day)}</td><td>${signed(s.change_pp, " pp")}</td><td>${esc(s.reason)}</td></tr>`).join(""))}<div class="grid">${card("Out-of-sample validation", bt.mae_pp != null ? num(bt.mae_pp, 2, " pp mean error") : "Withheld", bt.note || "A stable observed trend is required.")}${card("Constant-median baseline", num(bt.baseline_mae_pp, 2, " pp mean error"), bt.status === "not_improved" ? "Trend did not improve on this simpler baseline." : "Compare held-out error, not years-ahead accuracy.")}${card("Validation windows", num(bt.windows, 0))}</div>${table(["Cycle observation window", "Cycles added", "Snapshots"], (d.cycle_windows || []).map(w => `<tr><td>${num(w.days,0)} days</td><td>${num(w.cycles_added,0)}</td><td>${num(w.observations,0)}</td></tr>`).join(""))}</details>`;
  }
  const chargerWidgets = window.BatmonChargers.create({esc,num,wh,duration,tsLabel,card,table,canvas,chart,timeOptions,saved,save});
  let chargerDays = 30;
  async function renderCharging(id) {
    const [d, h, recent, chargerData] = await Promise.all([
      json("/api/charging"),
      json("/api/habits"),
      json("/api/workbench?hours=24"),
      json(`/api/chargers?days=${chargerDays}`).catch(error => {
        if (error.status !== 404) throw error;
        return {available:false,service_update_required:true,current:null,sessions:[],patterns:[],days:chargerDays};
      }),
    ]);
    if (id !== renderId) return;
    const ex = recent.exposure || {};
    const a = d.aggregates || {};
    const sessions = d.sessions.filter(
      (s) => !hideShort || s.ended == null || s.ended - s.started >= 300,
    );
    const rows = sessions
      .slice(0, 100)
      .map(
        (s) =>
          `<tr><td>${esc({ battery: "Battery", charging: "AC, charging", full: "AC, holding" }[s.kind] || s.kind)}</td><td>${tsLabel(s.started)}</td><td>${s.ended == null ? "Open segment" : duration((s.ended - s.started) / 60)}</td><td>${num(s.soc_start, 0, "%")} to ${num(s.soc_end, 0, "%")}</td><td class="num">${wh(s.wh)}</td></tr>`,
      )
      .join("");
    if (
      !paint(
        id,
        heading(
          "Charging sources and sessions",
          "Understand your connected source, compare setups and find recurring charging behavior.",
        ) + chargerWidgets.markup(chargerData) + `<details id="charging-legacy" class="evidence-details"><summary>Battery habits and older power-state segments</summary>` +
          `<div class="grid">${card("Observed battery segments", num(a.battery_sec / 3600, 1, " h"))}${card("Observed AC segments", num(a.ac_sec / 3600, 1, " h"))}${card("Estimated high-charge exposure", num(h.full_pct_of_ac, 1, "% of AC time"), "Noncharging AC segments with both endpoints at least 95%.")}${card("Low-charge episodes", num(h.deep_discharges, 0), "Below 10%; counted again only after recovery to 20%.")}${card("Cycles added", num(h.cycles_30d, 0))}${card("Average charging power", num(a.avg_charge_watts, 1, " W"))}</div><p class="note">Endpoint-based exposure is an estimate, not exact time at full charge. Overnight charging alone is not treated as a problem. Missing observations limit episode counts.</p><section class="evidence-panel"><p class="eyebrow">Observed charge exposure · Last 24 hours</p><h2>How long at higher charge?</h2><div class="grid">${card("At least 80%", num(ex.above_80_h, 2, " h"))}${card("At least 90%", num(ex.above_90_h, 2, " h"))}${card("At least 95%", num(ex.above_95_h, 2, " h"))}${card("Charging temperature", sensorValue(ex.avg_charging_temp_c, "battery_temp", 1, " °C"), sensorNote(ex.avg_charging_temp_c, "battery_temp", `${num(ex.charging_temp_observed_h, 2)} charging hours with a sensor reading`))}</div><p class="note">${num(ex.soc_observed_h, 2)} hours with observed charge level; ${num(ex.temp_observed_h, 2)} hours with a battery temperature reading. A short observed interval counts only when both endpoint readings meet the threshold; this is a conservative exposure estimate, not exact crossing time. Thresholds overlap and are not additive. Gaps are excluded, not assumed cool or low-charge. These recent sensor observations are separate from the 30-day endpoint estimate above.</p></section><h2>Recent segments</h2><label><input id="short-toggle" type="checkbox" ${hideShort ? "checked" : ""}> Hide closed segments shorter than 5 minutes</label><p class="note">Sleep and collector restarts can split a segment. These rows are not physical battery cycles. Showing up to 100 of ${sessions.length} matching segments.</p>${table(["Power state", "Start", "Duration", "Charge", "Battery energy"], rows)}</details>`,
      )
    )
      return;
    if (!chargerData.available) $("#charging-legacy").open = true;
    await chargerWidgets.bind(chargerData, {json,isCurrent:()=>id===renderId,refresh:()=>refresh(++renderId),setDays:n=>chargerDays=n});
    if (id !== renderId) return;
    $("#short-toggle").addEventListener("change", (e) => {
      hideShort = e.target.checked; save("hideShort", hideShort);
      switchTab("charging");
    });
  }
  function anomalyLabel(a) {
    const map = {
      __SYSTEM_THERMAL__: ["Thermal pressure", num(a.wh_today, 0, " min")],
      __SYSTEM_SLEEP_DRAIN__: [
        "Sleep-gap battery drop",
        num(a.wh_today, 1, "%"),
      ],
      __SYSTEM_RAPID_DISCHARGE__: [
        "High battery discharge",
        num(a.wh_today, 1, " W"),
      ],
      __SYSTEM_WEAK_CHARGER__: [
        "Battery drains while on AC",
        num(a.wh_today, 1, " W"),
      ],
      __SYSTEM_FULL_PLUGGED__: [
        "Historical high-charge event",
        num(a.wh_today, 1, " h"),
      ],
      __SYSTEM_HOT_CHARGE__: [
        "Elevated charging temperature",
        num(a.wh_today, 1, " °C"),
      ],
    };
    return (
      map[a.app] || [
        a.app,
        `${wh(a.wh_today)} vs ${wh(a.wh_baseline)} baseline (${num(a.ratio, 1)}x)`,
      ]
    );
  }
  function anomalyGroups(d) {
    const since = ranges.anomalies === "all" ? 0 : Date.now() / 1000 - (ranges.anomalies === "7d" ? 604800 : 86400);
    const grouped = new Map();
    d.filter(a => a.ts >= since).forEach(a => {
      if (!grouped.has(a.app)) grouped.set(a.app, []);
      grouped.get(a.app).push(a);
    });
    return [...grouped.entries()].map(([key, items]) => {
      items.sort((a,b) => b.ts - a.ts);
      return {key, items, latest: items[0].ts, first: items[items.length - 1].ts};
    }).sort((a,b) => b.latest - a.latest);
  }
  function updateAnomalyBadge(groups) {
    const open = groups.filter(g => !(acknowledged[g.key] >= g.latest)).length;
    $("#anombadge").textContent = open ? `(${open})` : "";
  }
  async function renderAnomalies(id) {
    const d = await json("/api/anomalies?since=0");
    if (id !== renderId) return;
    const groups = anomalyGroups(d);
    updateAnomalyBadge(groups);
    const rows = groups.map(g => {
      const a = g.items[0], [label,value] = anomalyLabel(a), ack = acknowledged[g.key] >= g.latest;
      return `<tr class="${ack ? "acknowledged" : ""}"><td><strong>${esc(label)}</strong><span class="process-note">${esc(processNote(a.app))}</span><span class="incident-state">${ack ? "Acknowledged" : "Needs review"}</span></td><td class="num">${g.items.length}</td><td>${tsLabel(g.first)}<span class="process-note">Latest ${tsLabel(g.latest)}</span></td><td>${esc(value)}</td><td><button data-ack="${esc(g.key)}" data-latest="${g.latest}" aria-pressed="${ack}">${ack ? "Undo" : "Acknowledge"}</button><details><summary>Evidence</summary>${g.items.map(item => `<p class="note">${tsLabel(item.ts)}: ${esc(anomalyLabel(item)[1])}${item.detail?.culprits?.length ? `. Associated activity: ${item.detail.culprits.map(c => esc(c.app)).join(", ")}` : ""}</p>`).join("")}<p class="note">Review the workload and power source around this time. System services may reflect other activity.</p></details></td></tr>`;
    }).join("");
    paint(id, heading("Events worth reviewing", "Grouped by app or detector type. Observations and correlations are not a diagnosis.") + rangebar("anomalies") + `<p class="local-note">Acknowledgements stay in this browser. A newer event reopens the group. Historical records retain the rules used when recorded.</p>` + table(["Event group", "Count", "First / latest", "Latest observation", "Review"],rows));
  }
  async function renderReport(id) {
    const d = await json("/api/report?range=" + ranges.report),
      a = d.analysis,
      p = a.current,
      prev = a.previous,
      comp = a.comparison;
    if (id !== renderId) return;
    reportData = d;
    const period = ranges.report === "30d" ? "30-day" : "7-day";
    const summary =
      comp.status === "ok" && comp.power_change_pct != null
        ? `Observed battery-only power averaged ${num(p.battery_only_watts, 1, " W")} in this window versus ${num(prev.battery_only_watts, 1, " W")} previously (${signed(comp.power_change_pct)}); workload and collection coverage can differ between windows.`
        : "There are not enough comparable battery-only hours to assess the power change between these windows.";
    const compareRows = [
      [
        "Battery energy out",
        wh(p.wh_out),
        wh(prev.wh_out),
        signed(comp.energy_change_pct),
      ],
      [
        "Observed collection",
        num(p.observed_h, 1, " h"),
        num(prev.observed_h, 1, " h"),
        signed(comp.observed_hours_change_pct),
      ],
      [
        "Observed battery-only average power",
        num(p.battery_only_watts, 1, " W"),
        num(prev.battery_only_watts, 1, " W"),
        signed(comp.power_change_pct),
      ],
    ];
    if (
      !paint(
        id,
        heading(
          `Your ${period} battery report`,
          `${tsLabel(p.start_ts)} to ${tsLabel(p.end_ts)} · Compared with the preceding equal ${period} window.`,
        ) +
          rangebar("report") + `<div class="report-actions"><button class="print-button" id="print-report">Print / Save as PDF</button><button data-export="json">Export JSON</button><button data-export="csv">Export daily CSV</button></div><p id="print-status" class="note" role="status"></p><div id="report-export-panel">${exportMarkup()}</div>` +
          periodCards(p) +
          `<p class="note">${coverageNote}</p><h2>What changed</h2><p>${esc(summary)}</p>${table(["Measure", "Current window", "Previous window", "Change"], compareRows.map((r) => `<tr>${r.map((v, i) => `<${i ? "td" : "th"}${i ? ' class="num"' : ' scope="row"'}>${esc(v)}</${i ? "td" : "th"}>`).join("")}</tr>`).join(""))}<p class="note">Previous coverage: ${num(prev.coverage_pct, 1, "%")}. Energy totals reflect both load and observed duration, not efficiency. Battery-only power excludes mixed AC/battery hours and needs at least 2 hours of observed collection in each window (${num(p.pure_battery_h, 1)} h this window; ${num(prev.pure_battery_h, 1)} h previously). Different workloads can explain the change. ${esc(p.power_method || "Power is averaged over collected battery time; within-hour power-sensor coverage is not known.")}</p>` +
          (p.has_data
            ? `<h2>Daily observed energy</h2>${canvas("daily", "Daily battery charge and discharge for this report window")}`
            : empty("No completed hourly observations for this window.")) +
          `<h2>Capacity context</h2>${healthCards(a.health)}<p class="note">${esc(a.health.forecast_reason || "Long-term capacity predictions require a reliable trend.")}</p><h2>Next steps</h2>${recommendations((d.recommendations || []).slice(0, 3))}<h2>Most active apps</h2><p class="note">${attributionNote} App ranking covers the same completed-hour window as this report. Recommendations use the most recent 30-day habits, independent of the report window.</p>${table(["App or process", "Estimated chip energy"], appRows(d.top_apps || [], false))}`,
      )
    )
      return;
    $("#print-report").addEventListener("click", () => {
      try { window.print(); }
      catch (_) { /* The fallback below also covers restricted embedded browsers. */ }
      $("#print-status").textContent = "Print dialog requested. If your browser does not open it, open this page in Safari or Chrome and use Print.";
    });
    if (p.has_data) dailyChart(p.daily);
  }
  const renderers = {
    now: renderNow,
    advisor: renderAdvisor,
    history: renderHistory,
    apps: renderApps,
    energy: renderEnergy,
    health: renderHealth,
    charging: renderCharging,
    anomalies: renderAnomalies,
    report: renderReport,
  };
  async function refresh(id) {
    try {
      await renderers[tab](id);
    } catch (e) {
      if (id !== renderId) return;
      paint(
        id,
        heading(
          "This view is unavailable",
          "The local service may be starting, or observations may not be available yet.",
        ) +
          `<p class="note">${esc(e.message)}. Your saved data is unchanged.</p><button data-retry>Try again</button>`,
      );
    }
  }
  function isEditingControl(element) {
    return !!element && (element.tagName === "TEXTAREA" || element.tagName === "SELECT" || (element.tagName === "INPUT" && !["range", "checkbox", "radio", "button", "submit"].includes(element.type)));
  }
  function switchTab(next) {
    if (!Object.hasOwn(renderers, next)) return;
    clearReportExport();
    reportData = null;
    tab = next;
    const id = ++renderId;
    clearInterval(timer);
    destroyCharts();
    document.querySelectorAll("#tabs button").forEach((b) => {
      const active = b.dataset.tab === tab;
      b.classList.toggle("active", active);
      if (active) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    $("#content").innerHTML =
      '<p class="empty" role="status">Loading observations...</p>';
    $("#content").setAttribute("aria-busy", "true");
    refresh(id);
    timer = setInterval(
      () => {
        if (document.hidden || ($("#content").contains(document.activeElement) && isEditingControl(document.activeElement))) return;
        refresh(++renderId);
      },
      tab === "now" ? 5000 : tab === "charging" ? 15000 : 60000,
    );
  }
  async function status() {
    try {
      const s = await json("/api/status"),
        age =
          s.last_sample_ts == null
            ? null
            : Math.max(0, s.now_ts - s.last_sample_ts),
        ok = age != null && age <= 180;
      $("#livetext").textContent =
        age == null
          ? "Awaiting first reading"
          : `${ok ? "Updated" : "Last reading"} ${duration(age / 60)} ago`;
      $("#live").classList.toggle("down", !ok);
      $("#stale").hidden = ok;
      sensors = s.sensors || {};
      json("/api/anomalies?since=0").then(d => updateAnomalyBadge(anomalyGroups(d))).catch(() => {});
    } catch (_) {
      $("#livetext").textContent = "Local service unavailable";
      $("#live").classList.add("down");
      $("#stale").hidden = false;
    }
  }
  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) switchTab(b.dataset.tab);
  });
  $("#content").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.range) {
      ranges[tab] = b.dataset.range;
      save("range." + tab, ranges[tab]);
      switchTab(tab);
    } else if (b.dataset.reserve) {
      reserve = Number(b.dataset.reserve); save("reserve", reserve); refresh(++renderId);
    } else if (b.dataset.purpose) {
      purpose = b.dataset.purpose; save("purpose", purpose); refresh(++renderId);
    } else if (b.dataset.source) {
      appSource = b.dataset.source; save("appSource", appSource); refresh(++renderId);
    } else if (b.dataset.journal) {
      const key = b.dataset.journal;
      if (journal[key]) delete journal[key]; else journal[key] = Math.floor(Date.now()/1000);
      save("journal", journal); refresh(++renderId);
    } else if (b.dataset.ack) {
      const key = b.dataset.ack;
      if (acknowledged[key] >= Number(b.dataset.latest)) delete acknowledged[key];
      else acknowledged[key] = Number(b.dataset.latest);
      save("acknowledged", acknowledged); refresh(++renderId);
    } else if (b.id === "experiment-start") {
      if (!$("#experiment-ready")?.checked) return;
      experiment = {start: Math.floor(Date.now()/1000)};
      save("experiment", experiment); refresh(++renderId);
    } else if (b.dataset.experiment) {
      const action = b.dataset.experiment, now = Math.floor(Date.now()/1000);
      if (action === "finish" && experiment?.split && now - experiment.split >= 300) return finishExperiment(now);
      if (action === "cancel") { experiment = null; experimentReady = false; }
      else if (action === "split" && experiment && now - experiment.start >= 300) experiment.split = now;
      save("experiment", experiment); refresh(++renderId);
    } else if (b.dataset.export) exportReport(b.dataset.export);
    else if (b.id === "copy-export") copyExport();
    else if (b.dataset.go) switchTab(b.dataset.go);
    else if (b.hasAttribute("data-retry")) switchTab(tab);
    else if (b.dataset.dismiss) {
      dismissed.add(b.dataset.dismiss);
      refresh(++renderId);
    }
  });
  $("#content").addEventListener("input", e => {
    if (e.target.id === "goal-duration") {
      const value = Number(e.target.value) * (goalUnit === "hours" ? 60 : 1);
      if (!Number.isFinite(value) || value < 15 || value > 1440) {
        $("#goal-result").textContent = "Choose a duration between 15 minutes and 24 hours.";
        return;
      }
      goalMinutes = value; save("goalMinutes", goalMinutes);
      $("#goal-result").textContent = goalResult(lastNow);
    }
  });
  $("#content").addEventListener("change", e => {
    if (e.target.id === "goal-unit") {
      goalUnit = e.target.value; save("goalUnit", goalUnit);
      const input = $("#goal-duration");
      input.value = goalUnit === "hours" ? goalMinutes/60 : goalMinutes;
      input.min = input.step = goalUnit === "hours" ? .25 : 15;
      input.max = goalUnit === "hours" ? 24 : 1440;
    }
    if (e.target.id === "experiment-ready") {
      experimentReady = e.target.checked;
      $("#experiment-start").disabled = !experimentReady;
    }
  });
  async function finishExperiment(now) {
    // End at the last stored reading. A click-time end has no covering sample
    // yet, and a source change right after finishing would never cover it.
    try {
      const st = await json("/api/status");
      const end = Math.min(now, st.last_sample_ts ?? 0);
      if (!experiment?.split || end - experiment.split < 300) {
        $("#control-status").textContent = "Waiting for a newer battery reading. Try Finish again shortly.";
        return;
      }
      experiment.end = end;
      save("experiment", experiment); refresh(++renderId);
    } catch (_) {
      $("#control-status").textContent = "Could not read the latest battery reading. Try Finish again.";
    }
  }
  function updateExperimentClock() {
    if (tab !== "advisor" || !experiment || experiment.end) return;
    const elapsed = (Date.now()/1000 - (experiment.split || experiment.start))/60;
    const button = $(experiment.split ? '[data-experiment="finish"]' : '[data-experiment="split"]');
    if (button) button.disabled = elapsed < 5;
  }
  setInterval(updateExperimentClock, 5000);
  function clearReportExport() {
    if (reportExport) URL.revokeObjectURL(reportExport.url);
    reportExport = null;
  }
  function exportMarkup() {
    if (!reportExport) return "";
    const x = reportExport;
    return `<details class="export-preview evidence-details" open><summary>Export ready: ${esc(x.filename)}</summary><p class="note">Snapshot of ${tsLabel(x.start)} to ${tsLabel(x.end)}. Use the download link or copy the data below. This preview remains unchanged until you export again or change view.</p><div class="report-actions"><a id="report-download" href="${esc(x.url)}" download="${esc(x.filename)}">Download ${esc(x.format.toUpperCase())}</a><button id="copy-export">Copy data</button></div><label for="export-data">${esc(x.format.toUpperCase())} data</label><textarea id="export-data" readonly spellcheck="false" rows="9">${esc(x.data)}</textarea><p id="export-status" class="note" role="status">The data is ready. Your browser controls whether the file is downloaded.</p></details>`;
  }
  function exportReport(format) {
    if (!reportData || !["json", "csv"].includes(format)) return;
    const data = format === "json" ? JSON.stringify(reportData, null, 2) :
      "day,observed_hours,battery_energy_out_wh,battery_energy_in_wh\n" + (reportData.analysis.current.daily || []).map(r => [r.day,r.observed_h,r.observed_h > 0 ? r.wh_out : null,r.observed_h > 0 ? r.wh_in : null].map(v => v == null ? "" : String(v)).join(",")).join("\n");
    clearReportExport();
    reportExport = {
      data, format,
      filename: `batmon-${ranges.report}-${new Date().toISOString().slice(0,10)}.${format}`,
      start: reportData.analysis.current.start_ts, end: reportData.analysis.current.end_ts,
      url: URL.createObjectURL(new Blob([data], {type: format === "json" ? "application/json" : "text/csv;charset=utf-8"})),
    };
    $("#report-export-panel").innerHTML = exportMarkup();
    $("#report-download").click();
  }
  async function copyExport() {
    const snapshot = reportExport;
    if (!snapshot) return;
    try {
      await navigator.clipboard.writeText(snapshot.data);
      if (reportExport === snapshot && $("#export-status")) $("#export-status").textContent = "Data copied to the clipboard.";
    } catch (_) {
      if (reportExport !== snapshot || !$("#export-data")) return;
      $("#export-data").focus(); $("#export-data").select();
      $("#export-status").textContent = "Clipboard access is unavailable. The data is selected; press Command+C on Mac or Control+C to copy it.";
    }
  }
  $("#awake").addEventListener("change", async (e) => {
    const input = e.target,
      previous = !input.checked;
    input.disabled = true;
    try {
      const d = await json("/api/awake", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: input.checked }),
      });
      input.checked = d.awake;
      $("#control-status").textContent = d.awake
        ? "Keep awake enabled."
        : "Normal idle sleep restored.";
    } catch (_) {
      input.checked = previous;
      $("#control-status").textContent =
        "Could not change keep awake. Try again.";
    } finally {
      input.disabled = false;
    }
  });
  $("#cl-settings").addEventListener("click", async () => {
    try {
      await json("/api/open_battery_settings", {
        method: "POST",
        headers: { "X-Batmon-Client": "1" },
      });
      $("#control-status").textContent = "Battery settings opened.";
    } catch (_) {
      $("#control-status").textContent =
        "Could not open settings. Open System Settings > Battery.";
    }
  });
  switchTab("now");
  status();
  setInterval(status, 30000);
})();
