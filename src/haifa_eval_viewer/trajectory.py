from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import DataIssue, TrajectoryEvent
from .security import UnsafeArchivePath, source_ref, stable_digest

NORMALIZER_VERSION = 1
MAX_JSONL_LINE_BYTES = 1_048_576

SAFE_TRACE_ATTRIBUTES = {
    "adapterType",
    "adapterVersion",
    "cacheHitTokens",
    "cacheMissTokens",
    "category",
    "compacted",
    "compactionCount",
    "compactionGeneration",
    "compactionReason",
    "costKnown",
    "costMinorUnits",
    "diagnosticId",
    "errorCode",
    "estimatedInputTokens",
    "estimatedSessionTokens",
    "exceptionType",
    "finishReason",
    "inputTokens",
    "iteration",
    "modelCallId",
    "modelId",
    "modelVersion",
    "outputTokens",
    "providerCode",
    "providerId",
    "providerVersion",
    "retryable",
    "successful",
    "toolName",
    "toolVersion",
    "truncated",
}


def parse_timestamp_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(numeric if numeric > 10_000_000_000 else numeric * 1000)
    text = str(value).strip()
    if text.isdigit():
        return parse_timestamp_ms(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return None


def _category(operation: str, tool_name: str | None = None) -> str:
    normalized = operation.lower()
    normalized_tool = (tool_name or "").lower().replace(".", "_")
    if normalized.startswith("verifier"):
        return "VERIFIER"
    if normalized.startswith("run.") or normalized.startswith("harbor."):
        return "RUN"
    if normalized.startswith(("context.", "loop.", "checkpoint.")):
        return "CONTEXT"
    if normalized.startswith("model."):
        return "MODEL"
    if normalized.startswith(("completion.", "final.")):
        return "COMPLETION"
    if normalized.startswith("policy."):
        return "POLICY"
    if normalized.startswith("journal."):
        return "JOURNAL"
    if normalized.startswith("runtime.error") or normalized.startswith("error."):
        return "ERROR"
    if normalized_tool.startswith(("file_", "workspace_")):
        return "WORKSPACE"
    if normalized.startswith(("tool.", "execution.")):
        return "TOOL"
    if any(token in normalized for token in ("error", "failed", "exception")):
        return "ERROR"
    return "UNKNOWN"


def _status(operation: str, explicit: str | None = None) -> str:
    value = (explicit or operation).upper()
    if "UNKNOWN" in value or "UNAVAILABLE" in value:
        return "UNKNOWN"
    if any(token in value for token in ("FAIL", "ERROR", "INVALID", "EXHAUSTED", "CANCEL", "TIMEOUT")):
        return "FAILURE"
    if any(token in value for token in ("SUCCEEDED", "SUCCESS", "COMPLETED", "COMPLETE", "PASS", "ACKNOWLEDGED")):
        return "SUCCESS"
    if any(token in value for token in ("START", "RUNNING", "REQUEST", "QUEUED", "CREATED", "DISPATCH")):
        return "RUNNING"
    return "UNKNOWN"


def _event_type(operation: str, category: str) -> str:
    normalized = operation.lower()
    if normalized in {"model.call.started", "model.request"}:
        return "MODEL_REQUEST"
    if normalized in {"model.call.succeeded", "model.invoke", "model.response"}:
        return "MODEL_RESPONSE"
    if normalized in {"tool.requested", "tool.execute"}:
        return "TOOL_CALL"
    if normalized.startswith("tool.") and any(part in normalized for part in ("succeeded", "failed", "persisted")):
        return "TOOL_RESULT"
    if "file_read" in normalized or "file.read" in normalized:
        return "FILE_READ"
    if any(
        part in normalized
        for part in ("file_write", "file_move", "file_delete", "file.write", "file.move", "file.delete", "file.edit")
    ):
        return "FILE_EDIT"
    if "execution" in normalized:
        return "COMMAND_EXECUTION"
    if normalized.startswith("verifier"):
        return "VERIFICATION"
    if "compaction" in normalized or "compression" in normalized:
        return "CONTEXT_COMPRESSION"
    if "retry" in normalized or "repair" in normalized:
        return "RETRY"
    if category == "ERROR":
        return "ERROR"
    if category == "COMPLETION":
        return "COMPLETION"
    return operation.upper().replace(".", "_").replace("-", "_")


def _semantic_phase(category: str, operation: str, tool_name: str | None = None) -> str:
    normalized_tool = (tool_name or "").lower().replace(".", "_")
    if category == "ERROR":
        return "ERROR"
    if category == "VERIFIER":
        return "VERIFICATION"
    if category == "COMPLETION":
        return "COMPLETION"
    if category == "RUN":
        return "RUN_LIFECYCLE"
    if category == "MODEL":
        return "MODEL_TURN"
    if category == "CONTEXT":
        return "BUILD_CONTEXT"
    if category == "WORKSPACE":
        return "INSPECT_FILES" if "read" in normalized_tool or "list" in normalized_tool else "IMPLEMENT_SOLUTION"
    if category == "TOOL" and normalized_tool == "execution_run":
        return "RUN_COMMAND"
    if category == "TOOL":
        return "OPERATE_TOOL"
    return "UNCLASSIFIED_OPERATION"


def _safe_metadata(attributes: Any) -> dict[str, Any]:
    if not isinstance(attributes, dict):
        return {}
    return {key: value for key, value in attributes.items() if key in SAFE_TRACE_ATTRIBUTES and _safe_scalar(value)}


def _safe_scalar(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, (bool, int, float))
        or (isinstance(value, str) and len(value) <= 256 and "\n" not in value and "\r" not in value)
    )


