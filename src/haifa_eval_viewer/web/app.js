const app = document.querySelector("#app");
const toast = document.querySelector("#toast");

const state = {
  level: "semantic",
  selectedEventId: null,
  errorWindow: true,
  activity: null,
  tasks: null,
  task: null,
  activityId: null,
  trialName: null,
  filterInputTimer: null,
};

const icons = {
  RUN: "●", CONTEXT: "◫", MODEL: "M", TOOL: "◆", WORKSPACE: "▣",
  POLICY: "P", JOURNAL: "J", COMPLETION: "★", VERIFIER: "✓", ERROR: "×", UNKNOWN: "?",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function fmt(value, fallback = "—") { return value === null || value === undefined || value === "" ? fallback : value; }
function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(Number(seconds));
  return total >= 60 ? `${Math.floor(total / 60)}m ${total % 60}s` : `${total}s`;
}
function fmtTime(milliseconds, first) {
  if (milliseconds === null || milliseconds === undefined || first === null || first === undefined) return "—";
  const delta = Math.max(0, milliseconds - first);
  const seconds = Math.floor(delta / 1000);
  return `+${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function badge(value) {
  const rendered = String(fmt(value, "UNAVAILABLE"));
  return `<span class="badge ${escapeHtml(rendered.toLowerCase())}">${escapeHtml(rendered)}</span>`;
}
function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2400);
}
async function api(path) {
  const response = await fetch(path, { cache: "no-store", headers: { Accept: "application/json" } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.message || `Request failed (${response.status})`);
  return body;
}

function route() {
  const raw = (location.hash || "#/").slice(1);
  const [pathname, query = ""] = raw.split("?");
  const segments = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  return { segments, query: Object.fromEntries(new URLSearchParams(query)) };
}
function hashForActivity(activityId, filters = {}) {
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();
  return `#/activity/${encodeURIComponent(activityId)}${query ? `?${query}` : ""}`;
}
function taskHref(activityId, trialName) {
  return `#/activity/${encodeURIComponent(activityId)}/task/${encodeURIComponent(trialName)}`;
}

