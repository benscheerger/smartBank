from __future__ import annotations

import math
import secrets
import time
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from types import TracebackType
from typing import Any, Literal, cast

from automation.evidence import InterventionReason

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template,
    request,
)
from werkzeug.serving import WSGIRequestHandler, make_server


TakeoverCommand = Literal["resume", "cancel"]
TakeoverMode = Literal["discovery", "replay"]

TakeoverStatus = Literal[
    "human",
    "validating",
    "resumed",
    "cancelled",
    "timed_out",
    "failed",
]

FINISHED_STATUSES = {
    "resumed",
    "cancelled",
    "timed_out",
    "failed",
}


class QuietRequestHandler(WSGIRequestHandler):
    def log(
        self,
        type: str,
        message: str,
        *args: Any,
    ) -> None:
        pass


class OperatorPanel:
    def __init__(
        self,
        *,
        run_id: str,
        member_id: str,
        step: int,
        timeout_seconds: float,
        mode: TakeoverMode,
        task: str,
        reason: InterventionReason,
    ):
        self.run_id = run_id
        self.member_id = member_id
        self.step = step
        self.mode = mode
        self.task = task
        self.reason = reason
        self.deadline = time.monotonic() + timeout_seconds

        self._token = secrets.token_hex(32)
        self._lock = Lock()
        self._commands: Queue[TakeoverCommand] = Queue(maxsize=1)
        self._finished_seen = Event()

        self._status: TakeoverStatus = "human"
        self._message = "Automation is paused for operator review."
        self.instructions = (
            "Resolve the supported service notice without advancing "
            "the banking workflow, then request Resume."
            if mode == "discovery"
            else (
                "Resolve the service notice in the banking browser, "
                "then request Resume."
            )
        )

        template_directory = (
            Path(__file__).resolve().parent / "templates"
        )

        app = Flask(
            __name__,
            template_folder=str(template_directory),
            static_folder=None,
        )
        app.config["MAX_CONTENT_LENGTH"] = 1024

        @app.before_request
        def check_host() -> None:
            if request.host != self._host:
                abort(403)

        @app.after_request
        def response_headers(response: Response) -> Response:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            return response

        def require_token() -> None:
            supplied = request.headers.get(
                "X-Operator-Token",
                "",
            )

            if not secrets.compare_digest(supplied, self._token):
                abort(403)

        @app.get("/")
        def index() -> str:
            return render_template(
                "operator_panel.html",
                token=self._token,
            )

        @app.get("/api/state")
        def state() -> Response:
            require_token()
            snapshot = self.snapshot()

            if snapshot["finished"]:
                self._finished_seen.set()

            return jsonify(snapshot)

        @app.post("/api/command")
        def command() -> tuple[Response, int]:
            require_token()

            if request.headers.get("Origin") != self.url:
                abort(403)

            body = request.get_json(silent=True)

            if (
                not isinstance(body, dict)
                or set(body) != {"command"}
                or body["command"] not in {"resume", "cancel"}
            ):
                abort(400)

            requested = cast(
                TakeoverCommand,
                body["command"],
            )

            if not self._submit(requested):
                return jsonify(
                    {"error": "A command cannot be accepted now."}
                ), 409

            return jsonify(self.snapshot()), 202

        # Port 0 selects an available local port.
        self._server = make_server(
            "127.0.0.1",
            0,
            app,
            threaded=True,
            request_handler=QuietRequestHandler,
        )

        self._host = f"127.0.0.1:{self._server.server_port}"
        self.url = f"http://{self._host}"

        self._thread = Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="operator-panel",
        )

    def __enter__(self) -> OperatorPanel:
        self._thread.start()
        return self

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            status = self._status
            message = self._message

        if status in {"human", "validating"}:
            owner = "human"
        elif status == "resumed":
            owner = "automation"
        else:
            owner = "none"

        return {
            "run_id": self.run_id,
            "member_id": self.member_id,
            "step": self.step,
            "mode": self.mode,
            "task": self.task,
            "reason": self.reason,
            "instructions": self.instructions,
            "status": status,
            "owner": owner,
            "message": message,
            "remaining_seconds": max(
                0,
                math.ceil(self.deadline - time.monotonic()),
            ),
            "can_command": status == "human",
            "finished": status in FINISHED_STATUSES,
        }

    def _submit(self, command: TakeoverCommand) -> bool:
        with self._lock:
            if self._status != "human":
                return False

            try:
                self._commands.put_nowait(command)
            except Full:
                return False

            self._status = "validating"
            self._message = (
                "Checking the browser checkpoint."
                if command == "resume"
                else "Cancelling takeover."
            )

        return True

    def poll_command(self) -> TakeoverCommand | None:
        try:
            return self._commands.get_nowait()
        except Empty:
            return None

    def set_status(
        self,
        status: TakeoverStatus,
        message: str,
    ) -> None:
        with self._lock:
            self._status = status
            self._message = message

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> Literal[False]:
        with self._lock:
            if self._status not in FINISHED_STATUSES:
                self._status = "failed"
                self._message = (
                    "Takeover ended before a validated resume. "
                    "Check the terminal result."
                )

        # Give the page a brief opportunity to display the final
        # takeover status before shutting down the local server.
        self._finished_seen.wait(timeout=1)

        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
            self._thread.join(timeout=1)

        return False