def _summary(operation: str, metadata: dict[str, Any], status: str) -> str:
    subject = metadata.get("toolName") or metadata.get("modelId") or metadata.get("errorCode")
    if subject:
        return f"{operation} · {subject} · {status.lower()}"
    return f"{operation} · {status.lower()}"


def _make_event(
    *,
    activity_id: str,
    task_id: str,
    source: str,
    source_ref_value: str,
    source_identity: str,
    source_sequence: int | None,
    occurred_at_ms: int | None,
    operation: str,
    explicit_status: str | None = None,
    duration_ms: int | None = None,
    attempt_id: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    tool_call_id: str | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    raw_locator: Any = None,
) -> TrajectoryEvent:
    safe = metadata or {}
    tool_name = safe.get("toolName") if isinstance(safe.get("toolName"), str) else None
    category = _category(operation, tool_name)
    status = _status(operation, explicit_status)
    return TrajectoryEvent(
        event_id=f"evt-{stable_digest(source, source_identity)}",
        activity_id=activity_id,
        task_id=task_id,
        attempt_id=attempt_id,
        run_id=run_id,
        sequence=0,
        source_sequence=source_sequence,
        occurred_at_ms=occurred_at_ms,
        duration_ms=duration_ms,
        category=category,
        event_type=_event_type(operation, category),
        operation=operation,
        status=status,
        source=source,
        source_ref=source_ref_value,
        summary=_summary(operation, safe, status),
        semantic_phase=_semantic_phase(category, operation, tool_name),
        error_code=error_code,
        step_id=step_id,
        tool_call_id=tool_call_id,
        metadata=safe,
        raw_locator=raw_locator,
    )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _raw_sqlite_value(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    return value


def _raw_sqlite_row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: _raw_sqlite_value(row[key]) for key in tuple(row.keys())}


def load_raw_payload(locator: dict[str, Any]) -> Any:
    kind = locator.get("kind")
    path = locator.get("path")
    if not isinstance(path, Path):
        raise ValueError("raw evidence locator has no source path")
    if kind == "sqlite-row":
        table = locator.get("table")
        key_column = locator.get("keyColumn")
        if (table, key_column) not in {("runtime_event", "event_id"), ("tool_call", "tool_call_id")}:
            raise ValueError("raw SQLite locator is not allowlisted")
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                f'SELECT * FROM "{table}" WHERE "{key_column}" = ? LIMIT 1',
                (locator.get("keyValue"),),
            ).fetchone()
            if row is None:
                raise ValueError("raw SQLite row no longer exists")
            return {"table": table, "row": _raw_sqlite_row(row)}
        finally:
            connection.close()
    if kind == "jsonl-line":
        target_line = locator.get("line")
        if not isinstance(target_line, int) or target_line < 1:
            raise ValueError("raw JSONL locator has no line number")
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number == target_line:
                    return json.loads(line)
        raise ValueError("raw JSONL line no longer exists")
    if kind == "json-file":
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    raise ValueError("raw evidence locator kind is unsupported")