async function render() {
  const current = route();
  app.innerHTML = '<div class="loading-panel">Reading private evaluation evidence…</div>';
  try {
    if (current.segments[0] === "activity" && current.segments[2] === "task") {
      await renderTask(current.segments[1], current.segments[3]);
    } else if (current.segments[0] === "activity") {
      await renderActivity(current.segments[1], current.query);
    } else {
      await renderCatalog(false);
    }
    app.focus({ preventScroll: true });
  } catch (error) {
    app.innerHTML = `<div class="error-panel"><strong>Evidence could not be loaded.</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function renderCatalog(force) {
  const catalog = await api(`/api/v1/activities${force ? "?refresh=true" : ""}`);
  app.innerHTML = `
    <section class="page-heading">
      <div><div class="eyebrow">Evaluation catalog</div><h1 class="page-title">Trace the evidence,<br>not just the score.</h1>
      <p class="page-subtitle">Completed Coding Agent evaluation archives appear here as they are added to configured data roots. Select an activity to drill from result taxonomy into its exact runtime trajectory.</p></div>
      <div class="toolbar"><button id="refresh-catalog" class="button primary">Refresh archives</button></div>
    </section>
    <div class="catalog-meta"><span>${catalog.activityCount} activities</span><span>Last scan ${escapeHtml(fmt(catalog.lastScannedAt))}</span></div>
    <section class="activity-grid" id="activity-grid"></section>`;
  document.querySelector("#refresh-catalog").addEventListener("click", async () => {
    await renderCatalog(true); showToast("Archive catalog refreshed");
  });
  const grid = document.querySelector("#activity-grid");
  if (!catalog.activities.length) {
    grid.innerHTML = '<div class="empty-state">No completed activity archive was found below the configured roots.</div>';
    return;
  }
  for (const activity of catalog.activities) {
    const counts = activity.counts || {};
    const card = document.createElement("a");
    card.className = "activity-card";
    card.href = hashForActivity(activity.activityId);
    card.innerHTML = `
      <div class="card-kicker">${escapeHtml(fmt(activity.archivedAtUtc, "Archived evaluation"))}</div>
      <h2>${escapeHtml(fmt(activity.evalId, activity.activityId))}</h2>
      <div class="run-id">${escapeHtml(fmt(activity.runId))}</div>
      <div class="card-counts">
        <div class="mini-stat"><strong>${escapeHtml(fmt(counts.planned))}</strong><span>Cases</span></div>
        <div class="mini-stat"><strong>${escapeHtml(fmt(counts.pass))}</strong><span>Pass</span></div>
        <div class="mini-stat"><strong>${escapeHtml(fmt(counts.fail))}</strong><span>Fail</span></div>
      </div>
      <div class="card-footer"><span>${escapeHtml(activity.archiveAlias)}</span>${badge(activity.dataQuality?.status)}</div>`;
    grid.append(card);
  }
}

async function renderActivity(activityId, filters) {
  const [activity, tasks] = await Promise.all([
    api(`/api/v1/activities/${encodeURIComponent(activityId)}`),
    api(`/api/v1/activities/${encodeURIComponent(activityId)}/tasks`),
  ]);
  state.activity = activity; state.tasks = tasks;
  const counts = activity.counts || {};
  const distributions = activity.distributions || { errors: {}, languages: {} };
  app.innerHTML = `
    <nav class="breadcrumb"><a href="#/">Evaluation catalog</a><span>›</span><span>${escapeHtml(activity.evalId)}</span></nav>
    <section class="page-heading"><div><div class="eyebrow">Evaluation activity</div><h1 class="page-title">${escapeHtml(activity.evalId)}</h1><p class="page-subtitle">${escapeHtml(activity.runId)} · ${escapeHtml(activity.archiveAlias)}</p></div><div class="toolbar"><button id="activity-refresh" class="button">Refresh</button></div></section>
    <section class="stat-grid">
      ${statCard("Cases", counts.planned)}
      ${statCard("Verifier pass", counts.pass, "success", "verifier", "PASS")}
      ${statCard("Verifier fail", counts.fail, "danger", "verifier", "FAIL")}
      ${statCard("Clean completion", counts.cleanCompletion, "success", "agent", "CLEAN")}
      ${statCard("Agent exceptions", counts.agentExceptions, "warning", "agent", "EXCEPTION")}
      ${statCard("Data quality", activity.dataQuality?.status)}
    </section>
    <section class="overview-split">
      <div class="panel"><h3>Failure taxonomy</h3><div id="error-taxonomy" class="taxonomy-list"></div></div>
      <div class="panel"><h3>Languages</h3><div id="language-taxonomy" class="taxonomy-list"></div></div>
    </section>
    <section class="panel"><h3>Cases</h3><div class="filters" id="filters"></div><div class="chips" id="chips"></div><div id="task-table"></div></section>`;
  document.querySelector("#activity-refresh").addEventListener("click", async () => {
    await api("/api/v1/activities?refresh=true"); showToast("Activity catalog refreshed"); await renderActivity(activityId, filters);
  });
  document.querySelectorAll("[data-filter-key]").forEach(element => element.addEventListener("click", () => {
    updateFilter(activityId, filters, element.dataset.filterKey, element.dataset.filterValue);
  }));
  renderTaxonomies(activityId, filters, distributions);
  renderFilters(activityId, filters, tasks.facets || {});
  renderTaskTable(activityId, applyClientFilters(tasks.tasks, filters), tasks.total);
}

function statCard(label, value, tone = "", filterKey = "", filterValue = "") {
  if (filterKey) {
    return `<button class="stat-card ${tone} clickable" data-filter-key="${escapeHtml(filterKey)}" data-filter-value="${escapeHtml(filterValue)}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(fmt(value))}</strong></button>`;
  }
  return `<div class="stat-card ${tone}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(fmt(value))}</strong></div>`;
}
function renderTaxonomies(activityId, filters, distributions) {
  const errors = Object.entries(distributions.errors || {}).filter(([name]) => name !== "NONE").sort((a, b) => b[1] - a[1]);
  const maxError = Math.max(1, ...errors.map(([, count]) => count));
  document.querySelector("#error-taxonomy").innerHTML = errors.length ? errors.map(([name, count]) => `
    <button class="taxonomy-row" data-error="${escapeHtml(name)}"><code>${escapeHtml(name)}</code><span class="bar"><i style="width:${count / maxError * 100}%"></i></span><em>${count}</em></button>`).join("") : '<span class="muted">No classified errors.</span>';
  document.querySelectorAll("[data-error]").forEach(element => element.addEventListener("click", () => updateFilter(activityId, filters, "errorCode", element.dataset.error)));
  const languages = Object.entries(distributions.languages || {});
  document.querySelector("#language-taxonomy").innerHTML = languages.map(([name, value]) => `
    <button class="taxonomy-row" data-language="${escapeHtml(name)}"><code>${escapeHtml(name)}</code><span class="bar"><i style="width:${value.total ? value.pass / value.total * 100 : 0}%"></i></span><em>${value.pass}/${value.total}</em></button>`).join("");
  document.querySelectorAll("[data-language]").forEach(element => element.addEventListener("click", () => updateFilter(activityId, filters, "language", element.dataset.language)));
}
function renderFilters(activityId, filters, facets) {
  const container = document.querySelector("#filters");
  const definitions = [
    ["language", "Language"], ["verifier", "Verifier", "verifierStatus"], ["agent", "Agent", "agentStatus"],
    ["errorCode", "Error code"], ["model", "Model"], ["provider", "Provider"],
  ];
  for (const [queryKey, label, facetKey = queryKey] of definitions) {
    const select = document.createElement("select"); select.setAttribute("aria-label", label);
    select.innerHTML = `<option value="">${escapeHtml(label)}: All</option>`;
    for (const value of Object.keys(facets[facetKey] || {}).sort()) {
      const option = document.createElement("option"); option.value = value; option.textContent = `${value} (${facets[facetKey][value]})`; option.selected = filters[queryKey] === value; select.append(option);
    }
    select.addEventListener("change", () => updateFilter(activityId, filters, queryKey, select.value));
    container.append(select);
  }
  for (const [queryKey, label] of [
    ["durationMin", "Min duration (s)"], ["durationMax", "Max duration (s)"], ["retryMin", "Min retries"],
  ]) {
    const input = document.createElement("input"); input.type = "number"; input.min = "0"; input.step = queryKey === "retryMin" ? "1" : "any";
    input.placeholder = label; input.setAttribute("aria-label", label); input.value = filters[queryKey] || "";
    input.addEventListener("input", () => {
      clearTimeout(state.filterInputTimer);
      state.filterInputTimer = setTimeout(() => updateFilter(activityId, filters, queryKey, input.value), 350);
    });
    container.append(input);
  }
  const sort = document.createElement("select"); sort.setAttribute("aria-label", "Sort cases");
  for (const [value, label] of [["task", "Sort: Task"], ["duration-desc", "Sort: Duration"], ["modelCalls-desc", "Sort: Model turns"], ["toolCalls-desc", "Sort: Tools"], ["retries-desc", "Sort: Retries"]]) {
    const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = (filters.sort || "task") === value; sort.append(option);
  }
  sort.addEventListener("change", () => updateFilter(activityId, filters, "sort", sort.value === "task" ? "" : sort.value)); container.append(sort);
  const summary = document.createElement("span"); summary.className = "filter-summary"; summary.id = "filter-summary"; container.append(summary);
  const chips = document.querySelector("#chips");
  for (const [key, value] of Object.entries(filters)) {
    if (!value) continue;
    const chip = document.createElement("button"); chip.className = "chip"; chip.textContent = `${key}: ${value} ×`; chip.addEventListener("click", () => updateFilter(activityId, filters, key, "")); chips.append(chip);
  }
  if (Object.values(filters).some(Boolean)) {
    const clear = document.createElement("button"); clear.className = "chip"; clear.textContent = "Clear all"; clear.addEventListener("click", () => { location.hash = hashForActivity(activityId); }); chips.append(clear);
  }
}
function updateFilter(activityId, current, key, value) {
  const next = { ...current }; if (value) next[key] = value; else delete next[key]; location.hash = hashForActivity(activityId, next);
}
function applyClientFilters(tasks, filters) {
  const mapping = { language: "language", verifier: "verifierStatus", agent: "agentStatus", model: "model", provider: "provider" };
  const selected = tasks.filter(task => {
    const ordinary = Object.entries(mapping).every(([queryKey, field]) => !filters[queryKey] || String(task[field] ?? "UNAVAILABLE").toLowerCase() === filters[queryKey].toLowerCase());
    const diagnostic = !filters.errorCode || String(task.errorCode || task.errorType || "UNAVAILABLE").toLowerCase() === filters.errorCode.toLowerCase();
    const duration = task.durationSeconds;
    const minimumDuration = !filters.durationMin || (duration !== null && duration >= Number(filters.durationMin));
    const maximumDuration = !filters.durationMax || (duration !== null && duration <= Number(filters.durationMax));
    const minimumRetries = !filters.retryMin || (task.runtimeRetries !== null && task.runtimeRetries >= Number(filters.retryMin));
    return ordinary && diagnostic && minimumDuration && maximumDuration && minimumRetries;
  });
  const sorters = {
    "duration-desc": task => task.durationSeconds ?? -1,
    "modelCalls-desc": task => task.modelCalls ?? -1,
    "toolCalls-desc": task => task.toolCalls ?? -1,
    "retries-desc": task => task.runtimeRetries ?? -1,
  };
  if (sorters[filters.sort]) selected.sort((left, right) => sorters[filters.sort](right) - sorters[filters.sort](left));
  return selected;
}
function renderTaskTable(activityId, tasks, total) {
  document.querySelector("#filter-summary").textContent = `${tasks.length} / ${total} cases`;
  const host = document.querySelector("#task-table");
  if (!tasks.length) { host.innerHTML = '<div class="empty-state">No cases match this filter combination.</div>'; return; }
  host.innerHTML = `<div class="table-shell"><table class="task-table"><thead><tr><th>Task</th><th>Lang</th><th>Verify</th><th>Agent</th><th>Duration</th><th>Turns</th><th>Tools</th><th>Provider</th><th>Error</th></tr></thead><tbody>${tasks.map(task => `
    <tr data-trial="${escapeHtml(task.trialName)}"><td class="task-name">${escapeHtml(task.taskId)}</td><td>${escapeHtml(task.language)}</td><td>${badge(task.verifierStatus)}</td><td>${badge(task.agentStatus)}</td><td>${fmtDuration(task.durationSeconds)}</td><td>${escapeHtml(fmt(task.modelCalls))}</td><td>${escapeHtml(fmt(task.toolCalls))}</td><td>${escapeHtml(fmt(task.provider))}</td><td class="task-name">${escapeHtml(fmt(task.errorCode || task.errorType))}</td></tr>`).join("")}</tbody></table></div>`;
  host.querySelectorAll("[data-trial]").forEach(row => row.addEventListener("click", () => { location.hash = taskHref(activityId, row.dataset.trial); }));
}

