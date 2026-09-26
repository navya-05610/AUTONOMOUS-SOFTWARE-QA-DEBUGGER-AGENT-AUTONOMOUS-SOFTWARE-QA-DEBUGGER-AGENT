"""Serve the local Autonomous Software QA & Debugger Agent dashboard."""
from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from qa_agent import (
    APP_DIR,
    ProjectPathError,
    get_history,
    inspect_project,
    run_checks,
)

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = 8765
AUTH_USERNAME = os.environ.get("QA_AGENT_USERNAME", "")
AUTH_PASSWORD = os.environ.get("QA_AGENT_PASSWORD", "")
STATIC_DIR = APP_DIR / "static"
MAX_REQUEST_BYTES = 64 * 1024


def _is_valid_basic_auth(
    authorization: str | None,
    username: str,
    password: str,
) -> bool:
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        credentials = base64.b64decode(authorization[6:], validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    expected = f"{username}:{password}".encode("utf-8")
    return hmac.compare_digest(credentials, expected)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "LocalQAAgent/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self) -> bool:
        if not AUTH_USERNAME and not AUTH_PASSWORD:
            return True
        if _is_valid_basic_auth(
            self.headers.get("Authorization"),
            AUTH_USERNAME,
            AUTH_PASSWORD,
        ):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Autonomous QA Agent"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body is empty or too large.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
            return payload
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"Invalid request: {error}") from error

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlparse(self.path).path
        if route == "/healthz":
            self._send_json({"status": "ok"})
            return
        if not self._require_auth():
            return
        if route == "/api/status":
            self._send_json(
                {
                    "name": "Autonomous Software QA & Debugger Agent",
                    "status": "ready",
                    "checks": ["pytest", "ruff", "python_syntax"],
                }
            )
        elif route == "/api/history":
            self._send_json({"history": get_history()})
        elif route == "/" or route == "/index.html":
            self._serve_static("index.html")
        elif route in {"/styles.css", "/status.css", "/app.js"}:
            self._serve_static(route.lstrip("/"))
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._require_auth():
            return
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
            project_path = payload.get("project_path", "")
            if route == "/api/scan":
                self._send_json(inspect_project(project_path))
            elif route == "/api/run":
                checks = payload.get("checks")
                if checks is not None and (
                    not isinstance(checks, list)
                    or not all(isinstance(item, str) for item in checks)
                ):
                    raise ValueError("Checks must be a list of check names.")
                self._send_json(
                    run_checks(project_path, checks, payload.get("timeout", 120))
                )
            else:
                self._send_json({"error": "Not found"}, 404)
        except (ProjectPathError, ValueError, TypeError) as error:
            self._send_json({"error": str(error)}, 400)
        except Exception as error:  # Keep unexpected errors visible.
            self._send_json({"error": f"Unexpected server error: {error}"}, 500)

    def _serve_static(self, name: str) -> None:
        path = (STATIC_DIR / name).resolve()
        if path.parent != STATIC_DIR.resolve() or not path.is_file():
            self._send_json({"error": "Not found"}, 404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if bool(AUTH_USERNAME) != bool(AUTH_PASSWORD):
        raise SystemExit("Set both QA_AGENT_USERNAME and QA_AGENT_PASSWORD, or neither.")
    if HOST not in {"127.0.0.1", "localhost", "::1"} and not AUTH_USERNAME:
        raise SystemExit(
            "Authentication is required when binding beyond localhost. Set "
            "QA_AGENT_USERNAME and QA_AGENT_PASSWORD."
        )
    default_port = sys.argv[1] if len(sys.argv) > 1 else PORT
    port = int(os.environ.get("PORT", default_port))
    server = ThreadingHTTPServer((HOST, port), DashboardHandler)
    print(f"QA Agent is running at http://{HOST}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping QA Agent...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
