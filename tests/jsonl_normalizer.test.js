"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { GENERIC_PROFILE, HAIFA_PROFILE, isHaifaRuntimeTrace, parseJsonl } = require("../src/haifa_eval_viewer/web/jsonl-normalizer.js");

function trace(sequence, overrides = {}) {
  return {
    recordType: "trace.event", schema: "haifa-runtime-trace", schemaVersion: 1,
    producer: "haifa-agent-cli", producerVersion: "0.1.0-SNAPSHOT",
    streamId: "ts_Q83mKMf7pV0r2XaN7cFwYg", sequence,
    timestamp: `2026-08-25T10:00:0${Math.min(sequence, 9)}.000Z`,
    traceId: "tr_zP6x5aJdC1nW8kRt4uHqBg", runId: "run-1", attemptId: "attempt-1",
    sessionId: "session-1", workerId: "worker-1", iteration: 1,
    scope: "ITERATION", phase: "BEFORE_CONTEXT_BUILD", operation: "loop.iteration", status: "ACTIVE",
    attributes: { iteration: 1 }, ...overrides,
  };
}

function completed(sequence, overrides = {}) {
  return trace(sequence, {
    traceId: undefined, runId: undefined, attemptId: undefined, sessionId: undefined,
    workerId: undefined, iteration: undefined, scope: "STREAM", phase: "NONE",
    operation: "stream.completed", status: "SUCCESS",
    attributes: { writtenEvents: sequence, droppedEvents: 0, cleanClose: true }, ...overrides,
  });
}

function jsonl(records) { return records.map(record => JSON.stringify(record)).join("\n"); }

test("recognizes only complete V1 metadata and preserves explicit semantics", () => {
  const records = parseJsonl(jsonl([
    trace(1),
    trace(2, { phase: "AFTER_MODEL_CALL", operation: "model.invoke", scope: "STEP", status: "SUCCESS", stepId: "model-step-1", attributes: { providerId: "provider-a", modelId: "model-a", inputTokens: 100, outputTokens: 20 } }),
    trace(3, { timestamp: "2026-08-25T10:00:03.100Z", phase: "BEFORE_DECISION_EXECUTION", operation: "tool.execute", scope: "TOOL_CALL", status: "STARTED", stepId: "tool-step-1", toolCallId: "call-1", attributes: { providerId: "local", toolName: "file.read", toolVersion: "1" } }),
    trace(4, { timestamp: "2026-08-25T10:00:03.450Z", phase: "AFTER_DECISION_EXECUTION", operation: "tool.persisted", scope: "TOOL_CALL", status: "FAILURE", stepId: "tool-step-1", toolCallId: "call-1", attributes: { toolName: "file.read", providerId: "local", successful: false, failureCode: "TOOL_BUSINESS_FAILURE", retryable: false, truncated: true, externalizationRequired: false, externalized: false } }),
    trace(5, { iteration: undefined, phase: "ON_ERROR", operation: "runtime.error", scope: "ATTEMPT", status: "FAILURE", attributes: { errorCode: "MODEL_RESPONSE_INVALID", exceptionType: "AgentExecutionFailureException" } }),
    completed(6),
  ]));

  assert.equal(records.length, 6);
  assert.ok(records.every(record => record.profile === HAIFA_PROFILE));
  assert.ok(records.every(record => record.integrity === "VALID"));
  assert.equal(records[0].status, "ACTIVE");
  assert.equal(records[2].role, "call");
  assert.equal(records[3].role, "result");
  assert.equal(records[3].status, "FAILURE");
  assert.equal(records[3].tool, "file.read");
  assert.equal(records[3].pairStatus, "PAIRED");
  assert.equal(records[3].durationMs, 350);
  assert.equal(records[4].scope, "ATTEMPT");
  assert.equal(records[4].iteration, null);
});

test("treats legacy V0 and unrelated JSONL as generic", () => {
  const legacy = { timestamp: "2026-08-25T10:00:00Z", traceId: "old", runId: "run-1", sessionId: "session-1", iteration: 1, phase: "AFTER_MODEL_CALL", operation: "model.invoke", attributes: {} };
  assert.equal(isHaifaRuntimeTrace(legacy), false);
  const records = parseJsonl(jsonl([legacy, { timestamp: "2026-08-25T10:00:00Z", operation: "custom.work", status: "success" }]));
  assert.ok(records.every(record => record.profile === GENERIC_PROFILE));
});

test("sorts each V1 stream by sequence and reports input order, gaps, and duplicates", () => {
  const records = parseJsonl(jsonl([trace(2), trace(1), trace(2), completed(4)]));
  assert.deepEqual(records.map(record => record.sequence), [1, 2, 2, 4]);
  assert.ok(records.some(record => record.issues.includes("OUT_OF_ORDER_SEQUENCE")));
  assert.ok(records.some(record => record.issues.includes("DUPLICATE_SEQUENCE")));
  assert.ok(records.some(record => record.issues.includes("SEQUENCE_GAP")));
  assert.ok(records.every(record => record.issues.includes("TRACE_INCOMPLETE")));
});

