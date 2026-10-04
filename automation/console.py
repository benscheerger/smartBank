import secrets
import traceback
from copy import deepcopy
from pathlib import Path
from queue import Full, Queue
from threading import Lock
from typing import Any, Literal
from uuid import UUID
from automation.capability import MemberLookupInputs

from automation.takeover_controls import (
    ConsoleTakeover,
    TakeoverCommand,
    TakeoverMode,
)

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    render_template,
    request,
)
from pydantic import Field, ValidationError
from automation.jobs import (
    PROJECT_ROOT,
    DiscoveryTask,
    discover_capability,
    list_capabilities,
    load_saved_capability,
    replay_capability,
)
from automation.actions import StrictModel
from automation.evidence import EvidenceEvent, InterventionReason
from demo_app.app import get_dataset_path, list_datasets
from demo_app.server import DemoServer


RUN_DIRECTORY = PROJECT_ROOT / "evidence" / "runs"


class ConsoleDiscoveryRequest(StrictModel):
    dataset_id: str = Field(min_length=1)
    task: DiscoveryTask

class ConsoleReplayRequest(StrictModel):
    dataset_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    inputs: MemberLookupInputs
    notice_mode: Literal[
        "off",
        "dismissible",
        "persistent",
    ] = "off"
    allow_human_takeover: bool = False

class ConsoleTakeoverRequest(StrictModel):
    run_id: str
    step: int = Field(ge=1)
    command: TakeoverCommand


def read_events(run_id: str) -> list[dict[str, Any]]:
    if str(UUID(run_id)) != run_id:
        raise ValueError("Invalid run ID.")

    path = RUN_DIRECTORY / f"{run_id}.jsonl"

    if path.stat().st_size > 1_000_000:
        raise ValueError("Run log exceeds the console size limit.")

    events = []

    with path.open(encoding="utf-8") as file:
        for line in file:
            # An active logger may still be writing its last line.
            if not line.endswith("\n"):
                break

            if not line.strip():
                continue

            event = EvidenceEvent.model_validate_json(line)
            events.append(event.model_dump(mode="json"))

    return events


