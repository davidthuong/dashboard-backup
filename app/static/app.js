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

// The hub stores every time in UTC; SQLite hands them back without an offset, so a bare
// "2026-10-08T13:04:06" means UTC, not the browser's local time.
function parseApiDateMs(value) {
  if (!value) return Number.NaN;
  const text = String(value).trim();
  if (!text) return Number.NaN;
  const hasTz = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(text);
  return Date.parse(hasTz ? text : text.replace(" ", "T") + "Z");
}

function fmtDate(value) {
  if (!value) return "-";
  const ms = parseApiDateMs(value);
  if (Number.isNaN(ms)) return String(value);
  return new Date(ms).toLocaleString();
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
      <td>${esc(fmtDate(item.collected_at))}</td>
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
      <td>${esc(fmtDate(item.collected_at))}</td>
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
    const collectedAtMs = parseApiDateMs(item?.collected_at);
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
    setStatus("No agents yet: use Add node (or AGENT_TRIGGER_ENABLED=true for legacy nodes).", "error");
    return;
  }
  setStatus("Triggering agents...", "ok");
  try {
    const data = await api("/api/agents/trigger-all", { method: "POST" });
    if (!data) return;
    const results = data.results || [];
    const queued = results.filter((r) => r?.response?.queued).length;
    const failedCount = Number(data.failed_count || 0);
    const direct = results.length - queued - failedCount;
    const parts = [];
    if (direct) parts.push(`${direct} ran`);
    if (queued) parts.push(`${queued} queued (agents pick it up within ~1 min)`);
    if (failedCount) parts.push(`${failedCount} failed`);
    setStatus(`Agents: ${parts.join(", ") || "nothing to do"}`, failedCount > 0 ? "error" : "ok");
    await Promise.all([loadData(), loadAgents()]);
  } catch (err) {
    setStatus(`Trigger failed: ${err.message}`, "error");
  }
}

function agentStatusChip(node) {
  if (!node.enabled) return '<span class="chip unknown">disabled</span>';
  if (node.online) return '<span class="chip success">online</span>';
  return '<span class="chip failed">offline</span>';
}

