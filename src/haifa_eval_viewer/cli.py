from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from .catalog import ArchiveCatalog
from .server import serve

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="haifa-eval-viewer", description="Read-only Coding Agent evaluation trajectory viewer"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    serve_parser = subparsers.add_parser("serve", help="start the loopback-only Web application")
    serve_parser.add_argument(
        "--data-root", action="append", default=[], help="configured archive or run_data root; repeatable"
    )
    serve_parser.add_argument("--host", default="127.0.0.1", choices=sorted(LOOPBACK_HOSTS))
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--refresh-seconds", type=float, default=5.0)
    return parser


def _data_roots(arguments: argparse.Namespace) -> list[Path]:
    configured = list(arguments.data_root)
    environment = os.environ.get("HAIFA_EVAL_VIEWER_DATA_ROOTS", "")
    if environment:
        configured.extend(part for part in environment.split(os.pathsep) if part)
    roots = [Path(value) for value in configured]
    if not roots:
        raise SystemExit("at least one --data-root or HAIFA_EVAL_VIEWER_DATA_ROOTS entry is required")
    return roots


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.action == "serve":
        catalog = ArchiveCatalog(_data_roots(arguments), refresh_interval_seconds=arguments.refresh_seconds)
        catalog.refresh(force=True)
        serve(catalog, arguments.host, arguments.port)
        return 0
    return 2
