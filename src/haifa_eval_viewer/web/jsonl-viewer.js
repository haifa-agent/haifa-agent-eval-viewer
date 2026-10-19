"use strict";

const PAGE_SIZE = 300;
const { HAIFA_PROFILE, isObject, parseJsonl } = globalThis.HaifaJsonlNormalizer;

const state = {
  records: [],
  filtered: [],
  selectedId: null,
  activeTab: "timeline",
  displayLimit: PAGE_SIZE,
  sourceName: "",
};

const elements = {
  input: document.querySelector("#jsonl-input"),
  inputMeta: document.querySelector("#input-meta"),
  inputPanel: document.querySelector(".input-panel"),
  fileInput: document.querySelector("#file-input"),
  results: document.querySelector("#results"),
  parseStatus: document.querySelector("#parse-status"),
  sourceProfile: document.querySelector("#source-profile"),
  typeFilter: document.querySelector("#type-filter"),
  toolFilter: document.querySelector("#tool-filter"),
  phaseFilter: document.querySelector("#phase-filter"),
  iterationFilter: document.querySelector("#iteration-filter"),
  searchFilter: document.querySelector("#search-filter"),
  errorFilter: document.querySelector("#error-filter"),
  filterSummary: document.querySelector("#filter-summary"),
  eventList: document.querySelector("#event-list"),
  eventDetail: document.querySelector("#event-detail"),
  loadMore: document.querySelector("#load-more"),
  typeDistribution: document.querySelector("#type-distribution"),
  toolDistribution: document.querySelector("#tool-distribution"),
  fieldTable: document.querySelector("#field-table"),
  treeLine: document.querySelector("#tree-line"),
  treeTitle: document.querySelector("#tree-title"),
  jsonTree: document.querySelector("#json-tree"),
  rawLines: document.querySelector("#raw-lines"),
  toast: document.querySelector("#toast"),
};

const sampleBase = {
  recordType: "trace.event", schema: "haifa-runtime-trace", schemaVersion: 1,
  producer: "haifa-agent-cli", producerVersion: "0.1.0-SNAPSHOT", streamId: "ts_Q83mKMf7pV0r2XaN7cFwYg",
  traceId: "tr_zP6x5aJdC1nW8kRt4uHqBg", runId: "run-42", attemptId: "attempt-1", sessionId: "session-1", workerId: "worker-1",
};
const sampleLines = [
  { ...sampleBase, sequence: 1, timestamp: "2026-08-25T10:01:02.000Z", iteration: 1, scope: "ITERATION", phase: "BEFORE_CONTEXT_BUILD", operation: "loop.iteration", status: "ACTIVE", attributes: { iteration: 1 } },
  { ...sampleBase, sequence: 2, timestamp: "2026-08-25T10:01:02.100Z", iteration: 1, scope: "ITERATION", phase: "AFTER_CONTEXT_BUILD", operation: "context.built", status: "SUCCESS", attributes: { estimatedInputTokens: 1200, selectedItems: 18, compacted: false } },
  { ...sampleBase, sequence: 3, timestamp: "2026-08-25T10:01:03.000Z", stepId: "step-model-1", iteration: 1, scope: "STEP", phase: "AFTER_MODEL_CALL", operation: "model.invoke", status: "SUCCESS", attributes: { providerId: "example-provider", modelId: "example-model", inputTokens: 1200, outputTokens: 80, finishReason: "tool_calls" } },
  { ...sampleBase, sequence: 4, timestamp: "2026-08-25T10:01:03.100Z", stepId: "step-tool-1", toolCallId: "call-17", iteration: 1, scope: "TOOL_CALL", phase: "BEFORE_DECISION_EXECUTION", operation: "tool.execute", status: "STARTED", attributes: { toolName: "file.read", toolVersion: "1", providerId: "local" } },
  { ...sampleBase, sequence: 5, timestamp: "2026-08-25T10:01:03.420Z", stepId: "step-tool-1", toolCallId: "call-17", iteration: 1, scope: "TOOL_CALL", phase: "AFTER_DECISION_EXECUTION", operation: "tool.persisted", status: "FAILURE", attributes: { toolName: "file.read", toolVersion: "1", providerId: "local", successful: false, failureCode: "TOOL_BUSINESS_FAILURE", retryable: false, truncated: true, externalizationRequired: false, externalized: false } },
  { recordType: "trace.event", schema: "haifa-runtime-trace", schemaVersion: 1, producer: "haifa-agent-cli", producerVersion: "0.1.0-SNAPSHOT", streamId: sampleBase.streamId, sequence: 6, timestamp: "2026-08-25T10:01:04.000Z", scope: "STREAM", phase: "NONE", operation: "stream.completed", status: "SUCCESS", attributes: { writtenEvents: 6, droppedEvents: 0, cleanClose: true } },
];