function renderAgents(data) {
  const tbody = qs("agentsTable").querySelector("tbody");
  tbody.innerHTML = "";
  const nodes = data.nodes || [];
  const legacy = data.legacy || [];

  const schedule = data.auto_trigger_times ? `auto run at ${data.auto_trigger_times}` : "manual run only";
  const latest = data.latest_agent_version ? `latest agent v${data.latest_agent_version}` : "";
  qs("agentsMeta").textContent = [`${nodes.length} agent(s)`, legacy.length ? `${legacy.length} legacy` : "", schedule, latest]
    .filter(Boolean)
    .join(" | ");

  if (!nodes.length && !legacy.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="8">No agents yet. Click "Add node" to install one.</td>';
    tbody.appendChild(tr);
    return;
  }

  for (const node of nodes) {
    const jobs = node.jobs || [];
    const pendingSince = node.run_claimed_at || node.run_requested_at;
    const pending = node.run_pending
      ? `<div class="muted small">${node.run_claimed_at ? "running since" : "run queued"} ${esc(fmtDate(pendingSince))}</div>`
      : "";
    const outdated = node.outdated ? ' <span class="chip warning">update</span>' : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${esc(node.name)}</strong></td>
      <td>${agentStatusChip(node)}${pending}</td>
      <td>${esc(fmtDate(node.last_seen_at))}</td>
      <td>${esc(node.hostname || "-")}<div class="muted small">${esc(node.remote_ip || "")}</div></td>
      <td title="${esc(jobs.map((job) => `${job.job_name}: ${job.log_path}`).join("\n"))}">${esc(jobs.map((job) => job.job_name).join(", ") || "-")}</td>
      <td>${esc(node.last_run_summary || "-")}<div class="muted small">${esc(node.last_run_at ? fmtDate(node.last_run_at) : "")}</div></td>
      <td title="${esc(node.os_info || "")}">v${esc(node.agent_version || "?")}${outdated}</td>
      <td class="row-actions">
        <button class="secondary small" data-action="run" data-name="${esc(node.name)}" ${node.enabled ? "" : "disabled"}>Run</button>
        <button class="secondary small" data-action="toggle" data-id="${node.id}" data-enabled="${node.enabled ? "1" : "0"}">${node.enabled ? "Disable" : "Enable"}</button>
        <button class="danger small" data-action="delete" data-id="${node.id}" data-name="${esc(node.name)}">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  }

  for (const node of legacy) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${esc(node.name)}</strong></td>
      <td><span class="chip unknown">legacy push</span></td>
      <td>-</td>
      <td>${esc(node.url)}</td>
      <td>${esc(node.action)}</td>
      <td>-</td>
      <td>receiver</td>
      <td class="row-actions">
        <button class="secondary small" data-action="run" data-name="${esc(node.name)}">Run</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

async function loadAgents() {
  const btn = qs("triggerAgentsBtn");
  try {
    const data = await api("/api/agents");
    if (!data) return;
    state.agentsEnabled = Number(data.triggerable || 0) > 0;
    btn.disabled = !state.agentsEnabled;
    btn.title = state.agentsEnabled ? `Agents: ${Number(data.triggerable || 0)}` : "No enabled agents: use Add node";
    renderAgents(data);
  } catch (err) {
    state.agentsEnabled = false;
    btn.disabled = true;
    btn.title = "Cannot load agents";
    setStatus(`Cannot load agents: ${err.message}`, "error");
  }
}

async function onAgentAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, name, id } = button.dataset;
  try {
    if (action === "run") {
      const data = await api(`/api/agents/trigger/${encodeURIComponent(name)}`, { method: "POST" });
      if (!data) return;
      const response = data.result?.response || {};
      if (response.queued) {
        const note = response.online ? "picked up within ~1 min" : "agent is offline, runs when it is back";
        setStatus(`Run queued for ${name} - ${note}`, response.online ? "ok" : "error");
      } else {
        const detail = `HTTP ${data.result?.status_code}, exit ${data.result?.exit_code}`;
        setStatus(`${name}: ${data.ok ? "ran" : "failed"} (${detail})`, data.ok ? "ok" : "error");
      }
    } else if (action === "toggle") {
      const enable = button.dataset.enabled !== "1";
      await api(`/api/agents/nodes/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: enable }) });
      setStatus(`Agent ${enable ? "enabled" : "disabled"}`, "ok");
    } else if (action === "delete") {
      const question = `Delete agent "${name}"? Its job history stays. Uninstall the agent on the server too, or it keeps failing to poll.`;
      if (!window.confirm(question)) return;
      await api(`/api/agents/nodes/${id}`, { method: "DELETE" });
      setStatus(`Agent ${name} deleted`, "ok");
    }
    await loadAgents();
  } catch (err) {
    setStatus(`Agent action failed: ${err.message}`, "error");
  }
}

function toggleEnrollPanel() {
  const panel = qs("enrollPanel");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) qs("enrollName").focus();
}

async function createEnrollment() {
  try {
    const data = await api("/api/agents/enrollments", {
      method: "POST",
      body: JSON.stringify({ name: qs("enrollName").value.trim() }),
    });
    if (!data) return;
    qs("enrollCommand").value = data.command;
    qs("enrollExpires").textContent = fmtDate(data.expires_at);
    qs("enrollCert").textContent = data.cert_sha1 ? `Hub certificate SHA1 ${data.cert_sha1}` : "";
    qs("enrollResult").hidden = false;
    setStatus(`Install command created${data.node_name ? ` for ${data.node_name}` : ""}`, "ok");
  } catch (err) {
    setStatus(`Cannot create install command: ${err.message}`, "error");
  }
}

async function copyEnrollCommand() {
  const box = qs("enrollCommand");
  try {
    await navigator.clipboard.writeText(box.value);
  } catch {
    box.select();
    document.execCommand("copy");
  }
  setStatus("Install command copied", "ok");
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
  qs("addNodeBtn").addEventListener("click", toggleEnrollPanel);
  qs("createEnrollBtn").addEventListener("click", createEnrollment);
  qs("copyEnrollBtn").addEventListener("click", copyEnrollCommand);
  qs("agentsTable").addEventListener("click", onAgentAction);
}

async function bootstrap() {
  bindFilters();
  await loadAgents();
  await loadSystemCapabilities();
  await loadPolling();
  await loadData();
  setInterval(() => {
    loadData().catch((err) => setStatus(`Error: ${err.message}`, "error"));
    loadAgents();
  }, 30000);
}

bootstrap().catch((err) => {
  setStatus(`Error: ${err.message}`, "error");
});
