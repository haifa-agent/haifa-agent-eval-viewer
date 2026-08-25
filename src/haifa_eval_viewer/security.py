from __future__ import annotations

import hashlib
from pathlib import Path


class UnsafeArchivePath(ValueError):
    pass


def ensure_within(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve(strict=True)
    candidate_resolved = candidate.resolve(strict=True)
    if not candidate_resolved.is_relative_to(root_resolved):
        raise UnsafeArchivePath("archive path escapes its configured root")
    return candidate_resolved


def resolve_archive_relative(root: Path, relative: str) -> Path:
    supplied = Path(relative)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise UnsafeArchivePath("archive-relative path is absolute or contains parent traversal")
    return ensure_within(root, root / supplied)


def stable_digest(*parts: str, length: int = 16) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def source_ref(root: Path, path: Path, root_alias: str, suffix: str | None = None) -> str:
    relative = ensure_within(root, path).relative_to(root.resolve(strict=True)).as_posix()
    rendered = f"{root_alias}/{relative}"
    return f"{rendered}{suffix}" if suffix else rendered
