(() => {
"use strict";
const $ = (s) => document.querySelector(s);
let currentTab = "now";
let pollTimer = null;
let charts = [];
let currentRenderId = 0;
const ranges = { history: "24h", apps: "24h", energy: "24h" };
let includeSystem = false;
let dismissedWarnings = new Set();

const colors = {
  accent: "#00d2ff",
  success: "#00ff88",
  warning: "#ffcc00",
  danger: "#ff3366",
  purple: "#b366ff",
  orange: "#ff8833",
  muted: "#8ba1b7"
};

if (window.Chart) {
  Chart.defaults.animation = false;
  Chart.defaults.elements.point.radius = 0;
}

async function j(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

function escapeHTML(str) {
  return String(str).replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[m]);
}

function destroyCharts() { charts.forEach(c => c.destroy()); charts = []; }
Chart.defaults.color = "#8ba1b7";
Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
if (Chart.defaults.plugins.tooltip) {
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(11, 15, 25, 0.95)";
  Chart.defaults.plugins.tooltip.titleColor = "#f0f4f8";
  Chart.defaults.plugins.tooltip.bodyColor = "#8ba1b7";
  Chart.defaults.plugins.tooltip.borderColor = "rgba(0, 210, 255, 0.3)";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
}

function addChart(el, cfg) {
  if (cfg.type === "line" && cfg.data && cfg.data.datasets) {
    cfg.data.datasets.forEach(d => {
      if (d.tension === undefined && !d.stepped) d.tension = 0.4; // smooth curves
    });
  }
  if (cfg.options && cfg.options.scales) {
    Object.values(cfg.options.scales).forEach(scale => {
      if (!scale.grid) scale.grid = {};
      scale.grid.color = "rgba(255, 255, 255, 0.03)";
      scale.grid.drawBorder = false;
    });
  } else if (!cfg.options) {
    cfg.options = { scales: { x: { grid: { color: "rgba(255, 255, 255, 0.03)" } }, y: { grid: { color: "rgba(255, 255, 255, 0.03)" } } } };
  }
  const c = new Chart(el, cfg);
  charts.push(c);
  return c;
}

function fmtMin(m) {
  if (m == null) return "-";
  return Math.floor(m / 60) + "h " + (m % 60) + "m";
}
function fmtDur(sec) { return fmtMin(Math.floor(sec / 60)); }
function fmtWh(wh) {
  return wh < 0.01 ? (wh * 1000).toFixed(1) + " mWh" : wh.toFixed(2) + " Wh";
}
function tsLabel(ts) {
  if (typeof ts === "string") return ts;
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
    " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function setLive(ok, text) {
  $("#live").classList.toggle("down", !ok);
  $("#livetext").textContent = text;
}

function emptyNote(text) {
  return `<p class="muted">${escapeHTML(text)}</p>`;
}

const TAB_RANGES = {
  history: ["24h", "7d", "30d"],
  energy: ["24h", "7d", "30d"],
  apps: ["1h", "8h", "24h", "7d", "30d"],
};

function rangeButtons(tab, extra) {
  const html = TAB_RANGES[tab].map(x =>
    `<button data-range="${x}" class="${x === ranges[tab] ? "active" : ""}">${x}</button>`
  ).join("") + (extra || "");
  return `<div class="rangebar">${html}</div>`;
}

function bindRangeButtons(tab) {
  document.querySelectorAll(".rangebar button[data-range]").forEach(b =>
    b.addEventListener("click", () => {
      ranges[tab] = b.dataset.range;
      switchTab(tab);
    }));
}

async function appAction(app, action) {
  try {
    await fetch("/api/apps/action", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app, action }) });
    alert(app + " " +
      { pause: "paused", resume: "resumed", kill: "killed" }[action] + ".");
  } catch (e) { alert("Failed to " + action + " " + app); }
}

