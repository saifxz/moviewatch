from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
from .runner import get_last_summary, request_scan, run_once


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    expected = os.getenv("MOVIE_WATCH_API_KEY", "").strip()
    if not expected:
        return True
    return handler.headers.get("X-API-Key", "") == expected


def _api_dry_run() -> bool:
    return os.getenv("MOVIE_WATCH_API_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}


# One instance serves the API, so a non-blocking lock prevents two overlapping
# scans from fighting over the same temp dir / source endpoints.
_serve_scan_lock = threading.Lock()


class MovieWatchHandler(BaseHTTPRequestHandler):
    server_version = "movie-watch/0.1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/scan":
            self._json(404, {"error": "not found"})
            return
        if not _authorized(self):
            self._json(401, {"error": "unauthorized"})
            return
        if not _serve_scan_lock.acquire(blocking=False):
            self._json(409, {"error": "scan already in progress"})
            return
        try:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("body must be a JSON object")
            except Exception as exc:
                self._json(400, {"error": f"bad request: {exc}"})
                return

            state_b64 = str(payload.get("state_b64") or "")
            include_state = bool(payload.get("include_state", False))
            config_ref = str(
                payload.get("config") or os.getenv("MOVIE_WATCH_CONFIG", "config.actions.yaml")
            )
            config_file = Path(config_ref)
            if not config_file.is_absolute():
                config_file = (Path(os.getenv("MOVIE_WATCH_DIR") or Path.cwd()) / config_file).resolve()
            if not config_file.is_file():
                self._json(400, {"error": f"config not found: {config_ref}"})
                return

            tmp = Path(tempfile.mkdtemp(prefix="movie-watch-"))
            old_dir = os.environ.get("MOVIE_WATCH_DIR")
            old_config = os.environ.get("MOVIE_WATCH_CONFIG")
            try:
                (tmp / "data").mkdir(parents=True, exist_ok=True)
                (tmp / "logs").mkdir(parents=True, exist_ok=True)
                if state_b64:
                    try:
                        (tmp / "data" / "watch.sqlite3").write_bytes(base64.b64decode(state_b64))
                    except Exception as exc:
                        self._json(400, {"error": f"bad state_b64: {exc}"})
                        return

                os.environ["MOVIE_WATCH_DIR"] = str(tmp)
                os.environ["MOVIE_WATCH_CONFIG"] = str(config_file)
                config = Config.load(config_file)
                # Render sends real Telegram alerts unless explicitly told not to.
                summary = run_once(config, dry_run=_api_dry_run())
                result = summary.as_dict()
                if include_state:
                    db = tmp / "data" / "watch.sqlite3"
                    if db.is_file():
                        result["state_b64"] = base64.b64encode(db.read_bytes()).decode("ascii")
                self._json(200, result)
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            finally:
                if old_dir is None:
                    os.environ.pop("MOVIE_WATCH_DIR", None)
                else:
                    os.environ["MOVIE_WATCH_DIR"] = old_dir
                if old_config is None:
                    os.environ.pop("MOVIE_WATCH_CONFIG", None)
                else:
                    os.environ["MOVIE_WATCH_CONFIG"] = old_config
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            _serve_scan_lock.release()


class ControlHandler(BaseHTTPRequestHandler):
    """Small control plane for the continuously running watcher."""

    server_version = "movie-watch-control/0.1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not _authorized(self):
            self._json(401, {"error": "unauthorized"})
        elif path == "/health":
            self._json(200, {"ok": True})
        elif path == "/status":
            self._json(200, {"last_run": get_last_summary()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not _authorized(self):
            self._json(401, {"error": "unauthorized"})
        elif path == "/scan":
            request_scan()
            self._json(202, {"status": "scan requested"})
        else:
            self._json(404, {"error": "not found"})


_control_started = False
_control_lock = threading.Lock()


def start_control_server() -> None:
    """Start the trigger API in a daemon thread for `movie-watch run`."""
    global _control_started
    port = int(os.getenv("MOVIE_WATCH_CONTROL_PORT", "8080"))
    with _control_lock:
        if _control_started:
            return
        server = ThreadingHTTPServer(("0.0.0.0", port), ControlHandler)
        thread = threading.Thread(
            target=server.serve_forever, name="movie-watch-control", daemon=True
        )
        thread.start()
        _control_started = True
        print(f"movie-watch control API listening on 0.0.0.0:{port}", flush=True)


def serve() -> None:
    """Run a tiny HTTP API so a scheduler can trigger one stateless scan."""
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MovieWatchHandler)
    print(f"movie-watch API listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
