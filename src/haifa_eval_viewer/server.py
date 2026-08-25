from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .catalog import ActivityNotFound, ArchiveCatalog, TaskNotFound

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _single_query(query: dict[str, list[str]]) -> dict[str, str]:
    return {key: values[-1] for key, values in query.items() if values}


class ViewerRequestHandler(BaseHTTPRequestHandler):
    server_version = "HaifaEvalViewer"
    sys_version = ""

    @property
    def catalog(self) -> ArchiveCatalog:
        return self.server.catalog  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        try:
            if parsed.path.startswith("/api/v1/"):
                self._handle_api(parsed.path, parse_qs(parsed.query, keep_blank_values=False))
            else:
                self._serve_static(parsed.path)
        except (ActivityNotFound, TaskNotFound):
            self._json(
                {"error": "NOT_FOUND", "message": "The requested evaluation evidence was not found"},
                HTTPStatus.NOT_FOUND,
            )
        except ValueError:
            self._json(
                {"error": "INVALID_REQUEST", "message": "The request could not be parsed"}, HTTPStatus.BAD_REQUEST
            )
        except Exception as exc:  # fail one request without exposing paths or payloads
            self._json(
                {
                    "error": "VIEWER_FAILURE",
                    "message": f"The Viewer could not complete the request ({type(exc).__name__})",
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        segments = [unquote(segment) for segment in path.strip("/").split("/")]
        if segments == ["api", "v1", "health"]:
            self._json({"status": "UP"})
            return
        if segments == ["api", "v1", "activities"]:
            force = _single_query(query).get("refresh", "false").lower() == "true"
            self._json(self.catalog.list_activities(force_refresh=force))
            return
        if len(segments) == 4 and segments[:3] == ["api", "v1", "activities"]:
            self._json(self.catalog.get_activity(segments[3], load_tasks=True))
            return
        if len(segments) == 5 and segments[:3] == ["api", "v1", "activities"] and segments[4] == "tasks":
            self._json(self.catalog.list_tasks(segments[3], _single_query(query)))
            return
        if len(segments) == 6 and segments[:3] == ["api", "v1", "activities"] and segments[4] == "tasks":
            self._json(self.catalog.get_task(segments[3], segments[5]))
            return
        if (
            len(segments) == 9
            and segments[:3] == ["api", "v1", "activities"]
            and segments[4] == "tasks"
            and segments[6] == "events"
            and segments[8] == "raw"
        ):
            self._json(self.catalog.get_raw_event(segments[3], segments[5], segments[7]))
            return
        self._json({"error": "NOT_FOUND", "message": "API route not found"}, HTTPStatus.NOT_FOUND)

    def _serve_static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if pure.suffix == "" and relative != "index.html":
            relative = "index.html"
        root = resources.files("haifa_eval_viewer").joinpath("web")
        target = root.joinpath(*PurePosixPath(relative).parts)
        if not target.is_file():
            target = root.joinpath("index.html")
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            f"{content_type}; charset=utf-8"
            if content_type.startswith("text/") or content_type.endswith("javascript")
            else content_type,
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, header_value in SECURITY_HEADERS.items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], catalog: ArchiveCatalog):
        super().__init__(address, ViewerRequestHandler)
        self.catalog = catalog


def serve(catalog: ArchiveCatalog, host: str, port: int) -> None:
    server = ViewerServer((host, port), catalog)
    print(f"Haifa Eval Viewer listening on http://{host}:{server.server_port}")
    print(f"Configured archive roots: {catalog.root_count}")  # safe count, never paths
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
