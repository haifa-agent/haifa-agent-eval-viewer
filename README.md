# Haifa Eval Viewer

Haifa Eval Viewer is a private, read-only Web application for investigating Coding Agent evaluation runs. It answers:

- what happened in an evaluation activity;
- which tasks passed verification but ended with an Agent exception;
- where a task trajectory first failed;
- which SQLite, transcript, trace, Harbor, and verifier evidence supports the conclusion.

The first release is intentionally not an evaluation runner, dashboard platform, leaderboard, replay engine, or AI root-cause analyst.

## Current scope

- discovers multiple archived Evaluation Activities below explicitly configured data roots;
- refreshes the catalog so newly archived activities appear without restarting the service;
- reads primary result CSV, inventory, integrity audit, Harbor Trial result, SQLite, transcript, and safe trace evidence;
- keeps Verifier correctness and Agent completion as separate states;
- normalizes evidence into a source-independent `TrajectoryEvent` projection;
- provides Activity overview, composable task filters, Task status matrix, and Semantic / Operations / Raw trajectory views;
- loads the complete source row or JSON object for one selected event in Raw mode, including private fields present in the archive;
- includes a framework-free JSONL Viewer for opening, dropping, or pasting arbitrary JSONL into timeline, field, tree,
  and raw-line views; only the latest self-describing Haifa Runtime Trace V1 receives Runtime semantics;
- listens on loopback only and makes no browser or server network requests to third parties.

Live tailing of an evaluation still in progress is not part of this milestone. The discovery boundary is a completed archive
containing a schema-version-1 `integrity/finalization.json` whose `status` is `COMPLETE`. Legacy inventory-only archives are
not discovered. Activities are ordered by archive completion time, newest first; when the finalization payload does not
provide `archivedAtUtc`, the Viewer uses the finalization marker's filesystem modification time in UTC.

## Run locally

Requirements: Python 3.12 and `uv`.

```powershell
.\scripts\eval-viewer.ps1 serve --data-root D:\path\to\run_data --port 8765
```

```bash
./scripts/eval-viewer.sh serve --data-root /path/to/run_data --port 8765
```

Then open `http://127.0.0.1:8765`.

The standalone JSONL utility is available at `http://127.0.0.1:8765/jsonl-viewer.html`. It also works without the Python
server: directly open `src/haifa_eval_viewer/web/jsonl-viewer.html` in a browser. Parsing stays in the current browser tab;
the page does not upload or persist opened, dropped, or pasted content. V1 streams are grouped by `streamId`, ordered by
`sequence`, and checked for gaps, duplicates, out-of-order input, and a clean `stream.completed`. Legacy V0, unknown schema
versions, and other JSONL shapes remain visible through the generic/raw path instead of being guessed as current Runtime semantics.

Raw mode is intentionally unrestricted for this single-user local Viewer. It may display prompts, reasoning, credentials,
provider responses, hidden answers, and complete Tool payloads if the archive contains them. Raw evidence is loaded only
after selecting an event, uses `Cache-Control: no-store`, and is never written to Viewer logs or persistent caches. Do not
expose the listener outside loopback or share screenshots without reviewing their contents.

Multiple `--data-root` options are supported. `HAIFA_EVAL_VIEWER_DATA_ROOTS` can also provide roots separated by the operating-system path separator.

## Validate

```powershell
uv sync --frozen
uv run python -m unittest discover -s tests -v
node --test tests/jsonl_normalizer.test.js
uv build
```

Tests generate minimal redacted archives in temporary directories. Real evaluation databases, prompts, model output, and verifier payloads never enter this repository.

## Repository boundary

This repository does not import `haifa_agent_evals`, invoke the Evals CLI, assume a sibling checkout, or write into an archive. The only integration boundary is the versioned file data contract described in [technical-design.md](docs/technical-design.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