def _read_sqlite(
    database: Path,
    archive_root: Path,
    root_alias: str,
    activity_id: str,
    task_id: str,
) -> tuple[list[TrajectoryEvent], list[DataIssue], dict[str, Any]]:
    events: list[TrajectoryEvent] = []
    issues: list[DataIssue] = []
    stats: dict[str, Any] = {
        "runId": None,
        "attemptId": None,
        "agentRunStatus": "UNAVAILABLE",
        "modelCalls": None,
        "toolCalls": None,
        "workspaceModified": None,
        "workspaceChangeCount": None,
    }
    try:
        reference = source_ref(archive_root, database, root_alias)
    except (OSError, UnsafeArchivePath):
        return events, [DataIssue("SQLITE_PATH_UNSAFE", "ERROR", "Runtime database path escapes the archive")], stats
    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "run" in tables:
            columns = _table_columns(connection, "run")
            selected = [
                name for name in ("run_id", "status", "usage_model_calls", "usage_tool_calls") if name in columns
            ]
            row = connection.execute(f"SELECT {', '.join(selected)} FROM run LIMIT 1").fetchone()
            if row:
                row_keys = set(row.keys())
                stats.update(
                    runId=row["run_id"] if "run_id" in row_keys else None,
                    agentRunStatus=row["status"] if "status" in row_keys else "UNKNOWN",
                    modelCalls=row["usage_model_calls"] if "usage_model_calls" in row_keys else None,
                    toolCalls=row["usage_tool_calls"] if "usage_tool_calls" in row_keys else None,
                )
        if "execution_attempt" in tables:
            row = connection.execute(
                "SELECT attempt_id FROM execution_attempt ORDER BY attempt_number LIMIT 1"
            ).fetchone()
            if row:
                stats["attemptId"] = row["attempt_id"]
        if "runtime_event" in tables:
            columns = _table_columns(connection, "runtime_event")
            required = {"event_id", "sequence", "type", "occurred_at"}
            if required.issubset(columns):
                selected = [
                    name
                    for name in ("event_id", "run_id", "sequence", "type", "occurred_at", "correlation_id")
                    if name in columns
                ]
                query = f"SELECT {', '.join(selected)} FROM runtime_event ORDER BY sequence"
                for row in connection.execute(query):
                    operation = str(row["type"])
                    row_keys = set(row.keys())
                    events.append(
                        _make_event(
                            activity_id=activity_id,
                            task_id=task_id,
                            source="SQLITE",
                            source_ref_value=f"{reference}#runtime_event:{row['sequence']}",
                            source_identity=str(row["event_id"]),
                            source_sequence=int(row["sequence"]),
                            occurred_at_ms=parse_timestamp_ms(row["occurred_at"]),
                            operation=operation,
                            attempt_id=stats["attemptId"],
                            run_id=row["run_id"] if "run_id" in row_keys else None,
                            raw_locator={
                                "kind": "sqlite-row",
                                "path": database,
                                "table": "runtime_event",
                                "keyColumn": "event_id",
                                "keyValue": row["event_id"],
                            },
                        )
                    )
        if "tool_call" in tables:
            columns = _table_columns(connection, "tool_call")
            required = {"tool_call_id", "tool_name", "status", "requested_at"}
            selected = [
                name
                for name in (
                    "tool_call_id",
                    "run_id",
                    "step_id",
                    "tool_name",
                    "status",
                    "requested_at",
                    "started_at",
                    "completed_at",
                )
                if name in columns
            ]
            query = f"SELECT {', '.join(selected)} FROM tool_call ORDER BY requested_at"
            write_count = 0
            if required.issubset(columns):
                for row in connection.execute(query):
                    row_keys = set(row.keys())
                    requested = parse_timestamp_ms(row["requested_at"])
                    completed = parse_timestamp_ms(row["completed_at"]) if "completed_at" in row_keys else None
                    tool_name = str(row["tool_name"])
                    normalized_tool = tool_name.lower().replace(".", "_")
                    if normalized_tool.startswith(("file_write", "file_move", "file_delete")):
                        write_count += 1
                    operation = (
                        "file.read"
                        if normalized_tool.startswith(("file_read", "file_list"))
                        else "file.edit"
                        if normalized_tool.startswith(("file_write", "file_move", "file_delete"))
                        else "execution.run"
                        if normalized_tool == "execution_run"
                        else "tool.call"
                    )
                    events.append(
                        _make_event(
                            activity_id=activity_id,
                            task_id=task_id,
                            source="SQLITE",
                            source_ref_value=f"{reference}#tool_call:{row['tool_call_id']}",
                            source_identity=f"tool:{row['tool_call_id']}",
                            source_sequence=None,
                            occurred_at_ms=requested,
                            duration_ms=(
                                completed - requested if requested is not None and completed is not None else None
                            ),
                            operation=operation,
                            explicit_status=row["status"],
                            attempt_id=stats["attemptId"],
                            run_id=row["run_id"] if "run_id" in row_keys else None,
                            step_id=row["step_id"] if "step_id" in row_keys else None,
                            tool_call_id=row["tool_call_id"],
                            metadata={"toolName": tool_name},
                            raw_locator={
                                "kind": "sqlite-row",
                                "path": database,
                                "table": "tool_call",
                                "keyColumn": "tool_call_id",
                                "keyValue": row["tool_call_id"],
                            },
                        )
                    )
            stats["workspaceModified"] = write_count > 0
            stats["workspaceChangeCount"] = write_count
        connection.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        issues.append(
            DataIssue(
                "SQLITE_READ_FAILED", "ERROR", f"Runtime database could not be read: {type(exc).__name__}", reference
            )
        )
    return events, issues, stats


