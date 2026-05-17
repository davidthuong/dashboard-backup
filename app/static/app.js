const state = {
  source: "",
  status: "",
  search: "",
  historyPage: 1,
  historyPageSize: 30,
  agentsEnabled: false,
  pullCollectionEnabled: true,
  activeServerWindowMinutes: 1440,
};

function qs(id) {
  return document.getElementById(id);
}

function fmtDate(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function fmtCollectedDate(value) {
  if (!value) return "-";
  const text = String(value).trim();
  if (!text) return "-";

  const hasTz = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(text);
  let candidate = text;
  if (!hasTz) {
    candidate = text.replace(" ", "T") + "Z";
  }

  const d = new Date(candidate);
  if (Number.isNaN(d.getTime())) return fmtDate(value);
  return d.toLocaleString();
}

function parseCollectedAtMs(value) {
  if (!value) return Number.NaN;
  const text = String(value).trim();
  if (!text) return Number.NaN;
  const hasTz = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(text);
  let candidate = text;
  if (!hasTz) {
    candidate = text.replace(" ", "T") + "Z";
  }
  return Date.parse(candidate);
}

function statusClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "success") return "chip success";
  if (s === "failed") return "chip failed";
  if (s === "warning") return "chip warning";
  if (s === "running") return "chip running";
  return "chip unknown";
}