test("marks a stream without a clean completion as incomplete", () => {
  const records = parseJsonl(jsonl([trace(1), trace(2, { operation: "future.new-operation" })]));
  assert.ok(records.every(record => record.issues.includes("TRACE_INCOMPLETE")));
  assert.ok(records[1].issues.includes("UNKNOWN_OPERATION"));
  assert.equal(records[1].family, "unknown");
});

test("orders a V1 stream by sequence even when generic records are also present", () => {
  const records = parseJsonl(jsonl([trace(2), { type: "note", message: "generic" }, trace(1), completed(3)]));
  assert.deepEqual(records.map(record => record.sequence), [1, 2, 3, null]);
  assert.equal(records[3].profile, GENERIC_PROFILE);
});

test("keeps unsupported schema and invalid JSON fail-visible", () => {
  const records = parseJsonl(`${JSON.stringify(trace(1, { schemaVersion: 3 }))}\n{broken`);
  assert.equal(records[0].profile, GENERIC_PROFILE);
  assert.equal(records[0].integrity, "UNSUPPORTED_SCHEMA");
  assert.ok(records[0].issues.includes("UNKNOWN_SCHEMA"));
  assert.equal(records[1].valid, false);
  assert.equal(records[1].isError, true);
});

function traceV2(sequence, overrides = {}) {
  return {
    recordType: "trace.event", schema: "haifa-runtime-trace", schemaVersion: 2,
    producer: "haifa-agent-cli", producerVersion: "0.1.0-SNAPSHOT",
    streamId: "ts_Q83mKMf7pV0r2XaN7cFwYg", sequence,
    timestampMs: 1787522400000 + sequence * 1000,
    traceId: "tr_zP6x5aJdC1nW8kRt4uHqBg", runId: "run-1", attemptId: "attempt-1",
    sessionId: "session-1", workerId: "worker-1", iteration: 1,
    scope: "ITERATION", phase: "BEFORE_CONTEXT_BUILD", operation: "loop.iteration",
    lifecycle: "ACTIVE",
    attributes: { iteration: 1 }, ...overrides,
  };
}

function completedV2(sequence, overrides = {}) {
  return traceV2(sequence, {
    traceId: undefined, runId: undefined, attemptId: undefined, sessionId: undefined,
    workerId: undefined, iteration: undefined, scope: "STREAM", phase: "NONE",
    operation: "stream.completed", lifecycle: "COMPLETED", outcome: "SUCCESS",
    attributes: { writtenEventCount: sequence, droppedEventCount: 0, cleanClose: true }, ...overrides,
  });
}

test("recognizes V2 lifecycle/outcome envelope and renamed attributes", () => {
  const records = parseJsonl(jsonl([
    traceV2(1, { phase: "AFTER_CONTEXT_BUILD", operation: "context.built", lifecycle: "COMPLETED", outcome: "SUCCESS", attributes: { estimatedInputTokens: 900, selectedItemCount: 7, summarySourceDigest: "sha256:abc" } }),
    traceV2(2, { phase: "BEFORE_DECISION_EXECUTION", operation: "tool.execute", scope: "TOOL_CALL", lifecycle: "STARTED", stepId: "tool-step-1", toolCallId: "call-1", attributes: { providerId: "local", toolName: "file.read", toolDefinitionDigest: "abc123" } }),
    traceV2(3, { phase: "AFTER_DECISION_EXECUTION", operation: "tool.persisted", scope: "TOOL_CALL", lifecycle: "COMPLETED", outcome: "FAILURE", stepId: "tool-step-1", toolCallId: "call-1", attributes: { toolName: "file.read", outputTruncated: true, payloadExternalized: true } }),
    completedV2(4),
  ]));

  assert.ok(records.every(record => record.profile === HAIFA_PROFILE));
  assert.ok(records.every(record => record.integrity === "VALID"));
  assert.equal(records[0].traceVersion, 2);
  assert.equal(records[0].status, "SUCCESS");
  assert.equal(records[0].summary, "Context built · input tokens=900 · items=7");
  assert.equal(records[1].status, "STARTED");
  assert.equal(records[2].isError, true);
  assert.equal(records[2].status, "FAILURE");
  assert.equal(records[2].summary, "file.read failed · truncated · externalized");
  assert.ok(records[2].timestampMs > 0);
});

test("flags V2 records missing required lifecycle or timestampMs", () => {
  const records = parseJsonl(jsonl([
    traceV2(1, { timestampMs: undefined }),
    traceV2(2, { lifecycle: "COMPLETED", outcome: undefined }),
    completedV2(3),
  ]));
  assert.ok(records[0].issues.includes("INVALID_TIMESTAMPMS"));
  assert.ok(records[1].issues.includes("MISSING_OUTCOME"));
});