class ConsoleTakeover:
    def __init__(
        self,
        *,
        run_id: str,
        member_id: str,
        step: int,
        timeout_seconds: float,
        url: str,
        mode: TakeoverMode,
        task: str,
        reason: InterventionReason,
    ):
        if timeout_seconds <= 0:
            raise ValueError("Takeover timeout must be positive.")

        self.run_id = run_id
        self.member_id = member_id
        self.step = step
        self.url = url
        self.mode = mode
        self.task = task
        self.reason = reason
        self.deadline = time.monotonic() + timeout_seconds
        self.instructions = (
            "Resolve only a supported service notice and do not advance "
            "the banking workflow."
            if mode == "discovery"
            else "Repair the service notice in the banking browser."
        )

        self._lock = Lock()
        self._commands: Queue[TakeoverCommand] = Queue(maxsize=1)
        self._status: TakeoverStatus = "human"
        self._message = (
            "Automation is paused. Repair the banking browser, "
            "then Resume or Cancel."
        )

    def __enter__(self) -> "ConsoleTakeover":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        with self._lock:
            if self._status not in FINISHED_STATUSES:
                self._status = "failed"
                self._message = (
                    "Takeover ended. Review the replay result."
                )

        return False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(
                0,
                math.ceil(self.deadline - time.monotonic()),
            )

            return {
                "run_id": self.run_id,
                "member_id": self.member_id,
                "step": self.step,
                "mode": self.mode,
                "task": self.task,
                "reason": self.reason,
                "instructions": self.instructions,
                "status": self._status,
                "message": self._message,
                "remaining_seconds": remaining,
                "can_command": (
                    self._status == "human"
                    and remaining > 0
                ),
                "owner": (
                    "human"
                    if self._status in {"human", "validating"}
                    else "automation"
                    if self._status == "resumed"
                    else "none"
                ),
            }

    def submit(
        self,
        command: TakeoverCommand,
        *,
        run_id: str,
        step: int,
    ) -> bool:
        with self._lock:
            if (
                command not in {"resume", "cancel"}
                or run_id != self.run_id
                or step != self.step
                or self._status != "human"
                or time.monotonic() >= self.deadline
            ):
                return False

            try:
                self._commands.put_nowait(command)
            except Full:
                return False

            self._status = "validating"
            self._message = (
                "Checking the resume checkpoint."
                if command == "resume"
                else "Cancellation requested."
            )

        return True

    def poll_command(self) -> TakeoverCommand | None:
        try:
            return self._commands.get_nowait()
        except Empty:
            return None

    def set_status(
        self,
        status: TakeoverStatus,
        message: str,
    ) -> None:
        with self._lock:
            self._status = status
            self._message = message