async function renderNow(renderId) {
  let d;
  try { d = await j("/api/now"); }
  catch (e) {
    if (renderId !== currentRenderId) return;
    $("#stale").style.display = "block";
    $("#content").innerHTML = '<div class="muted">Offline</div>';
    return;
  }
  if (renderId !== currentRenderId) return;
  $("#stale").style.display = d.staleness_sec > 180 ? "block" : "none";
  setLive(d.staleness_sec <= 180, "live · " + d.staleness_sec + "s ago");
  $("#awake").checked = d.awake;
  renderLongevity(d);

  let warningsHTML = "";
  if (d.radio_warnings && d.radio_warnings.length > 0) {
    for (const rw of d.radio_warnings) {
      const wid = "rw-" + rw.ts;
      if (dismissedWarnings.has(wid)) continue;
      warningsHTML += `<div class="card" style="border-left: 4px solid var(--warning); background: rgba(255, 204, 0, 0.05); margin-bottom: 8px; padding: 12px 16px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div style="font-size: 14px;"><strong style="color:var(--warning)">Radio Issue:</strong> ${escapeHTML(rw.reason || "Unknown")} at ${tsLabel(rw.ts)}</div>
          <button onclick="dismissWarning('${wid}')" style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:16px;">✖</button>
        </div>
      </div>`;
    }
  }
  if (d.dark_wakes && d.dark_wakes.length > 0) {
    for (const dw of d.dark_wakes) {
      const wid = "dw-" + dw.ts;
      if (dismissedWarnings.has(wid)) continue;
      // Only show the sleep-gap duration when it is coherent (>= 1 min).
      // Legacy rows stored a single ~5s wake, which renders as "0h 0m".
      const durPart = dw.duration_sec >= 60 ? `over ${fmtDur(dw.duration_sec)} ` : "";
      const cul = dw.culprits || [];
      const woke = cul.filter(c => c.why === "woke").map(c => escapeHTML(c.proc));
      const held = cul.filter(c => c.why === "kept-awake").map(c => escapeHTML(c.proc));
      let culLine = "";
      if (woke.length) culLine += ` · woke: ${woke.join(", ")}`;
      if (held.length) culLine += ` · held: ${held.join(", ")}`;
      warningsHTML += `<div class="card" style="border-left: 4px solid var(--purple); background: rgba(179, 102, 255, 0.05); margin-bottom: 8px; padding: 12px 16px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <div style="font-size: 14px;"><strong style="color:var(--purple)">Dark Wake:</strong> ${escapeHTML(dw.reason || "Unknown")} drained ${fmtWh(dw.wh_drained || 0)} ${durPart}at ${tsLabel(dw.ts)}${culLine}</div>
          <button onclick="dismissWarning('${wid}')" style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:16px;">✖</button>
        </div>
      </div>`;
    }
    if (d.frequent_culprit && d.dark_wakes && d.dark_wakes.length > 1) {
      const fc = d.frequent_culprit;
      warningsHTML += `<div class="card" style="border-left: 4px solid var(--warning); background: rgba(255, 204, 0, 0.05); margin-bottom: 8px; padding: 12px 16px;">
        <div style="font-size: 14px;">⚠️ <strong style="color:var(--warning)">Frequent culprit:</strong> ${escapeHTML(fc.proc)} (${fc.n} of last ${d.dark_wakes.length} drains)</div>
      </div>`;
    }
  }
  const rwContainer = $("#radio-warnings-container");
  if (rwContainer) {
    rwContainer.innerHTML = warningsHTML;
  }

  const s = d.sample, f = d.forecast || {}, h = d.health, c = d.component,
        sess = d.session;
  const dir = s.watts > 0 ? "^" : (s.watts < 0 ? "v" : "-");
  const chip = f.minutes != null
    ? `<span class="chip">${f.mode === "charging" ? "full in " : "~"}${fmtMin(f.minutes)}${f.mode === "charging" ? "" : " left"}</span>`
    : "";
  const src = s.on_ac ? (s.is_charging ? "charging" : "on AC") : "on battery";
  const cards = [];
  cards.push(["Charge", h
    ? `${h.raw_current_capacity_mah.toFixed(0)} / ${h.raw_max_capacity_mah.toFixed(0)} mAh`
    : "collecting"]);
  cards.push(["Health", h
    ? `${h.max_capacity_pct.toFixed(0)}% · ${h.cycle_count} cycles`
    : "collecting"]);
  const socTemp = c && c.soc_temp_c != null ? c.soc_temp_c.toFixed(0) + " C" : "-";
  const ssdTemp = c && c.ssd_temp_c != null ? c.ssd_temp_c.toFixed(0) + " C" : "-";
  const batTemp = s.temp_c != null ? s.temp_c.toFixed(0) + " C" : "-";
  const therm = c && c.thermal_pressure ? escapeHTML(c.thermal_pressure.toLowerCase()) : "-";
  cards.push(["Temp / thermal",
    `SoC (CPU/GPU) ${socTemp} - SSD ${ssdTemp} - Battery ${batTemp} - ${therm}`]);
  cards.push(["Brightness",
    s.brightness_pct != null ? s.brightness_pct.toFixed(0) + "%" : "-"]);
  if (sess && sess.soc_now != null) {
    const delta = sess.soc_now - sess.soc_start;
    cards.push(["Session",
      `${fmtDur(sess.duration_sec)} · ${delta >= 0 ? "+" : ""}${delta.toFixed(0)}% · ${sess.wh != null ? fmtWh(sess.wh) : "-"}`]);
  }
  if (d.devices && d.devices.length > 0) {
    for (const dev of d.devices) {
      cards.push([dev.name, dev.battery_pct + "%"]);
    }
  }
  const cardHTML = cards.map(([k, v]) =>
    `<div class="card"><div class="k">${escapeHTML(k)}</div><div class="v">${escapeHTML(v)}</div></div>`
  ).join("");
  let compHTML = "";
  if (c && c.package_mw != null) {
    const pkg = Math.max(c.package_mw, 1);
    const row = (lbl, mw, desc) => `<div class="barrow" title="${escapeHTML(desc)}"><span class="lbl" style="cursor:help; border-bottom:1px dotted var(--text-muted);">${lbl}</span>
      <span class="minibar-track"><span class="minibar" style="width:${Math.min(100, (mw || 0) / pkg * 100).toFixed(0)}%"></span></span>
      <span class="val">${(mw || 0).toFixed(0)} <abbr title="Milliwatts (Power Consumption)">mW</abbr></span></div>`;
    compHTML = `<h3 style="cursor:help; border-bottom:1px dotted var(--text-muted); display:inline-block;" title="Total power consumption of the main Apple Silicon chip (System-on-Chip)">Package now: ${c.package_mw.toFixed(0)} <abbr title="Milliwatts (Power Consumption)">mW</abbr></h3>` +
      row("CPU", c.cpu_mw, "Central Processing Unit (Main general-purpose processor core consumption)") + 
      row("GPU", c.gpu_mw, "Graphics Processing Unit (Graphics, video, and display processing)") + 
      row("ANE", c.ane_mw, "Apple Neural Engine (Hardware acceleration for AI and Machine Learning tasks)");
  }
  const maxWh = Math.max(...d.top_apps.map(a => a.attributed_wh), 1e-9);
  const rows = d.top_apps.map(a => {
    let pctStr = "";
    if (d.health && d.health.design_capacity_mah) {
      // Nominal MacBook battery voltage is ~11.4V. Wh = mAh * 11.4 / 1000
      const totalWh = (d.health.design_capacity_mah * 11.4) / 1000;
      const pct = (a.attributed_wh / totalWh) * 100;
      if (pct >= 0.05) {
        pctStr = ` <span style="font-size: 11px; margin-left: 4px;">(${pct.toFixed(1)}%)</span>`;
      } else {
        pctStr = ` <span style="font-size: 11px; margin-left: 4px;">(<0.1%)</span>`;
      }
    }
    return `<tr class="app-row">
    <td>${escapeHTML(a.app)}</td>
    <td style="width:40%"><div class="sharebar-wrapper"><div class="sharebar" style="width:${Math.max(2, a.attributed_wh / maxWh * 100).toFixed(0)}%"></div></div></td>
    <td style="text-align:right;color:var(--text-muted);white-space:nowrap;">${fmtWh(a.attributed_wh)}${pctStr}</td>
    <td class="app-actions">
      <button data-app="${escapeHTML(a.app)}" data-action="pause" class="btn-pause" title="Pause (SIGSTOP)">||</button>
      <button data-app="${escapeHTML(a.app)}" data-action="resume" class="btn-resume" title="Resume (SIGCONT)">▶</button>
      <button data-app="${escapeHTML(a.app)}" data-action="kill" class="btn-kill" title="Kill (SIGTERM)">✖</button>
    </td>
    </tr>`;
  }).join("");
  $("#content").innerHTML = `
    <div class="big">${Math.abs(s.watts).toFixed(1)} W ${dir} ${s.soc_pct}%
      ${chip}<span class="chip gray">${src}</span></div>
    <div class="grid">${cardHTML}</div>
    ${compHTML}
    <h3>Top apps, last hour (attributed)</h3>
    <table class="app-table">${rows || "<tr><td class='muted'>no attribution data yet - it appears within a minute of the daemon running</td></tr>"}</table>`;
}

