"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  GENERIC_PROFILE,
  HAIFA_PROFILE,
  isHaifaRuntimeTrace,
  parseJsonl,
} = require("../src/haifa_eval_viewer/web/jsonl-normalizer.js");

function trace(overrides = {}) {
  return {
    timestamp: "2026-08-25T10:00:00.000Z",
    traceId: "trace-1",
    runId: "run-1",
    attemptId: "attempt-1",
    sessionId: "session-1",
    workerId: "worker-1",
    iteration: 1,
    phase: "BEFORE_CONTEXT_BUILD",
    operation: "loop.iteration",
    attributes: { iteration: 1 },
    ...overrides,
  };
}

function jsonl(records) {
  return records.map(record => JSON.stringify(record)).join("\n");
}

test("recognizes the Haifa runtime trace envelope and preserves operation semantics", () => {
  const records = parseJsonl(jsonl([
    trace(),
    trace({
      timestamp: "2026-08-25T10:00:00.100Z",
      phase: "AFTER_MODEL_CALL",
      operation: "model.invoke",
      stepId: "model-step-1",
      attributes: { providerId: "provider-a", modelId: "model-a", inputTokens: 100, outputTokens: 20 },
    }),
    trace({
      timestamp: "2026-08-25T10:00:00.200Z",
      phase: "BEFORE_DECISION_EXECUTION",
      operation: "tool.execute",
      stepId: "tool-step-1",
      toolCallId: "call-1",
      attributes: { providerId: "local", toolName: "file.read", toolVersion: "1" },
    }),
    trace({
      timestamp: "2026-08-25T10:00:00.550Z",
      phase: "AFTER_DECISION_EXECUTION",
      operation: "tool.persisted",
      stepId: "tool-step-1",
      toolCallId: "call-1",
      attributes: { successful: false, truncated: true, externalizationRequired: false, externalized: false },
    }),
    trace({
      timestamp: "2026-08-25T10:00:01.000Z",
      iteration: 0,
      phase: "ON_ERROR",
      operation: "runtime.error",
      attributes: { errorCode: "MODEL_RESPONSE_INVALID", exceptionType: "AgentExecutionFailureException" },
    }),
  ]));

  assert.equal(records.length, 5);
  assert.ok(records.every(record => record.profile === HAIFA_PROFILE));
  assert.deepEqual(records.map(record => record.type), [
    "loop.iteration",
    "model.invoke",
    "tool.execute",
    "tool.persisted",
    "runtime.error",
  ]);
  assert.equal(records[0].status, "ACTIVE");
  assert.equal(records[1].status, "SUCCESS");
  assert.equal(records[2].role, "call");
  assert.equal(records[3].role, "result");
  assert.equal(records[3].status, "FAILURE");
  assert.equal(records[3].tool, "file.read");
  assert.equal(records[3].provider, "local");
  assert.equal(records[3].pairStatus, "PAIRED");
  assert.equal(records[3].durationMs, 350);
  assert.deepEqual(records[3].pairLines, [3]);
  assert.equal(records[4].status, "FAILURE");
  assert.equal(records[4].scope, "RUN/ATTEMPT");
  assert.equal(records[4].iteration, null);
  assert.equal(records[4].rawIteration, 0);
  assert.equal(records.filter(record => record.isError).length, 2);
});

test("does not guess incomplete or unrelated JSONL as a Haifa trace", () => {
  const value = { timestamp: "2026-08-25T10:00:00Z", operation: "custom.work", status: "success" };
  assert.equal(isHaifaRuntimeTrace(value), false);
  const [record] = parseJsonl(JSON.stringify(value));
  assert.equal(record.profile, GENERIC_PROFILE);
  assert.equal(record.type, "custom.work");
  assert.equal(record.status, "success");
});

test("correlates equal tool call IDs within each run instead of across runs", () => {
  const records = parseJsonl(jsonl([
    trace({ operation: "tool.execute", phase: "BEFORE_DECISION_EXECUTION", toolCallId: "call-1", attributes: { toolName: "file.read" } }),
    trace({ operation: "tool.persisted", phase: "AFTER_DECISION_EXECUTION", toolCallId: "call-1", attributes: { successful: true } }),
    trace({ runId: "run-2", operation: "tool.execute", phase: "BEFORE_DECISION_EXECUTION", toolCallId: "call-1", attributes: { toolName: "file.patch" } }),
    trace({ runId: "run-2", operation: "tool.persisted", phase: "AFTER_DECISION_EXECUTION", toolCallId: "call-1", attributes: { successful: true } }),
  ]));

  assert.equal(records[1].tool, "file.read");
  assert.equal(records[3].tool, "file.patch");
  assert.ok(records.every(record => record.pairStatus === "PAIRED"));
});

test("keeps unknown Haifa operations visible and invalid lines fail-visible", () => {
  const records = parseJsonl(`${JSON.stringify(trace({ operation: "future.new-operation" }))}\n{broken`);
  assert.equal(records[0].family, "unknown");
  assert.equal(records[0].type, "future.new-operation");
  assert.equal(records[1].valid, false);
  assert.equal(records[1].isError, true);
});
