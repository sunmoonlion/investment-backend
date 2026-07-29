from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
EVENTS = [
    {"id": "event-1", "type": "TimelineRunStarted", "payload": {}},
    {"id": "event-2", "type": "TimelineWaitInputDisplayed", "payload": {}},
    {"id": "event-3", "type": "TimelineRunCompleted", "payload": {}},
]


class RuntimeHarnessHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self._send_file(
                ROOT / "runtime_selection_browser_harness.html",
                "text/html; charset=utf-8",
            )
            return
        if parsed.path == "/runtime-selection-browser-client.js":
            self._send_file(
                ROOT / "runtime_selection_browser_client.js",
                "text/javascript; charset=utf-8",
            )
            return
        if parsed.path == "/stream":
            self._send_initial_stream()
            return
        if parsed.path == "/events":
            after_event_id = parse_qs(parsed.query).get("after_event_id", [None])[0]
            self._send_snapshot(after_event_id)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_initial_stream(self) -> None:
        body = (
            f"id: {EVENTS[0]['id']}\n"
            f"data: {json.dumps(EVENTS[0], separators=(',', ':'))}\n\n"
        ).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_snapshot(self, after_event_id: str | None) -> None:
        start = 0
        if after_event_id:
            ids = [event["id"] for event in EVENTS]
            if after_event_id in ids:
                start = ids.index(after_event_id) + 1
        body = json.dumps({"events": EVENTS[start:]}, separators=(",", ":")).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--node-modules",
        default="/home/zymun/tpl-app/tpl-admin-frontend/node_modules",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHarnessHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env["NODE_PATH"] = args.node_modules
        result = subprocess.run(
            [
                "node",
                str(ROOT / "runtime_selection_browser_spike.cjs"),
                f"http://127.0.0.1:{server.server_port}/",
            ],
            env=env,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "browser harness failed: "
                f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
            )
        print(result.stdout.strip())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