def _read_trace(
    trace: Path,
    archive_root: Path,
    root_alias: str,
    activity_id: str,
    task_id: str,
) -> tuple[list[TrajectoryEvent], list[DataIssue], dict[str, Any]]:
    events: list[TrajectoryEvent] = []
    issues: list[DataIssue] = []
    facts: dict[str, Any] = {"model": None, "provider": None, "providerErrors": 0, "runtimeRetries": 0}
    try:
        reference = source_ref(archive_root, trace, root_alias)
    except (OSError, UnsafeArchivePath):
        return events, [DataIssue("TRACE_PATH_UNSAFE", "ERROR", "Safe trace path escapes the archive")], facts
    try:
        with trace.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if len(line.encode("utf-8")) > MAX_JSONL_LINE_BYTES:
                    issues.append(
                        DataIssue(
                            "JSONL_LINE_TOO_LARGE",
                            "WARNING",
                            f"Trace line {line_number} was skipped",
                            f"{reference}#L{line_number}",
                        )
                    )
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    issues.append(
                        DataIssue(
                            "JSONL_LINE_INVALID",
                            "WARNING",
                            f"Trace line {line_number} is invalid JSON",
                            f"{reference}#L{line_number}",
                        )
                    )
                    continue
                operation = str(raw.get("operation") or raw.get("phase") or "unknown")
                metadata = _safe_metadata(raw.get("attributes"))
                if metadata.get("modelId") and facts["model"] is None:
                    facts["model"] = metadata["modelId"]
                if metadata.get("providerId") and operation.startswith("model.") and facts["provider"] is None:
                    facts["provider"] = metadata["providerId"]
                if operation == "model.error":
                    facts["providerErrors"] += 1
                if "retry" in operation or "repair" in operation:
                    facts["runtimeRetries"] += 1
                error_code = metadata.get("errorCode")
                events.append(
                    _make_event(
                        activity_id=activity_id,
                        task_id=task_id,
                        source="SAFE_TRACE",
                        source_ref_value=f"{reference}#L{line_number}",
                        source_identity=str(raw.get("traceId") or f"line:{line_number}"),
                        source_sequence=line_number,
                        occurred_at_ms=parse_timestamp_ms(raw.get("timestamp")),
                        operation=operation,
                        explicit_status="FAILURE" if raw.get("phase") == "ON_ERROR" else None,
                        attempt_id=raw.get("attemptId"),
                        run_id=raw.get("runId"),
                        step_id=raw.get("stepId"),
                        tool_call_id=raw.get("toolCallId"),
                        error_code=str(error_code) if error_code else None,
                        metadata=metadata,
                        raw_locator={"kind": "jsonl-line", "path": trace, "line": line_number},
                    )
                )
    except OSError as exc:
        issues.append(
            DataIssue("TRACE_READ_FAILED", "ERROR", f"Safe trace could not be read: {type(exc).__name__}", reference)
        )
    return events, issues, facts