async function renderHistory(renderId) {
  const rng = ranges.history;
  const [d, nowData] = await Promise.all([
    j("/api/history?range=" + rng),
    j("/api/now")
  ]);
  if (renderId !== currentRenderId) return;
  $("#content").innerHTML = rangeButtons("history") +
    (d.battery.length === 0
      ? emptyNote("no data in this range yet - 24h shows minute-level data as soon as the daemon runs; 7d/30d fill in after the first full hour")
      : '<h3>Battery</h3><canvas id="c1"></canvas><h3>Components</h3><canvas id="c2"></canvas><h3>Temperature</h3><canvas id="c3"></canvas>');
  bindRangeButtons("history");
  if (d.battery.length === 0) return;
  const bLabels = d.battery.map(r => tsLabel(r.ts));
  
  const socSets = rng === "24h"
    ? [{ label: "SoC %", data: d.battery.map(r => r.soc_pct), yAxisID: "y",
         borderColor: colors.accent, backgroundColor: "rgba(0, 210, 255, 0.1)", fill: true, borderWidth: 2 }]
    : [{ label: "SoC max %", data: d.battery.map(r => r.soc_max), yAxisID: "y",
         borderColor: colors.accent, backgroundColor: "rgba(0, 210, 255, 0.1)", fill: true, borderWidth: 2 },
       { label: "SoC min %", data: d.battery.map(r => r.soc_min), yAxisID: "y",
         borderColor: "rgba(0, 210, 255, 0.4)", borderWidth: 1 }];

  const extraSets = [];
  
  if (nowData.charge_limit) {
    const level = nowData.charge_limit.level != null ? nowData.charge_limit.level : 80;
    extraSets.push({
      label: `${level}% target`,
      data: Array(d.battery.length).fill(level),
      yAxisID: "y",
      borderColor: "rgba(255, 99, 132, 0.8)",
      borderWidth: 1,
      borderDash: [5, 5],
      pointRadius: 0,
      fill: false
    });
  }

  if (rng === "24h") {
    extraSets.push({
      label: "Awake State",
      data: d.battery.map(r => r.assert_awake ? 100 : 0),
      yAxisID: "y",
      backgroundColor: "rgba(255, 204, 0, 0.1)",
      borderColor: "transparent",
      borderWidth: 0,
      pointRadius: 0,
      fill: true,
      stepped: true
    });
  }

  addChart($("#c1"), { type: "line", data: { labels: bLabels, datasets: [
    ...extraSets,
    ...socSets,
    { label: "watts", data: d.battery.map(r => r.watts), yAxisID: "y2",
      borderColor: colors.orange, backgroundColor: "rgba(255, 136, 51, 0.1)", fill: true, borderWidth: 1 },
  ]}, options: { scales: {
    y: { min: 0, max: 100, title: { display: true, text: "%" } },
    y2: { position: "right", title: { display: true, text: "W" },
          grid: { drawOnChartArea: false } } } } });
  const cLabels = d.components.map(r => tsLabel(r.ts));
  addChart($("#c2"), { type: "line", data: { labels: cLabels, datasets: [
    { label: "CPU mW", data: d.components.map(r => r.cpu_mw), borderColor: colors.accent, borderWidth: 2 },
    { label: "GPU mW", data: d.components.map(r => r.gpu_mw), borderColor: colors.success, borderWidth: 2 },
    { label: "ANE mW", data: d.components.map(r => r.ane_mw), borderColor: colors.purple, borderWidth: 2 },
    { label: "Package mW", data: d.components.map(r => r.package_mw), borderColor: colors.orange, borderWidth: 2, borderDash: [5, 5] },
  ]}});
  
  if (d.temperature && d.temperature.length > 0) {
    const tLabels = d.temperature.map(r => tsLabel(r.ts));
    addChart($("#c3"), { type: "line", data: { labels: tLabels, datasets: [
      { label: "SoC °C", data: d.temperature.map(r => r.soc_temp_c), borderColor: colors.accent, borderWidth: 2 },
      { label: "SSD °C", data: d.temperature.map(r => r.ssd_temp_c), borderColor: colors.purple, borderWidth: 2 },
      { label: "Battery °C", data: d.temperature.map(r => r.temp_c), borderColor: colors.orange, borderWidth: 2 }
    ]}});
  }
}