class ConsoleController:
    def __init__(self):
        self.origin = ""

        self._lock = Lock()

        self._takeover: ConsoleTakeover | None = None

        self._queue: Queue[
            ConsoleDiscoveryRequest | ConsoleReplayRequest
        ] = Queue(maxsize=1)

        self._state: dict[str, Any] = {
            "status": "idle",
            "message": "Ready",
            "run_id": None,
            "dataset_id": None,
            "result": None,
            "error_type": None,
            "mode": "discovery",
            "capability_id": None,
            "member_id": None,
            "goal": None,
            "target_url": DemoServer.url,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = deepcopy(self._state)
            panel = self._takeover

        snapshot["takeover"] = None

        if (
            panel is not None
            and snapshot["status"] == "running"
            and snapshot["run_id"] == panel.run_id
        ):
            takeover = panel.snapshot()
            snapshot["takeover"] = takeover
            snapshot["message"] = takeover["message"]

            if takeover["status"] in {"human", "validating"}:
                snapshot["status"] = takeover["status"]

        return snapshot

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)
    
    def _make_takeover_panel(
        self,
        *,
        run_id: str,
        member_id: str,
        step: int,
        timeout_seconds: float,
        mode: TakeoverMode,
        task: str,
        reason: InterventionReason,
    ) -> ConsoleTakeover:
        panel = ConsoleTakeover(
            run_id=run_id,
            member_id=member_id,
            step=step,
            timeout_seconds=timeout_seconds,
            url=self.origin,
            mode=mode,
            task=task,
            reason=reason,
        )

        with self._lock:
            if (
                self._state["status"] != "running"
                or self._state["run_id"] != run_id
            ):
                raise RuntimeError(
                    "Takeover does not belong to the active run."
                )

            self._takeover = panel

        return panel


    def submit_takeover(
        self,
        command: TakeoverCommand,
        *,
        run_id: str,
        step: int,
    ) -> bool:
        with self._lock:
            if (
                self._state["status"] != "running"
                or self._state["run_id"] != run_id
            ):
                return False

            panel = self._takeover

        if panel is None:
            return False

        return panel.submit(
            command,
            run_id=run_id,
            step=step,
        )

    def submit(
        self,
        job: ConsoleDiscoveryRequest | ConsoleReplayRequest,
    ) -> bool:
        if isinstance(job, ConsoleDiscoveryRequest):
            mode = "discovery"
            inputs = job.task.inputs
            goal = job.task.goal
            target_url = job.task.target_url
            capability_id = None
            notice_mode = None
            allow_human_takeover = job.task.allow_human_takeover
        else:
            mode = "replay"
            inputs = job.inputs
            goal = None
            target_url = DemoServer.url
            capability_id = job.capability_id
            notice_mode = job.notice_mode
            allow_human_takeover = job.allow_human_takeover

        with self._lock:
            if self._state["status"] in {"queued", "running"}:
                return False

            try:
                self._queue.put_nowait(job)
            except Full:
                return False

            self._takeover = None

            self._state.update(
                status="queued",
                mode=mode,
                message="Run is queued.",
                run_id=None,
                dataset_id=job.dataset_id,
                capability_id=capability_id,
                member_id=inputs.member_id,
                goal=goal,
                target_url=target_url,
                notice_mode=notice_mode,
                allow_human_takeover=allow_human_takeover,
                result=None,
                error_type=None,
            )

        return True
    
    def _run_started(self, run_id: str) -> None:
        self._update(run_id=run_id)

    def run_forever(self) -> None:
        while True:
            job = self._queue.get()

            self._update(
                status="running",
                message="The run is active in the banking browser.",
            )

            try:
                if isinstance(job, ConsoleDiscoveryRequest):
                    discovery_result = discover_capability(
                        job.task,
                        dataset_id=job.dataset_id,
                        on_run_started=self._run_started,
                        panel_factory=self._make_takeover_panel,
                    )

                    self._update(
                        status="success",
                        message=(
                            "Discovery completed. The result was "
                            "verified and the capability was saved."
                        ),
                        result=discovery_result.model_dump(
                            mode="json"
                        ),
                    )

                else:
                    replay_job_result = replay_capability(
                        job.inputs,
                        dataset_id=job.dataset_id,
                        capability_id=job.capability_id,
                        on_run_started=self._run_started,
                        notice_mode=job.notice_mode,
                        allow_human_takeover=job.allow_human_takeover,
                        panel_factory=self._make_takeover_panel,
)

                    status = (
                        replay_job_result.replay_result.status
                    )

                    messages = {
                        "success": (
                            "Replay completed and the result "
                            "was independently verified."
                        ),
                        "business_outcome": (
                            "Replay completed with a business outcome."
                        ),
                        "failure": (
                            "Replay stopped. Review the structured "
                            "failure and run log."
                        ),
                    }

                    self._update(
                        status=status,
                        message=messages[status],
                        result=replay_job_result.model_dump(
                            mode="json"
                        ),
                    )

            except Exception as exc:
                self._update(
                    status="failure",
                    message=(
                        "The run failed. Review its evidence "
                        "and the terminal stack trace."
                    ),
                    error_type=type(exc).__name__,
                )

                print(f"\nRun failed: {type(exc).__name__}")
                traceback.print_tb(exc.__traceback__)

            finally:
                with self._lock:
                    self._takeover = None

                self._queue.task_done()