function countBy(records, field, includeEmpty = false) {
  const counts = new Map();
  for (const record of records) {
    const rawValue = record[field];
    const value = rawValue !== null && rawValue !== undefined && rawValue !== ""
      ? String(rawValue)
      : includeEmpty ? "unknown" : "";
    if (!value) continue;
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts.entries()].sort((left, right) => right[1] - left[1] || String(left[0]).localeCompare(String(right[0])));
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function formatDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || milliseconds < 0) return "—";
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function firstTimestamp(records = state.records) {
  const values = records.map(record => record.timestampMs).filter(value => value !== null);
  return values.length ? Math.min(...values) : null;
}

function formatEventTime(record, first) {
  if (record.timestampMs === null || first === null) return `L${record.lineNumber}`;
  const seconds = Math.max(0, Math.floor((record.timestampMs - first) / 1000));
  return `+${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function updateInputMeta() {
  const text = elements.input.value;
  const lines = text ? text.split(/\r?\n/).length : 0;
  const source = state.sourceName ? `${state.sourceName} · ` : "";
  elements.inputMeta.textContent = text ? `${source}${formatNumber(lines)} 行 · ${formatNumber(text.length)} 字符` : "等待输入";
}

function populateSelect(select, label, entries) {
  const previous = select.value;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = `${label}：全部`;
  select.append(all);
  for (const [value, count] of entries) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = `${value} (${count})`;
    select.append(option);
  }
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function parseCurrentInput() {
  const text = elements.input.value;
  state.records = parseJsonl(text);
  state.displayLimit = PAGE_SIZE;
  state.selectedId = state.records.find(record => record.valid)?.id || state.records[0]?.id || null;
  elements.results.hidden = false;
  populateSelect(elements.typeFilter, "类型", countBy(state.records, "type"));
  populateSelect(elements.toolFilter, "Tool", countBy(state.records, "tool"));
  populateSelect(elements.phaseFilter, "Phase", countBy(state.records, "phase"));
  populateSelect(elements.iterationFilter, "Iteration", countBy(state.records, "iteration"));
  updateStats();
  applyFilters();
  elements.results.scrollIntoView({ behavior: "smooth", block: "start" });
  showToast(state.records.length ? `已解析 ${formatNumber(state.records.length)} 条记录` : "没有可解析的非空行");
}

function updateStats() {
  const valid = state.records.filter(record => record.valid);
  const invalid = state.records.length - valid.length;
  const timestamps = valid.map(record => record.timestampMs).filter(value => value !== null);
  const duration = timestamps.length > 1 ? Math.max(...timestamps) - Math.min(...timestamps) : null;
  const toolCalls = valid.filter(record => record.family === "tool" && record.role === "call").length;
  const modelCalls = valid.filter(record => record.type === "model.invoke").length;
  const iterations = new Set(valid.filter(record => record.profile === HAIFA_PROFILE && record.type === "loop.iteration").map(record => record.iteration)).size;
  const failures = valid.filter(record => record.isError).length;
  const integrityIssues = valid.filter(record => record.issues.length > 0).length;
  const profiles = [...new Set(valid.map(record => record.profile))];
  document.querySelector("#stat-lines").textContent = formatNumber(state.records.length);
  document.querySelector("#stat-valid").textContent = formatNumber(valid.length);
  document.querySelector("#stat-invalid").textContent = formatNumber(invalid);
  document.querySelector("#stat-types").textContent = formatNumber(new Set(valid.map(record => record.type)).size);
  document.querySelector("#stat-iterations").textContent = formatNumber(iterations);
  document.querySelector("#stat-models").textContent = formatNumber(modelCalls);
  document.querySelector("#stat-tools").textContent = formatNumber(toolCalls);
  document.querySelector("#stat-failures").textContent = formatNumber(failures);
  document.querySelector("#stat-duration").textContent = formatDuration(duration);
  elements.sourceProfile.textContent = profiles.length === 1 ? profiles[0] : profiles.length > 1 ? "Mixed JSONL" : "Generic JSONL";
  elements.sourceProfile.classList.toggle("haifa", profiles.length === 1 && profiles[0] === HAIFA_PROFILE);
  elements.parseStatus.textContent = invalid ? "PARTIAL" : integrityIssues ? "TRACE_INCOMPLETE" : "READY";
  elements.parseStatus.classList.toggle("warning", invalid > 0 || integrityIssues > 0);
}

function applyFilters() {
  const type = elements.typeFilter.value;
  const tool = elements.toolFilter.value;
  const phase = elements.phaseFilter.value;
  const iteration = elements.iterationFilter.value;
  const search = elements.searchFilter.value.trim().toLowerCase();
  const errorsOnly = elements.errorFilter.checked;
  state.filtered = state.records.filter(record => {
    if (type && record.type !== type) return false;
    if (tool && record.tool !== tool) return false;
    if (phase && record.phase !== phase) return false;
    if (iteration && String(record.iteration) !== iteration) return false;
    if (search && !record.searchText.includes(search)) return false;
    return !errorsOnly || record.isError;
  });
  if (!state.filtered.some(record => record.id === state.selectedId)) {
    state.selectedId = state.filtered.find(record => record.valid)?.id || state.filtered[0]?.id || null;
  }
  state.displayLimit = Math.max(PAGE_SIZE, Math.min(state.displayLimit, state.filtered.length));
  elements.filterSummary.textContent = `${formatNumber(state.filtered.length)} / ${formatNumber(state.records.length)} 行`;
  renderTimeline();
  renderFields();
  renderTree();
  renderRaw();
}

function selectedRecord() {
  return state.records.find(record => record.id === state.selectedId) || null;
}

function eventIcon(record) {
  if (!record.valid) return "×";
  if (record.isError) return "!";
  if (record.family === "tool") return "◆";
  if (record.family === "model") return "M";
  if (record.family === "verifier") return "✓";
  return "●";
}

function renderTimeline() {
  elements.eventList.replaceChildren();
  if (!state.filtered.length) {
    elements.eventList.append(emptyState("没有记录符合当前筛选条件。"));
    renderDetail(null);
    elements.loadMore.hidden = true;
    return;
  }
  const first = firstTimestamp();
  const visible = state.filtered.slice(0, state.displayLimit);
  const fragment = document.createDocumentFragment();
  for (const record of visible) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `event-card${record.id === state.selectedId ? " selected" : ""}${record.isError ? " error" : ""}${record.valid ? "" : " invalid"}`;
    button.dataset.recordId = record.id;
    const line = document.createElement("span");
    line.className = "event-line";
    line.textContent = formatEventTime(record, first);
    if (record.timestampMs !== null) line.title = new Date(record.timestampMs).toISOString();
    const icon = document.createElement("span");
    icon.className = "event-icon";
    icon.textContent = eventIcon(record);
    const main = document.createElement("span");
    main.className = "event-main";
    const topline = document.createElement("span");
    topline.className = "event-topline";
    const strong = document.createElement("strong");
    strong.textContent = record.type;
    topline.append(strong);
    if (record.tool) topline.append(tag(record.tool, "tool"));
    if (record.iteration !== null) topline.append(tag(`i${record.iteration}`, "iteration"));
    if (record.phase) topline.append(tag(record.phase, "phase"));
    if (record.status) topline.append(tag(record.status));
    if (record.issues.length) topline.append(tag(record.issues.join(", "), "warning"));
    const summary = document.createElement("span");
    summary.textContent = record.valid ? record.summary || "没有可推断的摘要" : record.parseError;
    main.append(topline, summary);
    button.append(line, icon, main);
    button.addEventListener("click", () => selectRecord(record.id));
    fragment.append(button);
  }
  elements.eventList.append(fragment);
  elements.loadMore.hidden = state.displayLimit >= state.filtered.length;
  if (!elements.loadMore.hidden) elements.loadMore.textContent = `再显示 ${formatNumber(Math.min(PAGE_SIZE, state.filtered.length - state.displayLimit))} 条`;
  renderDetail(selectedRecord());
}

function tag(text, tone = "") {
  const element = document.createElement("span");
  element.className = `event-tag${tone ? ` ${tone}` : ""}`;
  element.textContent = text;
  return element;
}

function detailField(label, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "detail-field";
  const name = document.createElement("label");
  name.textContent = label;
  const content = document.createElement("div");
  content.textContent = value || "—";
  wrapper.append(name, content);
  return wrapper;
}

function renderDetail(record) {
  elements.eventDetail.replaceChildren();
  if (!record) {
    const empty = document.createElement("div");
    empty.className = "empty-detail";
    empty.textContent = "选择一条事件查看完整内容。";
    elements.eventDetail.append(empty);
    return;
  }
  const kicker = document.createElement("div");
  kicker.className = "detail-kicker";
  kicker.textContent = `LINE ${record.lineNumber} · ${record.family.toUpperCase()}`;
  const title = document.createElement("h2");
  title.textContent = record.type;
  const summary = document.createElement("p");
  summary.className = "detail-summary";
  summary.textContent = record.summary || "没有可推断的摘要";
  const grid = document.createElement("div");
  grid.className = "detail-grid";
  grid.append(
    detailField("Profile", record.profile),
    detailField("Timestamp", record.timestampMs === null ? "" : new Date(record.timestampMs).toISOString()),
    detailField("Scope", record.scope),
    detailField("Phase", record.phase),
    detailField("Iteration", record.iteration === null ? "" : String(record.iteration)),
    detailField("Status", record.status),
    detailField("Integrity", record.integrity),
    detailField("Issues", record.issues.join(", ")),
    detailField("Stream ID", record.streamId),
    detailField("Sequence", record.sequence === null ? "" : String(record.sequence)),
    detailField("Tool", record.tool),
    detailField("Provider", record.provider),
    detailField("Call ID", record.callId),
    detailField("Pair status", record.pairStatus),
    detailField("Duration", record.durationMs === null ? "" : formatDuration(record.durationMs)),
    detailField("Related lines", record.pairLines.length ? record.pairLines.join(", ") : ""),
    detailField("Trace ID", record.traceId),
    detailField("Run ID", record.runId),
    detailField("Attempt ID", record.attemptId),
    detailField("Session ID", record.sessionId),
    detailField("Step ID", record.stepId),
    detailField("Worker ID", record.workerId),
    detailField("JSON validity", record.valid ? "VALID" : "INVALID"),
  );
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const treeButton = document.createElement("button");
  treeButton.type = "button";
  treeButton.className = "button";
  treeButton.textContent = "查看 JSON 树";
  treeButton.disabled = !record.valid;
  treeButton.addEventListener("click", () => activateTab("tree"));
  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.className = "button";
  copyButton.textContent = "复制当前行";
  copyButton.addEventListener("click", () => copyText(record.valid ? JSON.stringify(record.value, null, 2) : record.raw));
  actions.append(treeButton, copyButton);
  const preview = document.createElement("pre");
  preview.className = "raw-preview";
  preview.textContent = record.valid ? JSON.stringify(record.value, null, 2) : record.raw;
  elements.eventDetail.append(kicker, title, summary, grid, actions, preview);
  if (!record.valid) {
    const error = document.createElement("div");
    error.className = "parse-error";
    error.textContent = record.parseError;
    elements.eventDetail.append(error);
  }
}

function selectRecord(id) {
  state.selectedId = id;
  renderTimeline();
  renderTree();
  renderRaw();
}

function renderDistribution(host, entries, emptyMessage) {
  host.replaceChildren();
  if (!entries.length) {
    host.append(emptyState(emptyMessage));
    return;
  }
  const max = entries[0][1] || 1;
  for (const [name, count] of entries.slice(0, 18)) {
    const row = document.createElement("div");
    row.className = "bar-row";
    const code = document.createElement("code");
    code.textContent = name;
    code.title = name;
    const track = document.createElement("span");
    track.className = "bar-track";
    const fill = document.createElement("i");
    fill.className = "bar-fill";
    fill.style.width = `${Math.max(2, count / max * 100)}%`;
    track.append(fill);
    const value = document.createElement("em");
    value.textContent = formatNumber(count);
    row.append(code, track, value);
    host.append(row);
  }
}

function valueType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

function collectPaths(value, path, result, depth, budget) {
  if (budget.remaining <= 0 || depth > 6) return;
  if (Array.isArray(value)) {
    const nextPath = path ? `${path}[]` : "[]";
    result.set(nextPath, valueType(value));
    budget.remaining -= 1;
    for (const item of value.slice(0, 8)) collectPaths(item, nextPath, result, depth + 1, budget);
    return;
  }
  if (!isObject(value)) return;
  for (const [key, nested] of Object.entries(value).slice(0, 200)) {
    if (budget.remaining <= 0) break;
    const nextPath = path ? `${path}.${key}` : key;
    result.set(nextPath, valueType(nested));
    budget.remaining -= 1;
    if (nested !== null && typeof nested === "object") collectPaths(nested, nextPath, result, depth + 1, budget);
  }
}

function fieldStatistics(records) {
  const aggregate = new Map();
  for (const record of records) {
    if (!record.valid) continue;
    const paths = new Map();
    collectPaths(record.value, "", paths, 0, { remaining: 500 });
    for (const [path, type] of paths) {
      if (!aggregate.has(path)) aggregate.set(path, { count: 0, types: new Map() });
      const entry = aggregate.get(path);
      entry.count += 1;
      entry.types.set(type, (entry.types.get(type) || 0) + 1);
    }
  }
  return [...aggregate.entries()].sort((left, right) => right[1].count - left[1].count || left[0].localeCompare(right[0]));
}

function renderFields() {
  const valid = state.filtered.filter(record => record.valid);
  renderDistribution(elements.typeDistribution, countBy(valid, "type"), "没有可统计的事件类型。" );
  renderDistribution(elements.toolDistribution, countBy(valid, "tool"), "没有识别到 Tool 字段。" );
  elements.fieldTable.replaceChildren();
  const stats = fieldStatistics(valid).slice(0, 300);
  if (!stats.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.append(emptyState("没有可统计的对象字段。"));
    row.append(cell);
    elements.fieldTable.append(row);
    return;
  }
  for (const [path, info] of stats) {
    const row = document.createElement("tr");
    const pathCell = document.createElement("td");
    const code = document.createElement("code");
    code.textContent = path;
    pathCell.append(code);
    const countCell = document.createElement("td");
    countCell.textContent = formatNumber(info.count);
    const coverageCell = document.createElement("td");
    coverageCell.textContent = valid.length ? `${(info.count / valid.length * 100).toFixed(1)}%` : "—";
    const typeCell = document.createElement("td");
    typeCell.textContent = [...info.types.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => `${type} ${count}`).join(", ");
    row.append(pathCell, countCell, coverageCell, typeCell);
    elements.fieldTable.append(row);
  }
}

function treeValue(value) {
  const span = document.createElement("span");
  if (value === null) {
    span.className = "tree-null";
    span.textContent = "null";
  } else if (typeof value === "string") {
    span.className = "tree-string";
    span.textContent = JSON.stringify(value);
  } else if (typeof value === "number") {
    span.className = "tree-number";
    span.textContent = String(value);
  } else if (typeof value === "boolean") {
    span.className = "tree-boolean";
    span.textContent = String(value);
  } else {
    span.textContent = String(value);
  }
  return span;
}

function treeNode(value, key, depth, budget) {
  if (budget.remaining <= 0) {
    const truncated = document.createElement("div");
    truncated.className = "tree-row tree-null";
    truncated.textContent = "… 节点过多，已停止展开";
    return truncated;
  }
  budget.remaining -= 1;
  const composite = value !== null && typeof value === "object";
  if (!composite) {
    const row = document.createElement("div");
    row.className = "tree-row";
    if (key !== null) {
      const keySpan = document.createElement("span");
      keySpan.className = "tree-key";
      keySpan.textContent = `${key}: `;
      row.append(keySpan);
    }
    row.append(treeValue(value));
    return row;
  }
  const entries = Array.isArray(value) ? value.map((item, index) => [index, item]) : Object.entries(value);
  const details = document.createElement("details");
  details.open = depth < 2;
  const summary = document.createElement("summary");
  const keySpan = document.createElement("span");
  keySpan.className = "tree-key";
  keySpan.textContent = key === null ? (Array.isArray(value) ? "root" : "root") : String(key);
  const count = document.createElement("span");
  count.className = "tree-count";
  count.textContent = Array.isArray(value) ? `: Array(${entries.length})` : `: Object(${entries.length})`;
  summary.append(keySpan, count);
  details.append(summary);
  for (const [nestedKey, nestedValue] of entries.slice(0, 250)) details.append(treeNode(nestedValue, nestedKey, depth + 1, budget));
  if (entries.length > 250) {
    const more = document.createElement("div");
    more.className = "tree-row tree-null";
    more.textContent = `… 还有 ${entries.length - 250} 个成员未展开`;
    details.append(more);
  }
  return details;
}

function renderTree() {
  const record = selectedRecord();
  elements.jsonTree.replaceChildren();
  elements.treeLine.textContent = record ? `LINE ${record.lineNumber}` : "LINE —";
  elements.treeTitle.textContent = record ? record.type : "选择一条有效 JSON";
  document.querySelector("#copy-json").disabled = !record;
  if (!record) {
    elements.jsonTree.append(emptyState("当前筛选结果中没有可查看的记录。"));
    return;
  }
  if (!record.valid) {
    const error = document.createElement("div");
    error.className = "parse-error";
    error.textContent = `${record.parseError}\n\n${record.raw}`;
    elements.jsonTree.append(error);
    return;
  }
  elements.jsonTree.append(treeNode(record.value, null, 0, { remaining: 4000 }));
}

function renderRaw() {
  elements.rawLines.replaceChildren();
  if (!state.filtered.length) {
    elements.rawLines.append(emptyState("没有记录符合当前筛选条件。"));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const record of state.filtered.slice(0, Math.max(state.displayLimit, PAGE_SIZE))) {
    const row = document.createElement("div");
    row.className = `raw-line${record.valid ? "" : " invalid"}${record.id === state.selectedId ? " selected" : ""}`;
    row.dataset.recordId = record.id;
    const number = document.createElement("span");
    number.className = "raw-number";
    number.textContent = String(record.lineNumber);
    const raw = document.createElement("pre");
    raw.className = "raw-content";
    raw.textContent = record.raw;
    row.append(number, raw);
    row.addEventListener("click", () => selectRecord(record.id));
    fragment.append(row);
  }
  elements.rawLines.append(fragment);
}

function activateTab(name) {
  state.activeTab = name;
  document.querySelectorAll(".tab").forEach(tab => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === `${name}-view`));
  if (name === "tree") renderTree();
  if (name === "raw") renderRaw();
}

function emptyState(text) {
  const element = document.createElement("div");
  element.className = "empty-state";
  element.textContent = text;
  return element;
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    showToast("已复制到剪贴板");
  } catch {
    showToast("浏览器未允许复制，请手动选择内容");
  }
}

let toastTimer = null;
function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2200);
}

async function loadLocalFile(file) {
  if (!file) return;
  try {
    elements.input.value = await file.text();
    state.sourceName = file.name;
    updateInputMeta();
    parseCurrentInput();
  } catch {
    showToast("无法读取该本地文件");
  } finally {
    elements.fileInput.value = "";
  }
}

document.querySelector("#parse-button").addEventListener("click", parseCurrentInput);
document.querySelector("#open-file-button").addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", () => loadLocalFile(elements.fileInput.files?.[0]));
document.querySelector("#sample-button").addEventListener("click", () => {
  elements.input.value = sampleLines.map(value => JSON.stringify(value)).join("\n");
  state.sourceName = "haifa-runtime-trace-sample.jsonl";
  updateInputMeta();
  parseCurrentInput();
});
document.querySelector("#clear-button").addEventListener("click", () => {
  elements.input.value = "";
  state.sourceName = "";
  state.records = [];
  state.filtered = [];
  state.selectedId = null;
  elements.results.hidden = true;
  updateInputMeta();
  elements.input.focus();
});
elements.input.addEventListener("input", () => {
  state.sourceName = "";
  updateInputMeta();
});
elements.input.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    parseCurrentInput();
  }
});
for (const element of [elements.typeFilter, elements.toolFilter, elements.phaseFilter, elements.iterationFilter, elements.searchFilter, elements.errorFilter]) {
  element.addEventListener(element === elements.searchFilter ? "input" : "change", applyFilters);
}
document.querySelector("#reset-filter").addEventListener("click", () => {
  elements.typeFilter.value = "";
  elements.toolFilter.value = "";
  elements.phaseFilter.value = "";
  elements.iterationFilter.value = "";
  elements.searchFilter.value = "";
  elements.errorFilter.checked = false;
  applyFilters();
});
for (const eventName of ["dragenter", "dragover"]) {
  elements.inputPanel.addEventListener(eventName, event => {
    event.preventDefault();
    elements.inputPanel.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  elements.inputPanel.addEventListener(eventName, event => {
    event.preventDefault();
    elements.inputPanel.classList.remove("dragging");
  });
}
elements.inputPanel.addEventListener("drop", event => loadLocalFile(event.dataTransfer?.files?.[0]));
document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
elements.loadMore.addEventListener("click", () => {
  state.displayLimit += PAGE_SIZE;
  renderTimeline();
  renderRaw();
});
document.querySelector("#copy-json").addEventListener("click", () => {
  const record = selectedRecord();
  if (record) copyText(record.valid ? JSON.stringify(record.value, null, 2) : record.raw);
});

updateInputMeta();
activateTab("timeline");