async function renderApps(renderId) {
  const d = await j(`/api/apps?range=${ranges.apps}&include_system=${includeSystem}`);
  if (renderId !== currentRenderId) return;
  const toggle = `<button id="systoggle">${includeSystem ? "hide" : "show"} system</button>`;
  const top = d.slice(0, 15);
  $("#content").innerHTML = rangeButtons("apps", toggle) +
    (top.length === 0
      ? emptyNote("no attribution data in this range yet")
      : '<canvas id="c1"></canvas>');
  bindRangeButtons("apps");
  $("#systoggle").addEventListener("click", () => {
    includeSystem = !includeSystem;
    switchTab("apps");
  });
  if (top.length === 0) return;
  addChart($("#c1"), { type: "bar", data: {
    labels: top.map(a => a.app),
    datasets: [{ label: "Attributed Wh",
      data: top.map(a => a.attributed_wh), backgroundColor: "rgba(0, 210, 255, 0.6)", borderColor: colors.accent, borderWidth: 1, borderRadius: 4, hoverBackgroundColor: colors.accent }]
  }, options: { indexAxis: "y", plugins: { tooltip: { callbacks: {
    label: (ctx) => fmtWh(ctx.parsed.x) + " · " +
      top[ctx.dataIndex].share_pct.toFixed(1) + "%" } } } } });
}