def _read_transcript_fallback(
    transcript: Path,
    archive_root: Path,
    root_alias: str,
    activity_id: str,
    task_id: str,
) -> tuple[list[TrajectoryEvent], list[DataIssue]]:
    events: list[TrajectoryEvent] = []
    issues: list[DataIssue] = []
    try:
        reference = source_ref(archive_root, transcript, root_alias)
    except (OSError, UnsafeArchivePath):
        return events, [DataIssue("TRANSCRIPT_PATH_UNSAFE", "ERROR", "Transcript path escapes the archive")]
    try:
        with transcript.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if len(line.encode("utf-8")) > MAX_JSONL_LINE_BYTES:
                    issues.append(
                        DataIssue(
                            "JSONL_LINE_TOO_LARGE",
                            "WARNING",
                            f"Transcript line {line_number} was skipped",
                            f"{reference}#L{line_number}",
                        )
                    )
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    issues.append(
                        DataIssue(
                            "JSONL_LINE_INVALID",
                            "WARNING",
                            f"Transcript line {line_number} is invalid JSON",
                            f"{reference}#L{line_number}",
                        )
                    )
                    continue
                operation = str(raw.get("eventType") or "unknown")
                events.append(
                    _make_event(
                        activity_id=activity_id,
                        task_id=task_id,
                        source="TRANSCRIPT",
                        source_ref_value=f"{reference}#L{line_number}",
                        source_identity=str(raw.get("eventId") or f"line:{line_number}"),
                        source_sequence=raw.get("sequence") if isinstance(raw.get("sequence"), int) else line_number,
                        occurred_at_ms=parse_timestamp_ms(raw.get("occurredAt")),
                        operation=operation,
                        run_id=raw.get("runId"),
                        raw_locator={"kind": "jsonl-line", "path": transcript, "line": line_number},
                    )
                )
    except OSError as exc:
        issues.append(
            DataIssue(
                "TRANSCRIPT_READ_FAILED", "ERROR", f"Transcript could not be read: {type(exc).__name__}", reference
            )
        )
    return events, issues


def _harbor_events(
    result: dict[str, Any],
    result_path: Path,
    archive_root: Path,
    root_alias: str,
    activity_id: str,
    task_id: str,
) -> list[TrajectoryEvent]:
    events: list[TrajectoryEvent] = []
    try:
        reference = source_ref(archive_root, result_path, root_alias)
    except (OSError, UnsafeArchivePath):
        return events
    moments = (
        ("harbor.trial.started", result.get("started_at"), "RUNNING"),
        ("harbor.agent.started", (result.get("agent_execution") or {}).get("started_at"), "RUNNING"),
        (
            "harbor.agent.completed",
            (result.get("agent_execution") or {}).get("finished_at"),
            "SUCCESS" if not result.get("exception_info") else "FAILURE",
        ),
        ("verifier.started", (result.get("verifier") or {}).get("started_at"), "RUNNING"),
        ("verifier.completed", (result.get("verifier") or {}).get("finished_at"), "SUCCESS"),
        ("harbor.trial.completed", result.get("finished_at"), "SUCCESS"),
    )
    for index, (operation, timestamp, status) in enumerate(moments, start=1):
        if timestamp is None:
            continue
        events.append(
            _make_event(
                activity_id=activity_id,
                task_id=task_id,
                source="HARBOR",
                source_ref_value=reference,
                source_identity=f"{result.get('id', 'trial')}:{operation}",
                source_sequence=index,
                occurred_at_ms=parse_timestamp_ms(timestamp),
                operation=operation,
                explicit_status=status,
                raw_locator={"kind": "json-file", "path": result_path},
            )
        )
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    if "reward" in rewards:
        reward = rewards["reward"]
        events.append(
            _make_event(
                activity_id=activity_id,
                task_id=task_id,
                source="VERIFIER",
                source_ref_value=reference,
                source_identity=f"{result.get('id', 'trial')}:reward",
                source_sequence=99,
                occurred_at_ms=parse_timestamp_ms((result.get("verifier") or {}).get("finished_at")),
                operation="verifier.result",
                explicit_status="PASS" if float(reward) > 0 else "FAILURE",
                metadata={"successful": float(reward) > 0},
                raw_locator={"kind": "json-file", "path": result_path},
            )
        )
    return events


