(function exposeJsonlNormalizer(root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.HaifaJsonlNormalizer = api;
})(typeof globalThis === "undefined" ? this : globalThis, function createJsonlNormalizer() {
  "use strict";

  const HAIFA_PROFILE = "Haifa Runtime Trace";
  const GENERIC_PROFILE = "Generic JSONL";
  const SEARCH_CONTAINERS = ["event", "data", "payload", "attributes", "metadata", "message", "record"];
  const TYPE_KEYS = ["type", "event_type", "eventType", "kind", "category", "event_name", "eventName"];
  const TIME_KEYS = ["timestamp", "time", "occurred_at", "occurredAt", "created_at", "createdAt", "ts", "time_ms", "timeMs"];
  const TOOL_KEYS = ["tool_name", "toolName", "tool", "function_name", "functionName"];
  const CALL_ID_KEYS = ["call_id", "callId", "tool_call_id", "toolCallId", "invocation_id", "invocationId"];
  const STATUS_KEYS = ["status", "level", "severity", "outcome", "state"];
  const SUMMARY_KEYS = ["summary", "message", "content", "text", "name", "title"];
  const ERROR_KEYS = ["error", "exception", "error_code", "errorCode", "stacktrace", "stack_trace"];

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function displayValue(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (isObject(value)) {
      for (const key of ["name", "id", "type", "value"]) {
        if (typeof value[key] === "string" || typeof value[key] === "number") return String(value[key]);
      }
    }
    try { return JSON.stringify(value); } catch { return String(value); }
  }

  function compact(value, limit = 180) {
    const text = displayValue(value).replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
  }

  function directText(value, key, limit = 180) {
    return isObject(value) && typeof value[key] === "string" ? compact(value[key], limit) : "";
  }

  function findValue(rootValue, keys, maxDepth = 3) {
    if (!isObject(rootValue)) return null;
    const wanted = new Set(keys.map(key => key.toLowerCase()));
    const queue = [{ value: rootValue, depth: 0 }];
    const visited = new Set();
    while (queue.length) {
      const current = queue.shift();
      if (!current || visited.has(current.value)) continue;
      visited.add(current.value);
      for (const [key, value] of Object.entries(current.value)) {
        if (wanted.has(key.toLowerCase()) && value !== null && value !== "") return value;
      }
      if (current.depth >= maxDepth) continue;
      for (const container of SEARCH_CONTAINERS) {
        const nested = current.value[container];
        if (isObject(nested)) queue.push({ value: nested, depth: current.depth + 1 });
      }
    }
    return null;
  }

  function parseTimestamp(value) {
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return null;
      if (value > 1e14) return Math.round(value / 1000);
      if (value > 1e11) return Math.round(value);
      if (value > 1e9) return Math.round(value * 1000);
      return null;
    }
    if (typeof value === "string" && /^\d+(\.\d+)?$/.test(value.trim())) return parseTimestamp(Number(value));
    const parsed = Date.parse(String(value));
    return Number.isNaN(parsed) ? null : parsed;
  }

  function isHaifaRuntimeTrace(value) {
    return isObject(value)
      && typeof value.operation === "string"
      && typeof value.phase === "string"
      && typeof value.runId === "string"
      && typeof value.sessionId === "string"
      && Number.isInteger(value.iteration)
      && isObject(value.attributes);
  }

  function inferGenericFamily(type, tool) {
    const lowered = type.toLowerCase();
    if (tool || lowered.includes("tool") || lowered.includes("function")) return "tool";
    if (lowered.includes("model") || lowered.includes("llm") || lowered.includes("assistant")) return "model";
    if (lowered.includes("verify") || lowered.includes("test")) return "verifier";
    if (lowered.includes("error") || lowered.includes("exception")) return "error";
    return "event";
  }

  function inferHaifaFamily(operation) {
    const prefix = operation.split(".", 1)[0].toLowerCase();
    return ["loop", "context", "model", "tool", "runtime"].includes(prefix) ? prefix : "unknown";
  }

  function inferGenericRole(type) {
    const lowered = type.toLowerCase();
    if (/(result|response|output|completed|finished)/.test(lowered)) return "result";
    if (/(call|request|invoke|input|started)/.test(lowered)) return "call";
    return "event";
  }

  function inferHaifaRole(operation) {
    if (operation === "tool.execute") return "call";
    if (operation === "tool.persisted" || operation === "model.invoke") return "result";
    return "event";
  }

  function inferHaifaStatus(operation, phase, attributes) {
    if (phase === "ON_ERROR" || /(^|\.)(error|terminal-error|context-too-long)$/.test(operation)) return "FAILURE";
    if (attributes.successful === false) return "FAILURE";
    if (operation === "tool.execute") return "STARTED";
    if (operation === "loop.iteration") return "ACTIVE";
    if (attributes.successful === true || ["context.built", "context.forced-rebuild", "model.invoke"].includes(operation)) {
      return "SUCCESS";
    }
    return "UNKNOWN";
  }

  function isGenericError(type, status, value) {
    const combined = `${type} ${status}`.toLowerCase();
    if (/(^|[._ -])(error|failed|failure|fatal|exception)([._ -]|$)/.test(combined)) return true;
    const candidate = findValue(value, ERROR_KEYS);
    return candidate !== null && candidate !== undefined && candidate !== "" && candidate !== false;
  }

  function metric(attributes, key, label) {
    const value = attributes[key];
    return value === null || value === undefined || value === "" ? "" : `${label}=${compact(value, 80)}`;
  }

  function haifaSummary(record) {
    const attributes = record.attributes;
    const provider = record.provider ? ` · ${record.provider}` : "";
    switch (record.operation) {
      case "loop.iteration":
        return `Iteration ${record.iteration}`;
      case "context.built":
        return [
          "Context built",
          metric(attributes, "estimatedInputTokens", "input tokens"),
          metric(attributes, "selectedItems", "items"),
          attributes.compacted === true ? "compacted" : "",
        ].filter(Boolean).join(" · ");
      case "context.forced-rebuild":
        return ["Context forced rebuild", metric(attributes, "forcedRebuildAttempt", "attempt"), metric(attributes, "estimatedInputTokens", "input tokens")].filter(Boolean).join(" · ");
      case "model.invoke":
        return [
          `${record.tool || attributes.modelId || "Model"}${provider}`,
          metric(attributes, "inputTokens", "in"),
          metric(attributes, "outputTokens", "out"),
          metric(attributes, "reasoningTokens", "reasoning"),
          metric(attributes, "finishReason", "finish"),
        ].filter(Boolean).join(" · ");
      case "model.error":
      case "model.context-too-long":
        return [
          `${attributes.modelId || "Model"}${provider} failed`,
          metric(attributes, "category", "category"),
          metric(attributes, "exceptionType", "exception"),
        ].filter(Boolean).join(" · ");
      case "tool.execute":
        return `${record.tool || "Tool"}${provider} started`;
      case "tool.persisted":
        return [
          `${record.tool || "Tool"} ${record.status === "FAILURE" ? "failed" : "completed"}`,
          attributes.truncated === true ? "truncated" : "",
          attributes.externalized === true ? "externalized" : "",
        ].filter(Boolean).join(" · ");
      case "runtime.error":
      case "runtime.terminal-error":
        return [
          "Runtime failed",
          metric(attributes, "errorCode", "code"),
          metric(attributes, "exceptionType", "exception"),
        ].filter(Boolean).join(" · ");
      default:
        return `${record.operation} · ${record.status.toLowerCase()}`;
    }
  }

  function baseRecord(value, lineNumber, raw) {
    return {
      id: `line-${lineNumber}`,
      lineNumber,
      raw,
      value,
      valid: true,
      operation: "",
      phase: "",
      iteration: null,
      rawIteration: null,
      scope: "EVENT",
      profile: GENERIC_PROFILE,
      provider: "",
      traceId: "",
      runId: "",
      attemptId: "",
      sessionId: "",
      stepId: "",
      workerId: "",
      attributes: {},
      pairLines: [],
      pairStatus: "",
      durationMs: null,
      searchText: raw.toLowerCase(),
    };
  }

  function normalizeHaifaRecord(value, lineNumber, raw) {
    const record = baseRecord(value, lineNumber, raw);
    const operation = compact(value.operation, 100) || "unknown";
    const phase = compact(value.phase, 80);
    const attributes = value.attributes;
    record.profile = HAIFA_PROFILE;
    record.operation = operation;
    record.phase = phase;
    record.rawIteration = value.iteration;
    record.scope = operation.startsWith("runtime.") ? "RUN/ATTEMPT" : "ITERATION";
    record.iteration = record.scope === "ITERATION" ? value.iteration : null;
    record.type = operation;
    record.attributes = attributes;
    record.tool = operation.startsWith("model.") ? compact(attributes.modelId, 100) : compact(attributes.toolName, 100);
    record.provider = compact(attributes.providerId, 100);
    record.callId = directText(value, "toolCallId", 140);
    record.traceId = directText(value, "traceId", 140);
    record.runId = directText(value, "runId", 140);
    record.attemptId = directText(value, "attemptId", 140);
    record.sessionId = directText(value, "sessionId", 140);
    record.stepId = directText(value, "stepId", 140);
    record.workerId = directText(value, "workerId", 140);
    record.status = inferHaifaStatus(operation, phase, attributes);
    record.family = inferHaifaFamily(operation);
    record.role = inferHaifaRole(operation);
    record.isError = record.status === "FAILURE";
    record.timestampMs = parseTimestamp(value.timestamp);
    record.summary = haifaSummary(record);
    return record;
  }

  function normalizeGenericRecord(value, lineNumber, raw) {
    const record = baseRecord(value, lineNumber, raw);
    const directOperation = directText(value, "operation", 100);
    const rawType = directOperation || findValue(value, TYPE_KEYS);
    const type = compact(rawType || (Array.isArray(value) ? "array" : typeof value), 100) || "unknown";
    const tool = compact(findValue(value, TOOL_KEYS), 100);
    const callId = compact(findValue(value, CALL_ID_KEYS), 140);
    const status = compact(findValue(value, STATUS_KEYS), 80);
    let summary = compact(findValue(value, SUMMARY_KEYS));
    if (!summary || summary === type) summary = tool ? `${tool}${callId ? ` · ${callId}` : ""}` : compact(value);
    record.operation = directOperation;
    record.type = type;
    record.tool = tool;
    record.callId = callId;
    record.status = status;
    record.timestampMs = parseTimestamp(findValue(value, TIME_KEYS));
    record.summary = summary;
    record.family = inferGenericFamily(type, tool);
    record.role = inferGenericRole(type);
    record.isError = isGenericError(type, status, value);
    return record;
  }

  function normalizeRecord(value, lineNumber, raw) {
    return isHaifaRuntimeTrace(value)
      ? normalizeHaifaRecord(value, lineNumber, raw)
      : normalizeGenericRecord(value, lineNumber, raw);
  }

  function correlationKey(record) {
    if (!record.callId) return "";
    return record.profile === HAIFA_PROFILE ? `${record.runId || record.sessionId}\u0000${record.callId}` : record.callId;
  }

  function correlateCalls(records) {
    const groups = new Map();
    for (const record of records) {
      const key = correlationKey(record);
      if (!key) continue;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(record);
    }
    for (const group of groups.values()) {
      const lines = group.map(record => record.lineNumber);
      const calls = group.filter(record => record.role === "call");
      const results = group.filter(record => record.role === "result");
      const pairStatus = calls.length === 1 && results.length === 1
        ? "PAIRED"
        : group.length === 1 ? "ORPHAN" : "AMBIGUOUS";
      const call = calls.length === 1 ? calls[0] : null;
      const result = results.length === 1 ? results[0] : null;
      for (const record of group) {
        record.pairLines = lines.filter(line => line !== record.lineNumber);
        record.pairStatus = pairStatus;
        if (!record.tool && call?.tool) record.tool = call.tool;
        if (!record.provider && call?.provider) record.provider = call.provider;
      }
      if (pairStatus === "PAIRED" && call.timestampMs !== null && result.timestampMs !== null) {
        const duration = Math.max(0, result.timestampMs - call.timestampMs);
        call.durationMs = duration;
        result.durationMs = duration;
      }
      for (const record of group) {
        if (record.profile === HAIFA_PROFILE) record.summary = haifaSummary(record);
        record.searchText = `${record.raw} ${record.tool} ${record.provider} ${record.pairStatus}`.toLowerCase();
      }
    }
  }

  function invalidRecord(raw, lineNumber, error) {
    return {
      ...baseRecord(null, lineNumber, raw),
      valid: false,
      type: "INVALID_JSON",
      callId: "",
      status: "ERROR",
      timestampMs: null,
      summary: compact(error.message, 180),
      family: "error",
      role: "event",
      isError: true,
      parseError: error.message,
      searchText: `${raw} ${error.message}`.toLowerCase(),
    };
  }

  function parseJsonl(text) {
    const records = [];
    text.split(/\r?\n/).forEach((raw, index) => {
      if (!raw.trim()) return;
      const lineNumber = index + 1;
      try {
        records.push(normalizeRecord(JSON.parse(raw), lineNumber, raw));
      } catch (error) {
        records.push(invalidRecord(raw, lineNumber, error));
      }
    });
    correlateCalls(records);
    return records;
  }

  return {
    GENERIC_PROFILE,
    HAIFA_PROFILE,
    correlateCalls,
    isHaifaRuntimeTrace,
    isObject,
    normalizeRecord,
    parseJsonl,
    parseTimestamp,
  };
});