async function renderEnergy(renderId) {
  const d = await j("/api/energy?range=" + ranges.energy);
  if (renderId !== currentRenderId) return;
  $("#content").innerHTML = rangeButtons("energy") +
    (d.length === 0
      ? emptyNote("no energy buckets yet - the first one appears within a minute")
      : '<canvas id="c1"></canvas><p class="muted">bars: discharged / charged energy per bucket; line: average display brightness; the last bucket is in progress</p>');
  bindRangeButtons("energy");
  if (d.length === 0) return;
  addChart($("#c1"), { type: "bar", data: {
    labels: d.map(r => tsLabel(r.ts) + (r.partial ? " *" : "")),
    datasets: [
      { label: "Discharged Wh", data: d.map(r => r.wh_out), backgroundColor: "rgba(255, 136, 51, 0.6)", borderColor: colors.orange, borderWidth: 1, borderRadius: 4 },
      { label: "Charged Wh", data: d.map(r => r.wh_in), backgroundColor: "rgba(0, 255, 136, 0.6)", borderColor: colors.success, borderWidth: 1, borderRadius: 4 },
      { label: "Brightness %", type: "line", data: d.map(r => r.avg_brightness),
        yAxisID: "y2", borderColor: colors.muted, borderWidth: 2 },
    ]}, options: { scales: {
      y: { title: { display: true, text: "Wh" } },
      y2: { position: "right", min: 0, max: 100,
            grid: { drawOnChartArea: false } } } } });
}

async function renderHealth(renderId) {
  const [rows, now] = await Promise.all([j("/api/health"), j("/api/now")]);
  if (renderId !== currentRenderId) return;
  const h = now.health;
  
  let extraCards = "";
  if (h && h.cell_voltage_mv && h.lifetime_temp_min !== undefined) {
    const minTemp = h.lifetime_temp_min.toFixed(1);
    const maxTemp = h.lifetime_temp_max.toFixed(1);
    const avgTemp = h.lifetime_temp_avg.toFixed(1);
    const opDays = (h.operating_time_hours / 24).toFixed(0);
    const cells = h.cell_voltage_mv;
    const maxCell = Math.max(...cells);
    const minCell = Math.min(...cells);
    const diff = maxCell - minCell;
    const diffColor = diff > 50 ? "var(--danger)" : "var(--success)";
    const tempColor = h.lifetime_temp_max > 40 ? "var(--warning)" : "var(--success)";
    
    extraCards = `
    <h3 style="margin-top:24px"><span style="font-size:18px">🔬</span> Deep Diagnostics</h3>
    <div class="grid">
      <div class="card" style="border-top: 2px solid var(--accent)">
        <div class="k">🔋 Cell Balance</div>
        <div class="v" style="font-size:16px">${cells.join(' / ')} mV</div>
        <div class="k" style="margin-top:8px; color: ${diffColor}">Imbalance: ${diff} mV</div>
      </div>
      <div class="card" style="border-top: 2px solid ${tempColor}">
        <div class="k">🌡️ Lifetime Temps</div>
        <div class="v" style="font-size:16px">${minTemp}°C - <span style="color:${tempColor}">${maxTemp}°C</span></div>
        <div class="k" style="margin-top:8px">Avg: ${avgTemp}°C</div>
      </div>
      <div class="card" style="border-top: 2px solid var(--purple)">
        <div class="k">⏱️ Battery Age</div>
        <div class="v">${opDays} days</div>
        <div class="k" style="margin-top:8px">Active operating time</div>
      </div>
    </div>`;
  }

  const cards = h ? `<div class="grid">
    <div class="card"><div class="k">Max capacity</div><div class="v">${h.max_capacity_pct.toFixed(1)}%</div></div>
    <div class="card"><div class="k">Cycle count</div><div class="v">${h.cycle_count}</div></div>
    <div class="card"><div class="k">Full charge</div><div class="v">${h.raw_max_capacity_mah.toFixed(0)} mAh</div></div>
    <div class="card"><div class="k">Design</div><div class="v">${h.design_capacity_mah.toFixed(0)} mAh</div></div>
  </div>${extraCards}` : emptyNote("no health data yet");
  
  $("#content").innerHTML = cards +
    (rows.length < 2
      ? emptyNote("trend appears after a few daily snapshots (one is taken when the daemon starts and then once per day)")
      : '<h3 style="margin-top:24px"><span style="font-size:18px">📉</span> Capacity Trend</h3><canvas id="c1"></canvas>');
  if (rows.length < 2) return;
  addChart($("#c1"), { type: "line", data: {
    labels: rows.map(r => r.day),
    datasets: [
      { label: "Max Capacity %", data: rows.map(r => r.max_capacity_pct),
        yAxisID: "y", borderColor: colors.accent, backgroundColor: "rgba(0, 210, 255, 0.1)", fill: true, borderWidth: 2 },
      { label: "Cycles", data: rows.map(r => r.cycle_count),
        yAxisID: "y2", borderColor: colors.orange, borderWidth: 2 },
    ]}, options: { scales: { y2: { position: "right",
      grid: { drawOnChartArea: false } } } } });
}