async function renderTask(activityId, trialName) {
  const task = await api(`/api/v1/activities/${encodeURIComponent(activityId)}/tasks/${encodeURIComponent(trialName)}`);
  state.task = task; state.activityId = activityId; state.trialName = trialName; state.level = "semantic"; state.selectedEventId = task.focusEventId || task.trajectory.events[0]?.eventId || null; state.errorWindow = Boolean(task.focusEventId);
  app.innerHTML = `
    <nav class="breadcrumb"><a href="#/">Evaluation catalog</a><span>›</span><a href="${hashForActivity(activityId)}">${escapeHtml(activityId)}</a><span>›</span><span>${escapeHtml(task.taskId)}</span></nav>
    <section class="page-heading"><div><div class="eyebrow">Primary attempt trajectory</div><h1 class="page-title">${escapeHtml(task.taskId)}</h1><p class="page-subtitle">${escapeHtml(task.trialName)} · ${fmtDuration(task.durationSeconds)} · ${task.trajectoryEventCount} normalized events</p></div><div>${badge(task.dataQuality?.status)}</div></section>
    <section class="status-matrix">
      ${statusCell("Verifier", task.verifierStatus, `reward ${fmt(task.reward)}`)}
      ${statusCell("Agent run", task.agentStatus, `${fmt(task.agentRunStatus)} · exit ${fmt(task.exitCode)}`)}
      ${statusCell("Workspace", task.workspaceModified === true ? "MODIFIED" : task.workspaceModified === false ? "UNCHANGED" : "UNAVAILABLE", `${fmt(task.workspaceChangeCount)} write operations`)}
      ${statusCell("Last verification", task.lastVerification, fmt(task.lastVerificationSource))}
      ${statusCell("Model / Provider", fmt(task.model), fmt(task.provider))}
      ${statusCell("Provider errors", fmt(task.providerErrors), `${fmt(task.runtimeRetries)} runtime retries`)}
    </section>
    <section class="trajectory-shell"><div class="trajectory-column"><div class="trajectory-toolbar" id="trajectory-toolbar"></div><div class="event-list" id="event-list"></div></div><aside class="event-detail" id="event-detail"></aside></section>`;
  renderTrajectory();
}
function statusCell(label, value, detail) { return `<div class="status-cell"><label>${escapeHtml(label)}</label><strong>${escapeHtml(fmt(value))}</strong><small>${escapeHtml(fmt(detail))}</small></div>`; }

