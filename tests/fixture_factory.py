from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SyntheticTask:
    name: str
    language: str
    verifier_status: str
    reward: float
    exit_code: int
    error_type: str = ""
    error_code: str | None = None
    unknown_event: bool = False


def create_activity(
    root: Path, run_id: str, tasks: list[SyntheticTask], *, finalization_status: str = "COMPLETE"
) -> Path:
    activity = root / "coding" / "final" / run_id
    (activity / "integrity").mkdir(parents=True)
    (activity / "reports").mkdir()
    finalization = {
        "schemaVersion": 1,
        "evalId": f"synthetic-{run_id}",
        "runId": run_id,
        "dataset": "synthetic/coding-v1@sha256:fixture",
        "status": finalization_status,
        "plannedTrials": len(tasks),
        "observedTrials": len(tasks),
        "invalidTrials": 0,
        "evidenceIssues": 0,
        "safetyFindings": [],
        "evidence": [],
        "reports": ["comparison.md", "results.csv"],
        "checksummedFiles": 0,
    }
    _json(activity / "integrity" / "finalization.json", finalization)
    fieldnames = [
        "eval_id",
        "candidate",
        "model",
        "agent_version",
        "task_id",
        "language",
        "attempt",
        "status",
        "reward",
        "duration_seconds",
        "exit_code",
        "error_type",
        "trial_path",
    ]
    with (activity / "reports" / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, task in enumerate(tasks, start=1):
            trial_name = f"{task.name}__trial{index}"
            writer.writerow(
                {
                    "eval_id": f"synthetic-{run_id}",
                    "candidate": "haifa",
                    "model": "synthetic-model",
                    "agent_version": "sha256:synthetic",
                    "task_id": f"aider/{task.name}",
                    "language": task.language,
                    "attempt": 1,
                    "status": task.verifier_status,
                    "reward": task.reward,
                    "duration_seconds": 40 + index,
                    "exit_code": task.exit_code,
                    "error_type": task.error_type,
                    "trial_path": trial_name,
                }
            )
            _create_trial(activity, trial_name, task, index)
    return activity


def add_unsafe_primary_row(activity: Path) -> None:
    csv_path = activity / "reports" / "results.csv"
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "unsafe",
                "haifa",
                "synthetic-model",
                "sha256:synthetic",
                "aider/unsafe",
                "python",
                1,
                "FAIL",
                0,
                1,
                2,
                "",
                "../outside",
            ]
        )


def _create_trial(activity: Path, trial_name: str, task: SyntheticTask, index: int) -> None:
    trial = activity / "jobs" / "primary" / trial_name
    agent = trial / "agent"
    transcripts = agent / "haifa-transcripts"
    transcripts.mkdir(parents=True)
    (trial / "verifier").mkdir()
    start = f"2026-08-21T00:00:{index:02d}Z"
    finish = f"2026-08-21T00:01:{index:02d}Z"
    result = {
        "id": f"result-{index}",
        "task_name": f"aider/{task.name}",
        "trial_name": trial_name,
        "trial_uri": "file:///redacted",
        "agent_info": {
            "name": "haifa",
            "version": "sha256:synthetic",
            "model_info": {"name": "synthetic-model", "provider": None},
        },
        "agent_result": {"metadata": {"exit_code": task.exit_code, "trajectory_archived": True}},
        "verifier_result": {"rewards": {"reward": task.reward}},
        "exception_info": {"exception_type": task.error_type, "exception_message": "redacted"}
        if task.error_type
        else None,
        "started_at": start,
        "finished_at": finish,
        "agent_execution": {"started_at": start, "finished_at": finish},
        "verifier": {"started_at": finish, "finished_at": finish},
    }
    _json(trial / "result.json", result)
    (trial / "verifier" / "reward.txt").write_text(str(task.reward), encoding="utf-8")
    _create_database(agent / "haifa-runtime.db", task, index)
    trace_rows = [
        {
            "timestamp": start,
            "traceId": f"trace-model-{index}",
            "runId": f"run-{index}",
            "attemptId": f"attempt-{index}",
            "phase": "AFTER_MODEL_CALL",
            "operation": "model.invoke",
            "attributes": {
                "modelId": "synthetic-model",
                "providerId": "synthetic-provider",
                "finishReason": "TOOL_CALLS",
                "reasoning_content": "must-not-appear",
            },
        },
        {
            "timestamp": finish,
            "traceId": f"trace-tool-{index}",
            "runId": f"run-{index}",
            "attemptId": f"attempt-{index}",
            "phase": "BEFORE_DECISION_EXECUTION",
            "operation": "tool.execute",
            "attributes": {"toolName": "file.write", "providerId": "haifa-project"},
        },
    ]
    if task.error_code:
        trace_rows.extend(
            [
                {
                    "timestamp": finish,
                    "traceId": f"trace-provider-error-{index}",
                    "runId": f"run-{index}",
                    "phase": "ON_ERROR",
                    "operation": "model.error",
                    "attributes": {
                        "providerId": "synthetic-provider",
                        "providerMessage": "private provider body",
                        "retryable": False,
                    },
                },
                {
                    "timestamp": finish,
                    "traceId": f"trace-runtime-error-{index}",
                    "runId": f"run-{index}",
                    "phase": "ON_ERROR",
                    "operation": "runtime.error",
                    "attributes": {"errorCode": task.error_code, "exceptionType": "SyntheticFailure"},
                },
            ]
        )
    if task.unknown_event:
        trace_rows.append(
            {
                "timestamp": finish,
                "traceId": f"trace-unknown-{index}",
                "operation": "future.signal",
                "attributes": {"newField": "kept-by-source-only"},
            }
        )
    with (agent / "haifa-trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in trace_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    transcript = {
        "eventId": f"event-{index}",
        "eventType": "run.created",
        "occurredAt": start,
        "payload": {"private": "must-not-appear"},
        "runId": f"run-{index}",
        "schemaVersion": 1,
        "sequence": 1,
    }
    (transcripts / f"run-{index}.jsonl").write_text(json.dumps(transcript) + "\n", encoding="utf-8")