async function renderCharging(renderId) {
  const [d, hb] = await Promise.all([j("/api/charging"), j("/api/habits")]);
  if (renderId !== currentRenderId) return;
  const a = d.aggregates;
  const hist = a.discharge_depth_hist || {};
  const histKeys = Object.keys(hist).sort(
    (x, y) => parseInt(x) - parseInt(y));
    
  const rows = d.sessions.slice(0, 50).map(s => {
    const isNow = !s.ended;
    const kindChip = s.kind === "AC" 
      ? `<span class="chip" style="color:var(--success);border-color:rgba(0,255,136,0.3);background:rgba(0,255,136,0.1);box-shadow:0 0 10px rgba(0,255,136,0.1)">🔌 AC</span>` 
      : `<span class="chip" style="color:var(--warning);border-color:rgba(255,204,0,0.3);background:rgba(255,204,0,0.1);box-shadow:0 0 10px rgba(255,204,0,0.1)">🔋 Battery</span>`;
    
    return `<tr ${isNow ? 'style="background: rgba(0, 210, 255, 0.05);"' : ''}>
    <td>${kindChip}${isNow ? ' <span class="chip" style="animation: pulse 2s infinite; border-color:var(--accent); color:var(--accent); background:rgba(0,210,255,0.1)">live</span>' : ''}</td>
    <td>${new Date(s.started * 1000).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</td>
    <td>${s.ended ? new Date(s.ended * 1000).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '<span style="color:var(--accent);text-shadow:0 0 8px var(--accent-glow)">open</span>'}</td>
    <td><span style="color:var(--text-main); font-weight: 600">${s.soc_start}%</span> <span style="color:var(--text-muted)">➔</span> <span style="color:var(--text-main); font-weight: 600">${s.soc_end != null ? s.soc_end + "%" : "-"}</span></td>
    <td style="text-align:right; font-weight:600; color:${s.kind === 'AC' ? 'var(--success)' : 'var(--warning)'}">${s.wh != null ? fmtWh(s.wh) : "-"}</td></tr>`;
  }).join("");
  
  $("#content").innerHTML = `
    <div class="grid">
      <div class="card" style="border-top: 2px solid var(--warning)">
        <div class="k">🔋 Time on Battery</div>
        <div class="v">${(a.battery_sec / 3600).toFixed(1)} h</div>
      </div>
      <div class="card" style="border-top: 2px solid var(--success)">
        <div class="k">🔌 Time on AC</div>
        <div class="v">${(a.ac_sec / 3600).toFixed(1)} h</div>
      </div>
      <div class="card" style="border-top: 2px solid var(--accent)">
        <div class="k">⚡ Avg Charge Power</div>
        <div class="v">${a.avg_charge_watts != null ? a.avg_charge_watts.toFixed(1) + " W" : "-"}</div>
      </div>
    </div>
    <h3><span style="font-size:18px">🧭</span> Charging Habits (30 days)</h3>
    <div class="grid">
      <div class="card">
        <div class="k">Time at full while plugged</div>
        <div class="v">${hb.full_pct_of_ac != null ? hb.full_pct_of_ac.toFixed(0) + "% of AC time" : "-"}</div>
      </div>
      <div class="card">
        <div class="k">Plugged-in share</div>
        <div class="v">${hb.ac_share_pct != null ? hb.ac_share_pct.toFixed(0) + "%" : "-"}</div>
      </div>
      <div class="card">
        <div class="k">Overnight charges</div>
        <div class="v">${hb.overnight_sessions}</div>
      </div>
      <div class="card">
        <div class="k">Deep discharges (&lt;10%)</div>
        <div class="v">${hb.deep_discharges}</div>
      </div>
      <div class="card">
        <div class="k">Cycles added</div>
        <div class="v">${hb.cycles_30d != null ? hb.cycles_30d : "-"}</div>
      </div>
      <div class="card">
        <div class="k">Avg battery temp</div>
        <div class="v">${hb.avg_temp_c != null ? hb.avg_temp_c.toFixed(1) + " °C" : "-"}</div>
      </div>
    </div>
    ${histKeys.length ? '<h3 style="margin-top:32px"><span style="font-size:18px">📉</span> Discharge Depth Overview</h3><canvas id="c1" style="max-height:220px; margin-bottom: 24px;"></canvas>' : ""}
    <h3 style="margin-top:24px"><span style="font-size:18px">⏱️</span> Recent Sessions</h3>
    <table><tr><th>Type</th><th>Start</th><th>End</th><th>SoC Shift</th><th style="text-align:right">Energy</th></tr>
    ${rows || "<tr><td class='muted' colspan='5'>no sessions yet</td></tr>"}</table>`;
    
  if (histKeys.length) {
    addChart($("#c1"), { type: "bar", data: {
      labels: histKeys.map(k => k + "%"),
      datasets: [{ label: "Discharge Sessions", data: histKeys.map(k => hist[k]),
        backgroundColor: "rgba(0, 210, 255, 0.6)", borderColor: "var(--accent)", borderWidth: 1, borderRadius: 4, hoverBackgroundColor: "var(--accent)" }]
    }});
  }
}

