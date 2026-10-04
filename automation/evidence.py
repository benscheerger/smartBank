import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TextIO, get_args
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError, Page
from pydantic import Field, ValidationError

from automation.actions import StrictModel
from automation.results import ReplayFailure


EventName = Literal[
    "run_started",
    "model_requested",
    "action_proposed",
    "step_started",
    "step_completed",
    "checkpoint_passed",
    "verification_started",
    "verification_passed",
    "capability_saved",
    "run_completed",
    "run_failed",
    "member_not_found",
    "failure_evidence_saved",
    "failure_evidence_unavailable",
    "recovery_started",
    "recovery_completed",
    "recovery_exhausted",
    "handoff_started",
    "manual_action",
    "manual_recording_limit",
    "handoff_resume_rejected",
    "handoff_resumed",
    "handoff_cancelled",
    "handoff_timed_out",
    "handoff_ended",
]

ActionKind = Literal[
    "fill",
    "click",
    "click_button",
    "click_link",
    "finish",
]

ActionPurpose = Literal[
    "enter_member_id",
    "submit_member_search",
    "open_member_details",
    "open_savings_account",
    "report_observed_result",
    "execute_recorded_step",
    "dismiss_blocking_notice",
    "request_manual_repair",
    "validate_resume_checkpoint",
    "resume_after_validation",
    "stop_after_cancellation",
    "stop_after_takeover_timeout",
    "verify_account_result",
]


class HumanAction(StrictModel):
    kind: Literal["click", "input", "change"]
    target: Literal[
        "member_id_field",
        "search_button",
        "savings_link",
        "dismiss_notice_button",
        "manual_resolution_button",
        "other",
    ]


class EvidenceEvent(StrictModel):
    schema_version: Literal["1.0"]
    timestamp: str
    elapsed_ms: int
    run_id: str
    mode: Literal["discovery", "replay"]
    source_run_id: str | None
    event: EventName
    step: int | None
    action: ActionKind | None
    error_type: str | None
    human_action: HumanAction | None = None
    dataset_id: str | None = None
    purpose: ActionPurpose | None = None


class RunLog:
    def __init__(
        self,
        directory: Path,
        mode: Literal["discovery", "replay"],
        source_run_id: str | None = None,
        dataset_id: str | None = None,
    ):
        self.run_id = str(uuid4())
        self.path = directory / f"{self.run_id}.jsonl"
        self.mode: Literal["discovery", "replay"] = mode
        self.source_run_id = source_run_id
        self.dataset_id: str | None = dataset_id

        self._file: TextIO | None = None
        self._started_at = 0.0
        self._step: int | None = None
        self._action: ActionKind | None = None
        self._failure_error_type: str | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", encoding="utf-8")
        self._started_at = time.monotonic()

        try:
            self.emit("run_started")
        except BaseException:
            self._file.close()
            raise

        return self

    def emit(
        self,
        event: EventName,
        *,
        step: int | None = None,
        action: ActionKind | None = None,
        error_type: str | None = None,
        human_action: HumanAction | None = None,
        purpose: ActionPurpose | None = None,
    ) -> None:
        if self._file is None or self._file.closed:
            raise RuntimeError("The evidence log is not open.")

        entry = EvidenceEvent(
            schema_version="1.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=int(
                (time.monotonic() - self._started_at) * 1000
            ),
            run_id=self.run_id,
            mode=self.mode,
            source_run_id=self.source_run_id,
            event=event,
            step=step,
            action=action,
            error_type=error_type,
            human_action=human_action,
            dataset_id=self.dataset_id,
            purpose=purpose,
        )

        self._file.write(entry.model_dump_json() + "\n")
        self._file.flush()

        self._step = step
        self._action = action

    def __exit__(self, exc_type, exc_value, traceback) -> Literal[False]:
        file = self._file

        if file is None:
            raise RuntimeError("The evidence log is not open.")

        error_type = (
            exc_type.__name__
            if exc_type is not None
            else self._failure_error_type
        )

        try:
            if error_type is None:
                self.emit("run_completed")
            else:
                self.emit(
                    "run_failed",
                    step=self._step,
                    action=self._action,
                    error_type=error_type,
                )
        finally:
            file.close()

        return False

    def mark_failed(
        self,
        *,
        error_type: str,
        step: int | None,
        action: ActionKind | None,
    ) -> None:
        self._failure_error_type = error_type
        self._step = step
        self._action = action


MAX_DOM_NODES = 200

DomTag = Literal[
    "body", "main", "section", "div", "span",
    "form", "label", "input", "button", "a",
    "h1", "h2", "h3", "p",
    "dl", "dt", "dd", "ul", "ol", "li",
    "table", "tbody", "tr", "th", "td",
    "iframe", "other",
]


class DomNode(StrictModel):
    index: int
    parent_index: int | None
    tag: DomTag
    has_layout_box: bool
    disabled: bool


class DomSnapshot(StrictModel):
    nodes: list[DomNode] = Field(max_length=MAX_DOM_NODES)
    truncated: bool


class KnownControls(StrictModel):
    member_id_fields: int
    search_buttons: int
    savings_links: int


class FailureEvidence(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    source_run_id: str | None
    failure: ReplayFailure
    dom: DomSnapshot | None
    known_controls: KnownControls | None
    frame_count: int | None
    capture_error: Literal["snapshot_unavailable"] | None


DOM_CAPTURE = """
(body, options) => {
    const allowedTags = new Set(options.allowed_tags);
    const nodes = [];
    let truncated = false;

    function visit(element, parentIndex) {
        if (nodes.length >= options.limit) {
            truncated = true;
            return;
        }

        const index = nodes.length;
        const tag = element.tagName.toLowerCase();

        nodes.push({
            index: index,
            parent_index: parentIndex,
            tag: allowedTags.has(tag) ? tag : "other",
            has_layout_box: element.getClientRects().length > 0,
            disabled: element.matches(":disabled")
        });

        for (const child of element.children) {
            if (nodes.length >= options.limit) {
                truncated = true;
                break;
            }
            visit(child, index);
        }
    }

    visit(body, null);
    return {nodes: nodes, truncated: truncated};
}
"""


def save_failure_evidence(
    page: Page,
    log: RunLog,
    failure: ReplayFailure,
) -> Path:
    dom = None
    controls = None
    frame_count = None
    capture_error = None

    try:
        raw_structure = page.locator("body").evaluate(
            DOM_CAPTURE,
            arg={
                "limit": MAX_DOM_NODES,
                "allowed_tags": list(get_args(DomTag)),
            },
            timeout=2000,
        )

        dom = DomSnapshot.model_validate(raw_structure)

        controls = KnownControls(
            member_id_fields=page.get_by_label(
                "Member ID", exact=True
            ).count(),
            search_buttons=page.get_by_role(
                "button", name="Search", exact=True
            ).count(),
            savings_links=page.get_by_role(
                "link", name="Savings", exact=True
            ).count(),
        )

        frame_count = len(page.frames)

    except (PlaywrightError, ValidationError):
        dom = None
        controls = None
        frame_count = None
        capture_error = "snapshot_unavailable"

    evidence = FailureEvidence(
        run_id=log.run_id,
        source_run_id=log.source_run_id,
        failure=failure,
        dom=dom,
        known_controls=controls,
        frame_count=frame_count,
        capture_error=capture_error,
    )

    path = log.path.with_suffix(".failure.json")
    path.write_text(
        evidence.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    return path