function esc(value) {
  const text = String(value ?? "");
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setStatus(text, level = "ok") {
  const el = qs("statusLine");
  el.textContent = text;
  el.classList.remove("status-error", "status-ok");
  el.classList.add(level === "error" ? "status-error" : "status-ok");
}

async function api(url, options = {}) {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    return null;
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return await res.json();
}

function renderLatest(items) {
  const tbody = qs("latestTable").querySelector("tbody");
  tbody.innerHTML = "";
  const hasNextRunField = items.some((item) => Object.prototype.hasOwnProperty.call(item || {}, "next_run"));
  for (const item of items) {
    const nextRunValue = hasNextRunField ? item.next_run : item.started_at;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(item.source)}</td>
      <td>${esc(item.job_name)}</td>
      <td><span class="${statusClass(item.status)}">${esc(item.status)}</span></td>
      <td>${esc(item.message || "")}</td>
      <td>${esc(fmtDate(nextRunValue))}</td>
      <td>${esc(fmtDate(item.ended_at))}</td>
      <td>${esc(fmtCollectedDate(item.collected_at))}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderHistory(items) {
  const tbody = qs("historyTable").querySelector("tbody");
  tbody.innerHTML = "";
  const hasNextRunField = items.some((item) => Object.prototype.hasOwnProperty.call(item || {}, "next_run"));
  for (const item of items) {
    const nextRunValue = hasNextRunField ? item.next_run : item.started_at;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(fmtCollectedDate(item.collected_at))}</td>
      <td>${esc(item.source)}</td>
      <td>${esc(item.job_name)}</td>
      <td><span class="${statusClass(item.status)}">${esc(item.status)}</span></td>
      <td>${esc(item.message || "")}</td>
      <td>${esc(fmtDate(nextRunValue))}</td>
      <td>${esc(fmtDate(item.ended_at))}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderSummary(items) {
  let success = 0;
  let warning = 0;
  let failed = 0;
  let running = 0;
  let unknown = 0;
  for (const item of items) {
    const s = (item.status || "").toLowerCase();
    if (s === "success") success += 1;
    else if (s === "warning") warning += 1;
    else if (s === "failed") failed += 1;
    else if (s === "running") running += 1;
    else unknown += 1;
  }
  qs("sumTotal").textContent = String(items.length);
  qs("sumSuccess").textContent = String(success);
  qs("sumWarning").textContent = String(warning);
  qs("sumFailed").textContent = String(failed);
  qs("sumRunning").textContent = String(running);
  qs("sumUnknown").textContent = String(unknown);
}

function inferServerName(item) {
  const jobName = String(item?.job_name || "");
  const match = jobName.match(/^\[([^\]]+)\]\s*/);
  if (match && match[1]) return match[1].trim();
  const source = String(item?.source || "unknown").trim() || "unknown";
  return `${source}-local`;
}

function summarizeByServer(items) {
  const map = new Map();
  const nowMs = Date.now();
  const activeWindowMs = Math.max(5, Number(state.activeServerWindowMinutes || 1440)) * 60 * 1000;
  for (const item of items) {
    const server = inferServerName(item);
    const collectedAtMs = parseCollectedAtMs(item?.collected_at);
    if (!map.has(server)) {
      map.set(server, {
        server,
        total: 0,
        success: 0,
        warning: 0,
        failed: 0,
        running: 0,
        unknown: 0,
        latestCollectedAtMs: Number.isNaN(collectedAtMs) ? 0 : collectedAtMs,
      });
    }
    const row = map.get(server);
    row.total += 1;
    if (!Number.isNaN(collectedAtMs) && collectedAtMs > row.latestCollectedAtMs) {
      row.latestCollectedAtMs = collectedAtMs;
    }
    const s = (item.status || "").toLowerCase();
    if (s === "success") row.success += 1;
    else if (s === "warning") row.warning += 1;
    else if (s === "failed") row.failed += 1;
    else if (s === "running") row.running += 1;
    else row.unknown += 1;
  }

  const activeRows = [...map.values()].filter((row) => {
    if (!row.latestCollectedAtMs) return false;
    return nowMs - row.latestCollectedAtMs <= activeWindowMs;
  });

  return activeRows.sort((a, b) => {
    if (b.total !== a.total) return b.total - a.total;
    if (b.failed !== a.failed) return b.failed - a.failed;
    return a.server.localeCompare(b.server);
  });
}

function renderServerSummaryTable(rows) {
  const tbody = qs("serverSummaryTable").querySelector("tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="7">No server data yet.</td>';
    tbody.appendChild(tr);
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(row.server)}</td>
      <td><strong>${row.total}</strong></td>
      <td>${row.success}</td>
      <td>${row.warning}</td>
      <td>${row.failed}</td>
      <td>${row.running}</td>
      <td>${row.unknown}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderServerChart(rows) {
  const chart = qs("serverChartBars");
  chart.innerHTML = "";
  if (!rows.length) return;

  const maxTotal = Math.max(...rows.map((r) => r.total), 1);
  for (const row of rows) {
    const tr = document.createElement("div");
    tr.className = "server-bar-row";

    const nameEl = document.createElement("div");
    nameEl.className = "server-bar-name";
    nameEl.textContent = row.server;
    nameEl.title = row.server;

    const track = document.createElement("div");
    track.className = "server-bar-track";

    const scale = row.total / maxTotal;
    const scaledWidth = Math.max(8, scale * 100);
    const values = [
      { cls: "success", value: row.success },
      { cls: "warning", value: row.warning },
      { cls: "failed", value: row.failed },
      { cls: "running", value: row.running },
      { cls: "unknown", value: row.unknown },
    ];
    for (const part of values) {
      if (!part.value) continue;
      const seg = document.createElement("div");
      seg.className = `server-bar-seg ${part.cls}`;
      seg.style.width = `${(part.value / row.total) * scaledWidth}%`;
      track.appendChild(seg);
    }

    const totalEl = document.createElement("div");
    totalEl.className = "server-bar-total";
    totalEl.textContent = `${row.total} jobs`;

    tr.appendChild(nameEl);
    tr.appendChild(track);
    tr.appendChild(totalEl);
    chart.appendChild(tr);
  }
}

function renderServerOverview(items) {
  const rows = summarizeByServer(items);
  renderServerSummaryTable(rows);
  renderServerChart(rows);
}

function summarizeRcloneByServer(items) {
  const map = new Map();
  for (const item of items) {
    if (String(item?.source || "").toLowerCase() !== "rclone") continue;
    const server = inferServerName(item);
    if (!map.has(server)) {
      map.set(server, {
        server,
        total: 0,
        success: 0,
        failed: 0,
        running: 0,
        unknown: 0,
      });
    }
    const row = map.get(server);
    row.total += 1;
    const s = String(item?.status || "").toLowerCase();
    if (s === "success") row.success += 1;
    else if (s === "failed") row.failed += 1;
    else if (s === "running") row.running += 1;
    else row.unknown += 1;
  }
  return [...map.values()].sort((a, b) => {
    if (b.total !== a.total) return b.total - a.total;
    if (b.failed !== a.failed) return b.failed - a.failed;
    return a.server.localeCompare(b.server);
  });
}

function renderRcloneSummaryTable(rows) {
  const tbody = qs("rcloneSummaryTable").querySelector("tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="6">No rclone data yet.</td>';
    tbody.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(row.server)}</td>
      <td><strong>${row.total}</strong></td>
      <td>${row.success}</td>
      <td>${row.failed}</td>
      <td>${row.running}</td>
      <td>${row.unknown}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderRcloneOverview(items) {
  const rows = summarizeRcloneByServer(items);
  renderRcloneSummaryTable(rows);
}

function queryString() {
  const params = new URLSearchParams();
  if (state.source) params.set("source", state.source);
  if (state.status) params.set("status", state.status);
  if (state.search) params.set("search", state.search);
  return params.toString() ? `?${params.toString()}` : "";
}

function historyQueryString() {
  const params = new URLSearchParams();
  if (state.source) params.set("source", state.source);
  if (state.status) params.set("status", state.status);
  if (state.search) params.set("search", state.search);
  params.set("page", String(state.historyPage));
  params.set("page_size", String(state.historyPageSize));
  return `?${params.toString()}`;
}

function renderHistoryPager(meta) {
  const page = Number(meta?.page || 1);
  const totalPages = Number(meta?.total_pages || 1);
  const total = Number(meta?.total || 0);
  const hasPrev = Boolean(meta?.has_prev);
  const hasNext = Boolean(meta?.has_next);

  state.historyPage = page;
  qs("historyPageInfo").textContent = `Page ${page} / ${totalPages} | ${total} records`;
  qs("historyPrevBtn").disabled = !hasPrev;
  qs("historyNextBtn").disabled = !hasNext;
  qs("historyPageSize").value = String(state.historyPageSize);
}

async function loadData() {
  const query = queryString();
  const historyQuery = historyQueryString();
  const [latest, history] = await Promise.all([
    api(`/api/jobs/latest${query}`),
    api(`/api/jobs/history${historyQuery}`),
  ]);
  if (!latest || !history) return;
  renderLatest(latest.items || []);
  renderHistory(history.items || []);
  renderHistoryPager(history);
  renderSummary(latest.items || []);
  renderServerOverview(latest.items || []);
  renderRcloneOverview(latest.items || []);
  setStatus(`Last updated: ${new Date().toLocaleTimeString()}`, "ok");
}

async function loadPolling() {
  const data = await api("/api/settings/polling");
  if (!data) return;
  qs("pollingSeconds").value = data.polling_interval_seconds;
}

async function savePolling() {
  const value = Number(qs("pollingSeconds").value || "0");
  if (value < 15 || value > 3600) {
    setStatus("Polling must be 15-3600 seconds.", "error");
    return;
  }
  await api("/api/settings/polling", {
    method: "PUT",
    body: JSON.stringify({ polling_interval_seconds: value }),
  });
  setStatus(`Polling interval saved: ${value}s`, "ok");
}

async function collectNow() {
  if (!state.pullCollectionEnabled) {
    setStatus("Collect is disabled in ingest-only mode.", "error");
    return;
  }
  setStatus("Collecting...", "ok");
  const data = await api("/api/jobs/collect", { method: "POST" });
  if (!data) return;
  setStatus(`Collected ${data.total_records} records, ${data.failed_records} failed/warning`, "ok");
  await loadData();
}

async function triggerAgents() {
  if (!state.agentsEnabled) {
    setStatus("Agent trigger is disabled on hub (.env: AGENT_TRIGGER_ENABLED=true).", "error");
    return;
  }
  setStatus("Triggering remote agents...", "ok");
  try {
    const data = await api("/api/agents/trigger-all", { method: "POST" });
    if (!data) return;
    const okCount = Number(data.ok_count || 0);
    const failedCount = Number(data.failed_count || 0);
    if (failedCount > 0) {
      setStatus(`Agents triggered: ${okCount} ok, ${failedCount} failed`, "error");
    } else {
      setStatus(`Agents triggered successfully: ${okCount}`, "ok");
    }
    await loadData();
  } catch (err) {
    setStatus(`Trigger failed: ${err.message}`, "error");
  }
}

async function loadAgentConfig() {
  const btn = qs("triggerAgentsBtn");
  try {
    const data = await api("/api/agents");
    if (!data) return;
    state.agentsEnabled = Boolean(data.enabled);
    if (state.agentsEnabled) {
      btn.disabled = false;
      btn.title = `Configured agent nodes: ${Number(data.count || 0)}`;
    } else {
      btn.disabled = true;
      btn.title = "Enable AGENT_TRIGGER_ENABLED=true in hub .env";
    }
  } catch {
    state.agentsEnabled = false;
    btn.disabled = true;
    btn.title = "Cannot load agent config";
  }
}

async function loadSystemCapabilities() {
  const collectBtn = qs("refreshBtn");
  const savePollingBtn = qs("savePollingBtn");
  const pollingInput = qs("pollingSeconds");
  try {
    const data = await api("/api/system/capabilities");
    if (!data) return;
    state.pullCollectionEnabled = Boolean(data.pull_collection_enabled);
    state.activeServerWindowMinutes = Number(data.active_server_window_minutes || 1440);
    if (state.pullCollectionEnabled) {
      collectBtn.disabled = false;
      collectBtn.title = "";
      savePollingBtn.disabled = false;
      pollingInput.disabled = false;
    } else {
      collectBtn.disabled = true;
      collectBtn.title = "Collect disabled: this hub is ingest-only";
      savePollingBtn.disabled = true;
      pollingInput.disabled = true;
    }
  } catch {
    state.pullCollectionEnabled = true;
    state.activeServerWindowMinutes = 1440;
    collectBtn.disabled = false;
  }
}

function bindFilters() {
  qs("sourceFilter").addEventListener("change", async (e) => {
    state.source = e.target.value;
    state.historyPage = 1;
    await loadData();
  });
  qs("statusFilter").addEventListener("change", async (e) => {
    state.status = e.target.value;
    state.historyPage = 1;
    await loadData();
  });
  qs("searchFilter").addEventListener("input", async (e) => {
    state.search = e.target.value.trim();
    state.historyPage = 1;
    await loadData();
  });
  qs("historyPageSize").addEventListener("change", async (e) => {
    state.historyPageSize = Number(e.target.value || "30");
    state.historyPage = 1;
    await loadData();
  });
  qs("historyPrevBtn").addEventListener("click", async () => {
    if (state.historyPage <= 1) return;
    state.historyPage -= 1;
    await loadData();
  });
  qs("historyNextBtn").addEventListener("click", async () => {
    state.historyPage += 1;
    await loadData();
  });
  qs("savePollingBtn").addEventListener("click", savePolling);
  qs("refreshBtn").addEventListener("click", collectNow);
  qs("triggerAgentsBtn").addEventListener("click", triggerAgents);
}

async function bootstrap() {
  bindFilters();
  await loadAgentConfig();
  await loadSystemCapabilities();
  await loadPolling();
  await loadData();
  setInterval(loadData, 30000);
}

bootstrap().catch((err) => {
  setStatus(`Error: ${err.message}`, "error");
});