async function renderAnomalies(renderId) {
  const d = await j("/api/anomalies?since=0");
  if (renderId !== currentRenderId) return;
  if (d.length === 0) {
    $("#content").innerHTML = emptyNote(
      "no anomalies detected. Anomalies track apps using 2x their 7-day average, as well as system issues like high thermal pressure, sleep drain, rapid discharge, or a weak charger.");
    return;
  }
  const rows = d.slice().reverse().map(a => {
    let appText = escapeHTML(a.app);
    let todayText = `${a.wh_today.toFixed(1)} Wh`;
    let baselineText = `${a.wh_baseline.toFixed(1)} Wh`;
    let ratioText = `${a.ratio.toFixed(1)}x`;

    if (a.app === "__SYSTEM_THERMAL__") {
        appText = "🌡️ High Thermal Pressure";
        todayText = `${a.wh_today.toFixed(0)} mins`;
        baselineText = "5 mins threshold";
        ratioText = "-";
    } else if (a.app === "__SYSTEM_SLEEP_DRAIN__") {
        appText = "⚠️ High Sleep Drain";
        todayText = `${a.wh_today.toFixed(1)}% dropped`;
        baselineText = "5% threshold";
        ratioText = "-";
    } else if (a.app === "__SYSTEM_RAPID_DISCHARGE__") {
        appText = "⚡ Rapid Discharge";
        todayText = `${a.wh_today.toFixed(1)}W avg`;
        baselineText = "30W threshold";
        ratioText = "-";
    } else if (a.app === "__SYSTEM_WEAK_CHARGER__") {
        appText = "🔌 Weak Charger - charging is bad";
        todayText = `${a.wh_today.toFixed(1)}W draining while plugged in`;
        baselineText = "should be charging";
        ratioText = "-";
    }

    let rowHTML = `<tr ${a.detail ? 'style="border-bottom: none;"' : ''}>
    <td ${a.detail ? 'style="border-bottom: none;"' : ''}>${new Date(a.ts * 1000).toLocaleString()}</td>
    <td ${a.detail ? 'style="border-bottom: none;"' : ''}>${appText}</td>
    <td ${a.detail ? 'style="border-bottom: none;"' : ''}>${todayText}</td>
    <td ${a.detail ? 'style="border-bottom: none;"' : ''}>${baselineText}</td>
    <td ${a.detail ? 'style="border-bottom: none;"' : ''}>${ratioText}</td></tr>`;

    if (a.detail) {
      const culpritsText = a.detail.culprits ? a.detail.culprits.map(c => {
        const w = (typeof c.wh === "number" && c.wh > 0) ? ` (${c.wh.toFixed(1)} Wh)` : "";
        return `${escapeHTML(c.app)}${w}`;
      }).join(", ") : "";
      const adviceText = a.detail.advice ? escapeHTML(a.detail.advice) : "";
      let detailContent = "";
      if (culpritsText) detailContent += `Caused by: ${culpritsText}`;
      if (culpritsText && adviceText) detailContent += ` - `;
      if (adviceText) detailContent += `What to do: ${adviceText}`;
      
      rowHTML += `<tr><td colspan="5" class="muted" style="padding-top: 0; padding-bottom: 12px;">${detailContent}</td></tr>`;
    }
    return rowHTML;
  }).join("");
  $("#content").innerHTML = `
    <table><tr><th>when</th><th>app/event</th><th>value</th><th>baseline</th><th>ratio</th></tr>
    ${rows}</table>`;
}