function renderTrajectory() {
  const task = state.task; if (!task) return;
  const all = task.trajectory.events || [];
  let visible = all;
  const focusIndex = all.findIndex(event => event.eventId === task.focusEventId);
  if (state.errorWindow && focusIndex >= 0) visible = all.slice(Math.max(0, focusIndex - 5), focusIndex + 6);
  const rendered = state.level === "semantic" ? semanticGroups(visible) : visible;
  const toolbar = document.querySelector("#trajectory-toolbar");
  toolbar.innerHTML = ["semantic", "operations", "raw"].map(level => `<button class="zoom-button ${state.level === level ? "active" : ""}" data-level="${level}">${level[0].toUpperCase() + level.slice(1)}</button>`).join("") +
    (task.focusEventId ? `<button class="zoom-button ${state.errorWindow ? "active" : ""}" id="error-window">${state.errorWindow ? "±5 events" : "Show error window"}</button>` : "");
  toolbar.querySelectorAll("[data-level]").forEach(button => button.addEventListener("click", () => { state.level = button.dataset.level; renderTrajectory(); }));
  toolbar.querySelector("#error-window")?.addEventListener("click", () => { state.errorWindow = !state.errorWindow; renderTrajectory(); });
  const firstTime = all.find(event => event.occurredAtMs !== null)?.occurredAtMs ?? null;
  const list = document.querySelector("#event-list");
  list.innerHTML = rendered.map(event => eventMarkup(event, firstTime, task.focusEventId)).join("") || '<div class="empty-state">No trajectory events are available.</div>';
  list.querySelectorAll("[data-event]").forEach(button => button.addEventListener("click", () => { state.selectedEventId = button.dataset.event; renderTrajectory(); }));
  const selected = rendered.find(event => event.eventId === state.selectedEventId || event.members?.includes(state.selectedEventId)) || rendered[0];
  if (selected && !state.selectedEventId) state.selectedEventId = selected.eventId;
  renderEventDetail(selected);
  requestAnimationFrame(() => list.querySelector(".error-anchor")?.scrollIntoView({ block: "center" }));
}
function semanticGroups(events) {
  const groups = [];
  for (const event of events) {
    const previous = groups.at(-1);
    if (previous && previous.semanticPhase === event.semanticPhase && event.semanticPhase !== "ERROR") {
      previous.count += 1; previous.members.push(event.eventId); previous.summary = `${previous.semanticPhase.replaceAll("_", " ")} · ${previous.count} events`;
      previous.status = previous.status === "FAILURE" || event.status === "FAILURE" ? "FAILURE" : event.status === "RUNNING" ? "RUNNING" : previous.status;
      previous.occurredAtEndMs = event.occurredAtMs;
    } else {
      groups.push({ ...event, eventId: `semantic-${event.eventId}`, members: [event.eventId], count: 1, summary: event.semanticPhase.replaceAll("_", " "), operation: event.semanticPhase });
    }
  }
  return groups;
}
function eventMarkup(event, firstTime, focusEventId) {
  const containsFocus = event.eventId === focusEventId || event.members?.includes(focusEventId);
  const selected = event.eventId === state.selectedEventId || event.members?.includes(state.selectedEventId);
  return `<button class="event-item ${containsFocus ? "error-anchor" : ""} ${selected ? "selected" : ""}" data-event="${escapeHtml(event.members?.[0] || event.eventId)}"><span class="event-time">${fmtTime(event.occurredAtMs, firstTime)}</span><span class="event-icon">${icons[event.category] || "?"}</span><span class="event-main"><strong>${escapeHtml(event.operation.replaceAll("_", " "))}</strong><span>${escapeHtml(event.summary)} · ${escapeHtml(event.source)}</span></span></button>`;
}
function renderEventDetail(event) {
  const host = document.querySelector("#event-detail");
  if (!event) { host.innerHTML = '<div class="empty-state">Select an event.</div>'; return; }
  const projection = { type: event.type, operation: event.operation, status: event.status, errorCode: event.errorCode, metadata: event.metadata };
  const rawMode = state.level === "raw";
  host.innerHTML = `<div class="detail-kicker">${escapeHtml(event.category)} · ${escapeHtml(event.source)}</div><h2>${escapeHtml(event.operation)}</h2><p class="page-subtitle">${escapeHtml(event.summary)}</p>
    <div class="detail-grid">${detailField("Status", event.status)}${detailField("Error code", event.errorCode)}${detailField("Run", event.runId)}${detailField("Attempt", event.attemptId)}${detailField("Step", event.stepId)}${detailField("Tool call", event.toolCallId)}${detailField("Duration", event.durationMs === null ? null : `${event.durationMs} ms`)}${detailField("Source", event.sourceRef)}</div>
    <h3>${rawMode ? "Complete source evidence" : "Event projection"}</h3><pre class="raw-box">${escapeHtml(rawMode ? "Loading complete raw evidence…" : JSON.stringify(projection, null, 2))}</pre>${rawMode ? '<div class="privacy-note">Loaded on demand from the loopback-only local archive. This response is not cached.</div>' : ""}`;
  if (rawMode) loadRawEvent(event, host);
}
async function loadRawEvent(event, host) {
  const eventId = event.members?.[0] || event.eventId;
  const rawBox = host.querySelector(".raw-box");
  try {
    const evidence = await api(`/api/v1/activities/${encodeURIComponent(state.activityId)}/tasks/${encodeURIComponent(state.trialName)}/events/${encodeURIComponent(eventId)}/raw`);
    if (state.level === "raw" && state.selectedEventId === eventId && rawBox.isConnected) rawBox.textContent = JSON.stringify(evidence.raw, null, 2);
  } catch (error) {
    if (rawBox.isConnected) rawBox.textContent = `Raw evidence could not be loaded: ${error.message}`;
  }
}
function detailField(label, value) { return `<div class="detail-field"><label>${escapeHtml(label)}</label><div>${escapeHtml(fmt(value))}</div></div>`; }

window.addEventListener("hashchange", render);
render();
setInterval(() => {
  const current = route();
  if (!current.segments.length) api("/api/v1/activities?refresh=true").catch(() => {});
}, 30000);
