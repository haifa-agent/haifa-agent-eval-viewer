from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ActivityRecord, DataIssue, TaskRecord
from .security import UnsafeArchivePath, ensure_within, resolve_archive_relative
from .trajectory import load_raw_payload, load_trajectory

MAX_DISCOVERY_DEPTH = 8
MAX_DISCOVERY_DIRECTORIES = 20_000
MAX_JSON_BYTES = 16 * 1024 * 1024
ACTIVITY_MARKER = Path("integrity") / "finalization.json"
SUPPORTED_FINALIZATION_SCHEMA_VERSION = 1


class CatalogError(RuntimeError):
    pass


class ActivityNotFound(CatalogError):
    pass


class TaskNotFound(CatalogError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _archive_time(finalization: dict[str, Any], marker: Path) -> str:
    archived_at = finalization.get("archivedAtUtc")
    if isinstance(archived_at, str) and archived_at.strip():
        try:
            parsed = datetime.fromisoformat(archived_at.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return datetime.fromtimestamp(marker.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON file exceeds the Viewer size limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _safe_slug(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return rendered[:160] or "activity"


def _fingerprint(paths: Iterable[Path]) -> tuple[Any, ...]:
    values: list[Any] = []
    for path in sorted(set(paths)):
        try:
            stat = path.stat()
            values.append((path.name, stat.st_size, stat.st_mtime_ns))
        except OSError:
            values.append((path.name, None, None))
    return tuple(values)


class ArchiveCatalog:
    def __init__(self, data_roots: Iterable[Path | str], refresh_interval_seconds: float = 5.0):
        roots = [Path(root).expanduser().resolve(strict=False) for root in data_roots]
        if not roots:
            raise ValueError("at least one data root is required")
        self._roots = tuple(roots)
        self._refresh_interval_seconds = max(0.0, refresh_interval_seconds)
        self._lock = threading.RLock()
        self._activities: dict[str, ActivityRecord] = {}
        self._last_scan_monotonic = 0.0
        self._last_scanned_at: str | None = None
        self._catalog_issues: list[DataIssue] = []

    def refresh(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_scan_monotonic < self._refresh_interval_seconds:
                return self._catalog_metadata()
            discovered: list[tuple[Path, Path, str]] = []
            issues: list[DataIssue] = []
            for index, root in enumerate(self._roots, start=1):
                alias = f"root-{index}"
                if not root.is_dir():
                    issues.append(
                        DataIssue(
                            "DATA_ROOT_UNAVAILABLE", "ERROR", f"Configured data root {alias} is unavailable", alias
                        )
                    )
                    continue
                try:
                    if (root / ACTIVITY_MARKER).is_file():
                        discovered.append((root, root, alias))
                    else:
                        discovered.extend((activity, root, alias) for activity in self._discover_under(root))
                except (OSError, UnsafeArchivePath) as exc:
                    issues.append(
                        DataIssue(
                            "DATA_ROOT_SCAN_FAILED", "ERROR", f"Could not scan {alias}: {type(exc).__name__}", alias
                        )
                    )

            next_records: dict[str, ActivityRecord] = {}
            identities: dict[str, str] = {}
            for archive, configured_root, alias in discovered:
                try:
                    finalization_path = archive / ACTIVITY_MARKER
                    finalization_bytes = finalization_path.read_bytes()
                    if len(finalization_bytes) > MAX_JSON_BYTES:
                        raise ValueError("finalization exceeds the Viewer size limit")
                    finalization = json.loads(finalization_bytes)
                    if not isinstance(finalization, dict):
                        raise ValueError("finalization must be an object")
                    if finalization.get("schemaVersion") != SUPPORTED_FINALIZATION_SCHEMA_VERSION:
                        raise ValueError("unsupported finalization schema version")
                    if finalization.get("status") != "COMPLETE":
                        issues.append(
                            DataIssue(
                                "ACTIVITY_NOT_COMPLETE",
                                "WARNING",
                                "A finalization marker was ignored because its status is not COMPLETE",
                                alias,
                            )
                        )
                        continue
                    finalization_digest = hashlib.sha256(finalization_bytes).hexdigest()
                    base_identity = _safe_slug(
                        str(finalization.get("runId") or finalization.get("evalId") or finalization_digest[:16])
                    )
                    activity_id = base_identity
                    if base_identity in identities:
                        if identities[base_identity] == finalization_digest:
                            continue
                        activity_id = f"{base_identity}--{finalization_digest[:8]}"
                    identities[activity_id] = finalization_digest
                    paths = [
                        finalization_path,
                        *self._primary_csv_paths(archive),
                    ]
                    fingerprint = _fingerprint(paths)
                    previous = self._activities.get(activity_id)
                    if previous and previous.fingerprint == fingerprint and previous.archive_directory == archive:
                        next_records[activity_id] = previous
                        continue
                    record = self._create_activity_record(
                        activity_id=activity_id,
                        archive=archive,
                        configured_root=configured_root,
                        root_alias=alias,
                        finalization=finalization,
                        fingerprint=fingerprint,
                    )
                    if "--" in activity_id:
                        record.issues.append(
                            DataIssue(
                                "ACTIVITY_ID_CONFLICT",
                                "WARNING",
                                "Another archive uses the same runId with different finalization content",
                                activity_id,
                            )
                        )
                    next_records[activity_id] = record
                except (OSError, json.JSONDecodeError, ValueError, UnsafeArchivePath) as exc:
                    issues.append(
                        DataIssue(
                            "ACTIVITY_DISCOVERY_FAILED",
                            "ERROR",
                            f"An activity marker could not be loaded: {type(exc).__name__}",
                            alias,
                        )
                    )
            self._activities = next_records
            self._catalog_issues = issues
            self._last_scan_monotonic = now
            self._last_scanned_at = _utc_now()
            return self._catalog_metadata()

    @property
    def root_count(self) -> int:
        return len(self._roots)

    def list_activities(self, force_refresh: bool = False) -> dict[str, Any]:
        self.refresh(force_refresh)
        with self._lock:
            activities = [record.public for record in self._activities.values()]
            activities.sort(key=lambda item: (item.get("archivedAtUtc") or "", item["activityId"]), reverse=True)
            return {**self._catalog_metadata(), "activities": activities}

    def get_activity(self, activity_id: str, load_tasks: bool = True) -> dict[str, Any]:
        self.refresh()
        with self._lock:
            record = self._activities.get(activity_id)
            if record is None:
                raise ActivityNotFound(activity_id)
            if load_tasks:
                self._ensure_tasks_loaded(record)
            return dict(record.public)

    def list_tasks(self, activity_id: str, filters: dict[str, str] | None = None) -> dict[str, Any]:
        self.refresh()
        with self._lock:
            record = self._activities.get(activity_id)
            if record is None:
                raise ActivityNotFound(activity_id)
            self._ensure_tasks_loaded(record)
            all_tasks = [task.public for task in record.tasks]
            selected = self._apply_filters(all_tasks, filters or {})
            sort = (filters or {}).get("sort", "task")
            reverse = sort.endswith("-desc")
            sort_key = sort.removesuffix("-desc").removesuffix("-asc")
            key_map = {
                "duration": lambda item: item.get("durationSeconds") if item.get("durationSeconds") is not None else -1,
                "modelCalls": lambda item: item.get("modelCalls") if item.get("modelCalls") is not None else -1,
                "toolCalls": lambda item: item.get("toolCalls") if item.get("toolCalls") is not None else -1,
                "retries": lambda item: item.get("runtimeRetries") if item.get("runtimeRetries") is not None else -1,
                "task": lambda item: item.get("taskId") or "",
            }
            selected.sort(key=key_map.get(sort_key, key_map["task"]), reverse=reverse)
            return {
                "activityId": activity_id,
                "total": len(all_tasks),
                "filtered": len(selected),
                "filters": filters or {},
                "facets": self._facets(all_tasks),
                "tasks": selected,
            }

    def get_task(self, activity_id: str, trial_name: str) -> dict[str, Any]:
        self.refresh()
        with self._lock:
            record = self._activities.get(activity_id)
            if record is None:
                raise ActivityNotFound(activity_id)
            self._ensure_tasks_loaded(record)
            task = next((candidate for candidate in record.tasks if candidate.public["trialName"] == trial_name), None)
            if task is None:
                raise TaskNotFound(trial_name)
            trajectory = {key: value for key, value in (task.trajectory or {}).items() if not key.startswith("_")}
            return {**task.public, "trajectory": trajectory}

    def get_raw_event(self, activity_id: str, trial_name: str, event_id: str) -> dict[str, Any]:
        self.refresh()
        with self._lock:
            record = self._activities.get(activity_id)
            if record is None:
                raise ActivityNotFound(activity_id)
            self._ensure_tasks_loaded(record)
            task = next((candidate for candidate in record.tasks if candidate.public["trialName"] == trial_name), None)
            if task is None or task.trajectory is None:
                raise TaskNotFound(trial_name)
            raw_locators = task.trajectory.get("_rawLocators") or {}
            events = task.trajectory.get("events") or []
            event = next((candidate for candidate in events if candidate.get("eventId") == event_id), None)
            if event is None or event_id not in raw_locators:
                raise TaskNotFound(event_id)
            return {
                "eventId": event_id,
                "source": event.get("source"),
                "sourceRef": event.get("sourceRef"),
                "raw": load_raw_payload(raw_locators[event_id]),
            }

    def _catalog_metadata(self) -> dict[str, Any]:
        return {
            "lastScannedAt": self._last_scanned_at,
            "activityCount": len(self._activities),
            "issues": [issue.to_dict() for issue in self._catalog_issues],
        }

    def _discover_under(self, root: Path) -> list[Path]:
        discovered: list[Path] = []
        for directory_count, (current, directories, files) in enumerate(os.walk(root, followlinks=False), start=1):
            if directory_count > MAX_DISCOVERY_DIRECTORIES:
                raise OSError("configured root exceeds the bounded directory scan limit")
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts)
            directories[:] = [name for name in directories if name not in {".git", ".venv", "node_modules", "target"}]
            if depth >= MAX_DISCOVERY_DEPTH:
                directories[:] = []
            if current_path.name == "integrity" and "finalization.json" in files:
                activity = ensure_within(root, current_path.parent)
                discovered.append(activity)
                directories[:] = []
        return discovered

    def _create_activity_record(
        self,
        *,
        activity_id: str,
        archive: Path,
        configured_root: Path,
        root_alias: str,
        finalization: dict[str, Any],
        fingerprint: tuple[Any, ...],
    ) -> ActivityRecord:
        issues: list[DataIssue] = []
        planned_trials = self._integer(finalization.get("plannedTrials"))
        counts = self._preview_counts(archive, planned_trials)
        safety_findings = finalization.get("safetyFindings")
        relative_archive = archive.relative_to(configured_root).as_posix() or "."
        public = {
            "activityId": activity_id,
            "evalId": finalization.get("evalId"),
            "runId": finalization.get("runId"),
            "dataset": finalization.get("dataset"),
            "archivedAtUtc": _archive_time(finalization, archive / ACTIVITY_MARKER),
            "classification": "private-raw-evaluation-evidence",
            "counts": counts,
            "fileSummary": {"checksummedFiles": self._integer(finalization.get("checksummedFiles"))},
            "integritySummary": {
                "status": finalization.get("status"),
                "observedTrials": self._integer(finalization.get("observedTrials")),
                "invalidTrials": self._integer(finalization.get("invalidTrials")),
                "evidenceIssues": self._integer(finalization.get("evidenceIssues")),
                "safetyFindings": len(safety_findings) if isinstance(safety_findings, list) else None,
            },
            "rootAlias": root_alias,
            "archiveAlias": f"{root_alias}/{relative_archive}",
            "tasksLoaded": False,
            "discoveredAt": _utc_now(),
            "dataQuality": self._quality(issues),
        }
        return ActivityRecord(activity_id, archive, root_alias, public, [], issues, fingerprint)

    def _preview_counts(self, archive: Path, planned_trials: int | None) -> dict[str, int | None]:
        counts: dict[str, int | None] = {
            "planned": planned_trials,
            "pass": None,
            "fail": None,
            "error": None,
            "agentExceptions": None,
            "cleanCompletion": None,
        }
        csv_paths = self._primary_csv_paths(archive)
        if not csv_paths:
            return counts
        try:
            rows: list[dict[str, str]] = []
            for csv_path in csv_paths:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows.extend(dict(row) for row in csv.DictReader(handle))
        except (OSError, csv.Error):
            return counts
        statuses = Counter((row.get("status") or "ERROR").upper() for row in rows)
        counts.update(
            {
                "planned": planned_trials if planned_trials is not None else len(rows),
                "pass": statuses["PASS"],
                "fail": statuses["FAIL"],
                "error": statuses["ERROR"],
                "agentExceptions": sum(
                    self._integer(row.get("exit_code")) not in (None, 0) or bool(row.get("error_type")) for row in rows
                ),
                "cleanCompletion": sum(
                    self._integer(row.get("exit_code")) == 0 and not row.get("error_type") for row in rows
                ),
            }
        )
        return counts

    def _primary_csv_paths(self, archive: Path) -> list[Path]:
        reports = archive / "reports"
        if not reports.is_dir():
            return []
        primary = sorted(reports.glob("primary*.csv"))
        if primary:
            return primary
        direct = reports / "results.csv"
        if direct.is_file():
            return [direct]
        return sorted(reports.glob("*/results.csv"))

    def _ensure_tasks_loaded(self, record: ActivityRecord) -> None:
        if record.tasks_loaded:
            return
        csv_paths = self._primary_csv_paths(record.archive_directory)
        if not csv_paths:
            record.issues.append(DataIssue("PRIMARY_INDEX_MISSING", "ERROR", "No primary result CSV was found"))
            record.public["dataQuality"] = self._quality(record.issues)
            record.tasks_loaded = True
            record.public["tasksLoaded"] = True
            return
        rows: list[dict[str, str]] = []
        for csv_path in csv_paths:
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows.extend(dict(row) for row in csv.DictReader(handle))
            except (OSError, csv.Error) as exc:
                record.issues.append(
                    DataIssue(
                        "PRIMARY_INDEX_INVALID", "ERROR", f"Primary result CSV could not be read: {type(exc).__name__}"
                    )
                )
        result_index, duplicate_trials = self._trial_result_index(record.archive_directory)
        if duplicate_trials:
            record.issues.append(
                DataIssue("TRIAL_ID_DUPLICATE", "WARNING", f"{len(duplicate_trials)} Trial names occur more than once")
            )
        tasks: list[TaskRecord] = []
        for row in rows:
            trial_name = (row.get("trial_path") or row.get("trial_name") or "").strip()
            trial_directory = self._locate_trial(record.archive_directory, trial_name, result_index)
            result: dict[str, Any] = {}
            task_issues: list[DataIssue] = []
            if trial_directory is None:
                task_issues.append(
                    DataIssue("TRIAL_NOT_FOUND", "ERROR", "Primary Trial directory could not be located")
                )
            else:
                try:
                    result = _read_json(trial_directory / "result.json")
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    task_issues.append(
                        DataIssue(
                            "TRIAL_RESULT_INVALID", "ERROR", f"Harbor result could not be read: {type(exc).__name__}"
                        )
                    )
            task_id = row.get("task_id") or result.get("task_name") or trial_name
            trajectory = (
                load_trajectory(
                    archive_root=record.archive_directory,
                    root_alias=record.root_alias,
                    activity_id=record.activity_id,
                    task_id=str(task_id),
                    trial_directory=trial_directory,
                    result=result,
                )
                if trial_directory is not None
                else {
                    "normalizerVersion": 1,
                    "events": [],
                    "focusEventId": None,
                    "sourceCoverage": {},
                    "runtimeFacts": {},
                    "issues": [],
                }
            )
            task_issues.extend(DataIssue(**issue) for issue in trajectory["issues"])
            public = self._task_public(row, result, trial_name, str(task_id), trajectory, task_issues)
            tasks.append(TaskRecord(public, trial_directory, result, trajectory))
        record.tasks = tasks
        record.tasks_loaded = True
        record.public["tasksLoaded"] = True
        record.public["counts"] = self._counts(tasks)
        record.public["distributions"] = self._distributions(tasks)
        record.public["dataQuality"] = self._quality(record.issues)

    def _trial_result_index(self, archive: Path) -> tuple[dict[str, Path], set[str]]:
        index: dict[str, Path] = {}
        duplicates: set[str] = set()
        for current, directories, files in os.walk(archive, followlinks=False):
            directories[:] = [name for name in directories if not name.startswith(".")]
            if "result.json" not in files:
                continue
            path = Path(current) / "result.json"
            try:
                ensure_within(archive, path)
                value = _read_json(path)
            except (OSError, json.JSONDecodeError, ValueError, UnsafeArchivePath):
                continue
            trial_name = value.get("trial_name")
            if not isinstance(trial_name, str) or not trial_name:
                continue
            if trial_name in index:
                duplicates.add(trial_name)
                continue
            index[trial_name] = Path(current)
        return index, duplicates

    def _locate_trial(self, archive: Path, supplied: str, index: dict[str, Path]) -> Path | None:
        if not supplied:
            return None
        supplied_path = Path(supplied)
        if supplied_path.is_absolute() or ".." in supplied_path.parts:
            return None
        if len(supplied_path.parts) > 1:
            try:
                candidate = resolve_archive_relative(archive, supplied)
                return candidate if (candidate / "result.json").is_file() else None
            except (OSError, UnsafeArchivePath):
                return None
        return index.get(supplied)

    def _task_public(
        self,
        row: dict[str, str],
        result: dict[str, Any],
        trial_name: str,
        task_id: str,
        trajectory: dict[str, Any],
        issues: list[DataIssue],
    ) -> dict[str, Any]:
        runtime = trajectory.get("runtimeFacts") or {}
        exit_code = self._integer(row.get("exit_code"))
        error_type = row.get("error_type") or ((result.get("exception_info") or {}).get("exception_type"))
        verifier_status = (row.get("status") or "UNAVAILABLE").upper()
        focus = trajectory.get("focusEventId")
        focus_event = next((event for event in trajectory.get("events", []) if event.get("eventId") == focus), None)
        model = (
            row.get("model")
            or runtime.get("model")
            or ((result.get("agent_info") or {}).get("model_info") or {}).get("name")
        )
        return {
            "taskId": task_id,
            "trialName": trial_name,
            "language": row.get("language") or "unknown",
            "candidate": row.get("candidate") or (result.get("agent_info") or {}).get("name") or "unknown",
            "attempt": self._integer(row.get("attempt")),
            "role": "PRIMARY",
            "verifierStatus": verifier_status,
            "reward": self._number(row.get("reward")),
            "agentStatus": "CLEAN" if exit_code == 0 and not error_type else "EXCEPTION",
            "agentRunStatus": runtime.get("agentRunStatus") or "UNAVAILABLE",
            "exitCode": exit_code,
            "errorType": error_type or None,
            "errorCode": focus_event.get("errorCode") if focus_event else None,
            "durationSeconds": self._number(row.get("duration_seconds")),
            "model": model,
            "provider": runtime.get("provider"),
            "modelCalls": runtime.get("modelCalls"),
            "toolCalls": runtime.get("toolCalls"),
            "runtimeRetries": runtime.get("runtimeRetries", 0),
            "providerErrors": runtime.get("providerErrors", 0),
            "workspaceModified": runtime.get("workspaceModified"),
            "workspaceChangeCount": runtime.get("workspaceChangeCount"),
            "lastVerification": runtime.get("lastVerification"),
            "lastVerificationSource": runtime.get("lastVerificationSource"),
            "focusEventId": focus,
            "trajectoryEventCount": len(trajectory.get("events", [])),
            "sourceCoverage": trajectory.get("sourceCoverage") or {},
            "dataQuality": self._quality(issues),
        }

    @staticmethod
    def _integer(value: Any) -> int | None:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _quality(issues: list[DataIssue]) -> dict[str, Any]:
        severity = "ERROR" if any(issue.severity == "ERROR" for issue in issues) else "WARNING" if issues else "OK"
        return {"status": severity, "issues": [issue.to_dict() for issue in issues]}

    @staticmethod
    def _counts(tasks: list[TaskRecord]) -> dict[str, int]:
        return {
            "planned": len(tasks),
            "pass": sum(task.public["verifierStatus"] == "PASS" for task in tasks),
            "fail": sum(task.public["verifierStatus"] == "FAIL" for task in tasks),
            "error": sum(task.public["verifierStatus"] == "ERROR" for task in tasks),
            "agentExceptions": sum(task.public["agentStatus"] == "EXCEPTION" for task in tasks),
            "cleanCompletion": sum(task.public["agentStatus"] == "CLEAN" for task in tasks),
        }

    @staticmethod
    def _distributions(tasks: list[TaskRecord]) -> dict[str, Any]:
        errors = Counter(task.public.get("errorCode") or task.public.get("errorType") or "NONE" for task in tasks)
        languages: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "total": 0})
        for task in tasks:
            language = task.public.get("language") or "unknown"
            languages[language]["total"] += 1
            if task.public.get("verifierStatus") == "PASS":
                languages[language]["pass"] += 1
        return {"errors": dict(errors), "languages": dict(sorted(languages.items()))}

    @staticmethod
    def _facets(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        keys = ("language", "verifierStatus", "agentStatus", "model", "provider", "role")
        facets = {key: dict(Counter(str(task.get(key) or "UNAVAILABLE") for task in tasks)) for key in keys}
        facets["errorCode"] = dict(
            Counter(str(task.get("errorCode") or task.get("errorType") or "UNAVAILABLE") for task in tasks)
        )
        return facets

    @staticmethod
    def _apply_filters(tasks: list[dict[str, Any]], filters: dict[str, str]) -> list[dict[str, Any]]:
        mappings = {
            "language": "language",
            "verifier": "verifierStatus",
            "agent": "agentStatus",
            "candidate": "candidate",
            "role": "role",
            "model": "model",
            "provider": "provider",
        }
        selected = list(tasks)
        for query_name, field_name in mappings.items():
            expected = filters.get(query_name)
            if expected:
                selected = [
                    task for task in selected if str(task.get(field_name) or "UNAVAILABLE").lower() == expected.lower()
                ]
        if filters.get("errorCode"):
            expected_error = filters["errorCode"].lower()
            selected = [
                task
                for task in selected
                if str(task.get("errorCode") or task.get("errorType") or "UNAVAILABLE").lower() == expected_error
            ]
        try:
            if filters.get("durationMin"):
                minimum = float(filters["durationMin"])
                selected = [
                    task
                    for task in selected
                    if task.get("durationSeconds") is not None and task["durationSeconds"] >= minimum
                ]
            if filters.get("durationMax"):
                maximum = float(filters["durationMax"])
                selected = [
                    task
                    for task in selected
                    if task.get("durationSeconds") is not None and task["durationSeconds"] <= maximum
                ]
            if filters.get("retryMin"):
                minimum_retry = int(filters["retryMin"])
                selected = [
                    task
                    for task in selected
                    if task.get("runtimeRetries") is not None and task["runtimeRetries"] >= minimum_retry
                ]
        except ValueError:
            return []
        return selected