def _create_database(path: Path, task: SyntheticTask, index: int) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE run (
          run_id TEXT PRIMARY KEY, status TEXT, usage_model_calls INTEGER, usage_tool_calls INTEGER, created_at INTEGER
        );
        CREATE TABLE execution_attempt (
          attempt_id TEXT PRIMARY KEY, attempt_number INTEGER
        );
        CREATE TABLE runtime_event (
          event_id TEXT PRIMARY KEY, run_id TEXT, sequence INTEGER, type TEXT, occurred_at INTEGER,
          data_schema_version INTEGER, data_payload TEXT, data_hash TEXT, event_schema_version INTEGER,
          correlation_id TEXT, causation_id TEXT
        );
        CREATE TABLE tool_call (
          tool_call_id TEXT PRIMARY KEY, run_id TEXT, step_id TEXT, tool_name TEXT, status TEXT,
          requested_at INTEGER, started_at INTEGER, completed_at INTEGER,
          arguments_schema_version INTEGER, arguments_payload TEXT, arguments_hash TEXT,
          result_schema_version INTEGER, result_payload TEXT, result_hash TEXT,
          error_schema_version INTEGER, error_payload TEXT, error_hash TEXT
        );
        """
    )
    base = 1787260800000 + index * 1000
    run_status = "COMPLETED" if task.exit_code == 0 else "FAILED"
    connection.execute("INSERT INTO run VALUES (?, ?, ?, ?, ?)", (f"run-{index}", run_status, 2, 1, base))
    connection.execute("INSERT INTO execution_attempt VALUES (?, ?)", (f"attempt-{index}", 1))
    event_types = ["run.created", "run.started", "model.call.started", "model.call.succeeded"]
    if task.error_code:
        event_types.append("runtime.error")
    event_types.append("run.completed" if task.exit_code == 0 else "run.failed")
    for sequence, event_type in enumerate(event_types, start=1):
        connection.execute(
            "INSERT INTO runtime_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"db-event-{index}-{sequence}",
                f"run-{index}",
                sequence,
                event_type,
                base + sequence * 100,
                1,
                json.dumps({"complete": "runtime-event-payload", "sequence": sequence}),
                f"event-hash-{sequence}",
                1,
                None,
                None,
            ),
        )
    connection.execute(
        "INSERT INTO tool_call VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"tool-{index}",
            f"run-{index}",
            f"step-{index}",
            "file_write",
            "COMPLETED",
            base + 500,
            base + 510,
            base + 550,
            1,
            json.dumps({"path": "synthetic/private.txt", "content": "complete tool arguments"}),
            "arguments-hash",
            1,
            json.dumps({"bytesWritten": 23, "complete": "tool result"}),
            "result-hash",
            None,
            None,
            None,
        ),
    )
    connection.commit()
    connection.close()


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