def create_console_app(
    controller: ConsoleController,
    token: str,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).resolve().parent / "templates"
        ),
        static_folder=None,
    )

    app.config["MAX_CONTENT_LENGTH"] = 8192

    @app.before_request
    def check_access() -> None:
        if request.host_url.rstrip("/") != controller.origin:
            abort(403)

        if request.path.startswith("/api/"):
            supplied = request.headers.get(
                "X-Console-Token",
                "",
            )

            if not secrets.compare_digest(supplied, token):
                abort(403)

        if request.method == "POST":
            if request.headers.get("Origin") != controller.origin:
                abort(403)

    @app.after_request
    def response_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/")
    def index() -> str:
        return render_template(
            "console.html",
            token=token,
            target_url=DemoServer.url,
        )

    @app.get("/api/datasets")
    def datasets() -> Response:
        return jsonify(list_datasets())

    @app.get("/api/state")
    def state() -> Response:
        snapshot = controller.snapshot()
        snapshot["events"] = []
        snapshot["log_error"] = None

        run_id = snapshot["run_id"]

        if isinstance(run_id, str):
            try:
                snapshot["events"] = read_events(run_id)
            except (OSError, ValueError) as exc:
                snapshot["log_error"] = type(exc).__name__

        return jsonify(snapshot)

    @app.post("/api/discovery")
    def start_discovery() -> tuple[Response, int]:
        try:
            job = ConsoleDiscoveryRequest.model_validate(
                request.get_json(silent=True)
            )
        except ValidationError as exc:
            messages = [
                (
                    ".".join(str(part) for part in error["loc"])
                    + ": "
                    + error["msg"]
                )
                for error in exc.errors(
                    include_input=False,
                    include_url=False,
                )
            ]

            return jsonify(
                {"error": "; ".join(messages)}
            ), 400

        try:
            get_dataset_path(job.dataset_id)
        except ValueError:
            return jsonify(
                {"error": "Select an available dataset."}
            ), 400

        if not controller.submit(job):
            return jsonify(
                {"error": "A discovery run is already active."}
            ), 409

        return jsonify(controller.snapshot()), 202

    @app.get("/api/runs")
    def runs() -> Response:
        paths = sorted(
            RUN_DIRECTORY.glob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        history = []

        for path in paths:
            try:
                if str(UUID(path.stem)) != path.stem:
                    continue
            except ValueError:
                continue

            history.append(
                {
                    "run_id": path.stem,
                    "modified": path.stat().st_mtime,
                }
            )

            if len(history) >= 100:
                break

        return jsonify(history)

    @app.get("/api/runs/<run_id>")
    def run_events(run_id: str) -> tuple[Response, int]:
        try:
            events = read_events(run_id)
        except FileNotFoundError:
            return jsonify(
                {"error": "Run log was not found."}
            ), 404
        except (OSError, ValueError):
            return jsonify(
                {"error": "The selected run log could not be read."}
            ), 400

        return jsonify(
            {
                "run_id": run_id,
                "events": events,
            }
        ), 200
    
    @app.get("/api/capabilities")
    def capabilities() -> Response:
        return jsonify(list_capabilities())
    
    @app.post("/api/takeover")
    def takeover_command() -> tuple[Response, int]:
        try:
            command = ConsoleTakeoverRequest.model_validate(
                request.get_json(silent=True)
            )
        except ValidationError:
            return jsonify(
                {"error": "Invalid takeover command."}
            ), 400

        accepted = controller.submit_takeover(
            command.command,
            run_id=command.run_id,
            step=command.step,
        )

        if not accepted:
            return jsonify(
                {
                    "error": (
                        "Takeover is no longer accepting commands "
                        "for this run and step."
                    )
                }
            ), 409

        return jsonify(controller.snapshot()), 202

    @app.post("/api/replay")
    def start_replay() -> tuple[Response, int]:
        try:
            job = ConsoleReplayRequest.model_validate(
                request.get_json(silent=True)
            )
        except ValidationError as exc:
            messages = [
                (
                    ".".join(str(part) for part in error["loc"])
                    + ": "
                    + error["msg"]
                )
                for error in exc.errors(
                    include_input=False,
                    include_url=False,
                )
            ]

            return jsonify(
                {"error": "; ".join(messages)}
            ), 400

        try:
            get_dataset_path(job.dataset_id)
            load_saved_capability(job.capability_id)
        except (OSError, ValueError):
            return jsonify(
                {
                    "error": (
                        "Select an available dataset and "
                        "a valid saved capability."
                    )
                }
            ), 400

        if not controller.submit(job):
            return jsonify(
                {"error": "A run is already active."}
            ), 409

        return jsonify(controller.snapshot()), 202

    return app