const RENDER = { now: renderNow, history: renderHistory, apps: renderApps,
                 energy: renderEnergy, health: renderHealth,
                 charging: renderCharging, anomalies: renderAnomalies };

function switchTab(tab) {
  currentTab = tab;
  currentRenderId++;
  const rid = currentRenderId;
  destroyCharts();
  document.querySelectorAll("#tabs button").forEach(b =>
    b.classList.toggle("active", b.dataset.tab === tab));
  clearInterval(pollTimer);
  RENDER[tab](rid).catch(e => {
    if (rid !== currentRenderId) return;
    $("#content").textContent = String(e);
  });
  if (tab === "now") {
    pollTimer = setInterval(() => renderNow(currentRenderId), 5000);
  } else {
    pollTimer = setInterval(() => RENDER[tab](currentRenderId), 60000);
  }
}

async function pollStatus() {
  try {
    const st = await j("/api/status");
    const age = st.last_sample_ts != null ? st.now_ts - st.last_sample_ts : null;
    const ok = age != null && age <= 180;
    setLive(ok, ok ? "live · " + age + "s ago" : "daemon stale");
    if (currentTab !== "now") $("#stale").style.display = ok ? "none" : "block";
    const an = await j("/api/anomalies?since=0");
    const recent = an.filter(a => st.now_ts - a.ts < 86400).length;
    const badge = $("#anombadge");
    badge.style.display = recent ? "inline" : "none";
    badge.textContent = recent;
  } catch (e) {
    setLive(false, "web offline");
  }
}

window.dismissWarning = function(warningId) {
    dismissedWarnings.add(warningId);
    if (currentTab === "now") renderNow(currentRenderId);
};

$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b && b.dataset.tab) switchTab(b.dataset.tab);
});
// Delegated so process names never reach an inline handler (XSS-safe): the
// name lives in data-app and is read back as an inert string, never as code.
$("#content").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-action][data-app]");
  if (b) appAction(b.dataset.app, b.dataset.action);
});
$("#awake").addEventListener("change", async (e) => {
  const prev = !e.target.checked;
  try {
    const r = await j2post({ on: e.target.checked });
    e.target.checked = r.awake;
  } catch (err) {
    e.target.checked = prev;
    console.error(err);
  }
});
async function j2post(body) {
  const r = await fetch("/api/awake", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  if (!r.ok) throw new Error("/api/awake -> " + r.status);
  return r.json();
}

// Charge Limit: read-only mirror of the native macOS 80% limit. batmon cannot
// set it on Apple Silicon; the Settings link deep-links to System Settings.
// "holding" is inferred from today's peak charge (see queries.charge_limit_status).
function renderLongevity(d) {
  const cl = d.charge_limit || {};
  const level = cl.level != null ? cl.level : 80;
  const verdict = cl.holding === true ? "active"
                : cl.holding === false ? "off" : "-";
  const peak = cl.todays_peak_soc;
  const peakTxt = peak != null ? ` · peak ${Math.round(peak)}%` : "";
  $("#cl-status").textContent = `Limit ${level}%: ${verdict}${peakTxt}`;
}
// Open System Settings > Battery server-side (reliable across browsers; the
// x-apple.systempreferences: scheme cannot be followed from a fetch/link alone).
$("#cl-settings").addEventListener("click", () => {
  fetch("/api/open_battery_settings", { method: "POST",
    headers: { "X-Batmon-Client": "1" } }).catch(e => console.error(e));
});

switchTab("now");
pollStatus();
setInterval(pollStatus, 30000);

})();
