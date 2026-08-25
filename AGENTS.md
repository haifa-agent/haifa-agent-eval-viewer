# Haifa Eval Viewer development guide

This repository is an independent, read-only viewer for private Coding Agent evaluation archives.

- Keep the product focused on Activity -> Task -> Trajectory investigation.
- Never import or execute `haifa_agent_evals`; consume only versioned files from configured archive roots.
- Open SQLite with `mode=ro&immutable=1`. Never migrate, repair, cache into, or rewrite source archives.
- Treat raw evidence as private. Semantic/Operations responses, logs, search, and default pages must not expose credentials,
  reasoning, full provider responses, hidden answers, or host paths. The loopback-only Raw event endpoint is the explicit
  exception: it may return the complete archived row/object on demand, without truncation or redaction; never log, export,
  persist, or preload that response.
- Python business logic belongs under `src/`; PowerShell and POSIX shell files are thin argument-forwarding wrappers.
- Use deterministic normalization rules. Unknown fields and events remain visible as unknown instead of being guessed.
- Tests create synthetic, redacted archives outside the source tree. Never copy real evaluation payloads into fixtures.
- Commit messages are English. Feature work uses a `feat-*` branch.
