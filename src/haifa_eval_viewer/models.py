from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DataIssue:
    code: str
    severity: str
    message: str
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    event_id: str
    activity_id: str
    task_id: str
    attempt_id: str | None
    run_id: str | None
    sequence: int
    source_sequence: int | None
    occurred_at_ms: int | None
    duration_ms: int | None
    category: str
    event_type: str
    operation: str
    status: str
    source: str
    source_ref: str
    summary: str
    semantic_phase: str
    error_code: str | None = None
    step_id: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    payload_ref: str | None = None
    raw_locator: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "activityId": self.activity_id,
            "taskId": self.task_id,
            "attemptId": self.attempt_id,
            "runId": self.run_id,
            "sequence": self.sequence,
            "sourceSequence": self.source_sequence,
            "occurredAtMs": self.occurred_at_ms,
            "durationMs": self.duration_ms,
            "category": self.category,
            "type": self.event_type,
            "operation": self.operation,
            "status": self.status,
            "source": self.source,
            "sourceRef": self.source_ref,
            "summary": self.summary,
            "semanticPhase": self.semantic_phase,
            "errorCode": self.error_code,
            "stepId": self.step_id,
            "toolCallId": self.tool_call_id,
            "metadata": self.metadata,
            "payloadRef": self.payload_ref,
        }


@dataclass(slots=True)
class TaskRecord:
    public: dict[str, Any]
    trial_directory: Path | None
    result: dict[str, Any]
    trajectory: dict[str, Any] | None = None


@dataclass(slots=True)
class ActivityRecord:
    activity_id: str
    archive_directory: Path
    root_alias: str
    public: dict[str, Any]
    tasks: list[TaskRecord]
    issues: list[DataIssue]
    fingerprint: tuple[Any, ...]
    tasks_loaded: bool = False