def normalize_events(events: Iterable[TrajectoryEvent]) -> list[TrajectoryEvent]:
    ordered = sorted(
        events,
        key=lambda event: (
            event.occurred_at_ms is None,
            event.occurred_at_ms if event.occurred_at_ms is not None else 0,
            event.source_sequence if event.source_sequence is not None else 2**31,
            event.event_id,
        ),
    )
    return [replace(event, sequence=index) for index, event in enumerate(ordered, start=1)]


def load_trajectory(
    *,
    archive_root: Path,
    root_alias: str,
    activity_id: str,
    task_id: str,
    trial_directory: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    issues: list[DataIssue] = []
    events: list[TrajectoryEvent] = []
    agent_directory = trial_directory / "agent"
    database = agent_directory / "haifa-runtime.db"
    sqlite_stats: dict[str, Any] = {}
    sqlite_event_count = 0
    if database.is_file():
        sqlite_events, sqlite_issues, sqlite_stats = _read_sqlite(
            database, archive_root, root_alias, activity_id, task_id
        )
        events.extend(sqlite_events)
        sqlite_event_count = len(sqlite_events)
        issues.extend(sqlite_issues)
    else:
        issues.append(DataIssue("SQLITE_MISSING", "WARNING", "Runtime database is unavailable"))

    trace = agent_directory / "haifa-trace.jsonl"
    trace_facts: dict[str, Any] = {"model": None, "provider": None, "providerErrors": 0, "runtimeRetries": 0}
    if trace.is_file():
        trace_events, trace_issues, trace_facts = _read_trace(trace, archive_root, root_alias, activity_id, task_id)
        events.extend(trace_events)
        issues.extend(trace_issues)
    else:
        issues.append(DataIssue("TRACE_MISSING", "WARNING", "Safe trace is unavailable"))

    transcripts = (
        sorted((agent_directory / "haifa-transcripts").glob("*.jsonl"))
        if (agent_directory / "haifa-transcripts").is_dir()
        else []
    )
    if sqlite_event_count == 0:
        for transcript in transcripts:
            transcript_events, transcript_issues = _read_transcript_fallback(
                transcript, archive_root, root_alias, activity_id, task_id
            )
            events.extend(transcript_events)
            issues.extend(transcript_issues)
    elif not transcripts:
        issues.append(DataIssue("TRANSCRIPT_MISSING", "WARNING", "Transcript projection is unavailable"))

    result_path = trial_directory / "result.json"
    if result_path.is_file():
        events.extend(_harbor_events(result, result_path, archive_root, root_alias, activity_id, task_id))

    normalized = normalize_events(events)
    raw_locators = {event.event_id: event.raw_locator for event in normalized if event.raw_locator is not None}
    focus = next((event.event_id for event in normalized if event.error_code), None)
    if focus is None:
        focus = next(
            (event.event_id for event in normalized if event.category == "ERROR" or event.status == "FAILURE"), None
        )

    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    verifier_status = (
        "PASS" if reward is not None and float(reward) > 0 else "FAIL" if reward is not None else "UNAVAILABLE"
    )
    source_coverage = {
        "sqlite": database.is_file(),
        "safeTrace": trace.is_file(),
        "transcript": bool(transcripts),
        "harborResult": result_path.is_file(),
        "verifier": reward is not None,
    }
    return {
        "normalizerVersion": NORMALIZER_VERSION,
        "events": [event.to_dict() for event in normalized],
        "focusEventId": focus,
        "sourceCoverage": source_coverage,
        "runtimeFacts": {
            **sqlite_stats,
            **trace_facts,
            "lastVerification": verifier_status,
            "lastVerificationSource": "Harbor verifier" if reward is not None else None,
        },
        "issues": [issue.to_dict() for issue in issues],
        "_rawLocators": raw_locators,
    }
