# M1 technical design

Status: Accepted for implementation
Date: 2026-08-21

## Decisions

1. Use Python 3.12 standard-library HTTP, SQLite, CSV, and JSON support plus an offline static Web UI. There are no runtime third-party dependencies or CDN assets.
2. Open every runtime database through SQLite URI `mode=ro&immutable=1`; issue explicit-column `SELECT` statements only.
3. Keep source readers behind a versioned `TrajectoryNormalizer`. Ordinary browser responses consume `TrajectoryEvent`;
   the event-scoped Raw endpoint may return the corresponding complete source row or JSON object on demand.
4. Discover only archives below explicitly configured roots. An activity becomes discoverable when schema-version-1
   `integrity/finalization.json` exists and its `status` is `COMPLETE`. Legacy `inventory.json` markers are not supported.
   Bounded rescans make later archives visible without process restart.
5. Use deterministic semantic grouping. Unknown source events remain `UNKNOWN`; no LLM summarizes or repairs evidence.
6. Expose only allowlisted safe metadata in Activity, Task, Semantic, and Operations responses. Raw is an explicit
   loopback-only single-user exception and returns complete archived evidence without field redaction or truncation.
7. Generate synthetic redacted fixtures at test time and verify archive file hashes remain unchanged after browsing.

## Data flow

```text
configured roots
  -> bounded Activity discovery
  -> finalization / primary results index
  -> Harbor Trial locator
  -> SQLite / transcript / safe trace readers
  -> TrajectoryNormalizer v1
  -> safe projection API + event-scoped Raw API
  -> Activity / Task / Trajectory Web views
```

## Continuous activity discovery

The service periodically rescans configured roots and also accepts an explicit API refresh. Activity identity comes from the
finalized `runId`; an identical copied archive is deduplicated by finalization digest. A conflicting archive with the same
`runId` receives a deterministic content-digest suffix and a data-quality warning. Markers with unsupported schema versions
fail visibly, while markers whose status is not `COMPLETE` remain outside the activity catalog and produce a catalog issue.
Activities are sorted by normalized `archivedAtUtc` descending. Because current finalization schema v1 does not require that
field, the marker's filesystem modification time is the UTC fallback and is also exposed as the displayed archive time.

Discovery is deliberately different from live monitoring. Files inside a discovered archive are treated as immutable-by-convention. A future live mode would require snapshot consistency, partial-line handling, WAL semantics, authentication, and a separate product decision.

## Security boundary

- loopback-only HTTP listener;
- no remote fonts, scripts, telemetry, or API calls;
- no absolute host paths in API responses;
- no prompt, reasoning, credentials, full provider response, hidden answer, SQLite BLOB, or unrestricted JSON payload in
  ordinary APIs, logs, search, or default views;
- the Raw event API can return all of those fields when they exist in an archived source; non-UTF-8 BLOB values use complete
  Base64 representation, responses use `Cache-Control: no-store`, and the Viewer does not log, export, or persist them;
- source path resolution rejects absolute, parent-traversal, and symlink escape;
- one bad archive, trial, database, or JSONL line is isolated from other activities.
- Runtime-aware JSONL parsing recognizes only self-describing `haifa-runtime-trace` schema version 1 records. It does not
  maintain a Legacy V0 semantic adapter; older records stay available as generic/raw JSONL.
- V1 integrity is evaluated per `streamId`: input order, sequence gaps and duplicates remain fail-visible, display order uses
  `sequence`, and a missing or inconsistent `stream.completed` marks the stream `TRACE_INCOMPLETE` without changing Run status.
