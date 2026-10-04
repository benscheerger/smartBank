import argparse
import json
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from automation.evidence import EvidenceEvent
from automation.evidence import InterventionReason

from playwright.sync_api import Page

from automation.capability import MemberLookupInputs
from automation.takeover_controls import (
    ConsoleTakeover,
    TakeoverCommand,
    TakeoverMode,
)
from automation.handoff import HumanTakeover
from automation.jobs import (
    PROJECT_ROOT,
    DiscoveryTask,
    ReplayJobResult,
    discover_capability,
    load_saved_capability,
    replay_capability,
)
from automation.verification import BalanceResult
from demo_app.app import get_dataset_path, load_members
from demo_app.server import DemoServer


class ScriptedOperator(ConsoleTakeover):
    """Simulate an operator on the browser-owning replay thread."""

    def __init__(
        self,
        *,
        page: Page,
        run_id: str,
        member_id: str,
        step: int,
        timeout_seconds: float,
        mode: TakeoverMode,
        task: str,
        reason: InterventionReason,
    ):
        super().__init__(
            run_id=run_id,
            member_id=member_id,
            step=step,
            timeout_seconds=timeout_seconds,
            url="Scripted operator test",
            mode=mode,
            task=task,
            reason=reason,
        )

        self._page = page
        self._phase = 0

    def _queue_resume(self) -> None:
        accepted = self.submit(
            "resume",
            run_id=self.run_id,
            step=self.step,
        )

        if not accepted:
            raise AssertionError(
                "The scripted Resume command was not accepted."
            )

    def poll_command(self) -> TakeoverCommand | None:
        if self._phase == 0:
            # Attempt Resume while the notice still blocks the page.
            self._queue_resume()
            self._phase = 1

        elif self._phase == 1:
            # The real takeover loop must have rejected that Resume.
            if self.snapshot()["status"] != "human":
                raise AssertionError(
                    "The initial Resume did not return control "
                    "to the operator."
                )

            # Exercise the actual manual-resolution button.
            self._page.get_by_role(
                "button",
                name="Resolve notice manually",
                exact=True,
            ).click(timeout=5000)

            self._queue_resume()
            self._phase = 2

        else:
            raise AssertionError(
                "Takeover did not complete after manual repair."
            )

        return super().poll_command()


class ScriptedHumanTakeover(HumanTakeover):
    """Keep production handoff behavior; replace operator controls."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.panel_factory = self._make_scripted_panel

    def _make_scripted_panel(
        self,
        *,
        run_id: str,
        member_id: str,
        step: int,
        timeout_seconds: float,
        mode: TakeoverMode,
        task: str,
        reason: InterventionReason,
    ) -> ScriptedOperator:
        return ScriptedOperator(
            page=self.page,
            run_id=run_id,
            member_id=member_id,
            step=step,
            timeout_seconds=timeout_seconds,
            mode=mode,
            task=task,
            reason=reason,
        )


def read_completed_log(
    run_id: str,
    *,
    source_run_id: str | None = None,
) -> list[dict[str, Any]]:
    path = PROJECT_ROOT / "evidence" / "runs" / f"{run_id}.jsonl"

    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    if not events:
        raise AssertionError(f"Evidence log is empty: {run_id}")
    
    for event in events:
        EvidenceEvent.model_validate(event)

    # Older logs have no purpose field.
    legacy_event = dict(events[0])
    legacy_event.pop("purpose", None)

    if EvidenceEvent.model_validate(legacy_event).purpose is not None:
        raise AssertionError(
            "An older event should default to purpose=None."
        )

    if events[0]["event"] != "run_started":
        raise AssertionError("Missing initial run_started event.")

    if events[-1]["event"] != "run_completed":
        raise AssertionError(
            f"Run did not complete successfully: {run_id}"
        )

    terminal_events = [
        event
        for event in events
        if event["event"] in {"run_completed", "run_failed"}
    ]

    if len(terminal_events) != 1:
        raise AssertionError(
            "Expected exactly one terminal event."
        )

    for event in events:
        if event["run_id"] != run_id:
            raise AssertionError("Evidence run ID mismatch.")

        if (
            source_run_id is not None
            and event["source_run_id"] != source_run_id
        ):
            raise AssertionError(
                "Replay evidence has incorrect capability provenance."
            )

    return events


def require_event_order(
    events: list[dict[str, Any]],
    expected: list[str],
) -> None:
    names = [event["event"] for event in events]
    position = 0

    for name in expected:
        try:
            position = names.index(name, position) + 1
        except ValueError:
            raise AssertionError(
                f"Missing or out-of-order event: {name}"
            ) from None

def require_event_purpose(
    events: list[dict[str, Any]],
    event_name: str,
    expected_purpose: str,
) -> None:
    matching = [
        event for event in events
        if event["event"] == event_name
    ]

    if not matching:
        raise AssertionError(f"Missing event: {event_name}")

    for event in matching:
        if event.get("purpose") != expected_purpose:
            raise AssertionError(
                f"{event_name}: expected purpose "
                f"{expected_purpose!r}, got "
                f"{event.get('purpose')!r}."
            )


def require_action_purposes(
    events: list[dict[str, Any]],
    *,
    completed_event: str,
) -> None:
    expected = [
        "enter_member_id",
        "submit_member_search",
        "open_member_details",
        "open_savings_account",
    ]

    started = [
        event for event in events
        if event["event"] == "step_started"
    ]

    if not started:
        raise AssertionError("Missing action-start events.")

    for event in started:
        purpose = event.get("purpose")

        if purpose not in expected:
            raise AssertionError(
                f"Step {event['step']} has an unexpected "
                f"or missing purpose: {purpose!r}."
            )

        completed = [
            candidate for candidate in events
            if candidate["event"] == completed_event
            and candidate["step"] == event["step"]
        ]

        if len(completed) != 1:
            raise AssertionError(
                f"Step {event['step']} needs exactly one "
                f"{completed_event} event."
            )

        if completed[0].get("purpose") != purpose:
            raise AssertionError(
                f"Purpose changed during step {event['step']}."
            )

    # Require the main workflow in order, allowing repeated actions.
    purposes = [event["purpose"] for event in started]
    position = 0

    for purpose in expected:
        try:
            position = purposes.index(purpose, position) + 1
        except ValueError:
            raise AssertionError(
                f"Missing or out-of-order action purpose: {purpose}"
            ) from None

def check_balance(
    output: BalanceResult,
    *,
    members: dict[str, Any],
    member_id: str,
) -> None:
    expected = members[member_id]["accounts"]["savings"]

    if output.member_id != member_id:
        raise AssertionError("Incorrect output member.")

    if output.account_type != "savings":
        raise AssertionError("Incorrect output account.")

    if Decimal(str(output.available_balance)) != Decimal(
        str(expected["available_balance"])
    ):
        raise AssertionError("Balance does not match the dataset.")

    if output.currency != expected["currency"]:
        raise AssertionError("Currency does not match the dataset.")


def check_replay(
    job: ReplayJobResult,
    *,
    members: dict[str, Any],
    member_id: str,
    source_run_id: str,
) -> list[dict[str, Any]]:
    if job.kind == "job_failure":
        raise AssertionError(
            "Replay job failed before returning a replay result:\n"
            + job.model_dump_json(indent=2)
        )

    result = job.replay_result

    if result.status != "success":
        raise AssertionError(
            "Expected replay success:\n"
            + result.model_dump_json(indent=2)
        )

    check_balance(
        result.outputs,
        members=members,
        member_id=member_id,
    )

    events = read_completed_log(
        job.run_id,
        source_run_id=source_run_id,
    )

    require_event_order(
        events,
        [
            "verification_started",
            "verification_passed",
            "run_completed",
        ],
    )

    require_action_purposes(
        events,
        completed_event="checkpoint_passed",
    )

    for event_name in (
        "verification_started",
        "verification_passed",
    ):
        require_event_purpose(
            events,
            event_name,
            "verify_account_result",
        )

    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="members")
    parser.add_argument(
        "--capability-id",
        default="get_savings_balance",
    )
    parser.add_argument(
        "--live-discovery",
        action="store_true",
        help="Perform fresh discovery using the model API.",
    )
    args = parser.parse_args()

    members = load_members(get_dataset_path(args.dataset))
    capability_id = args.capability_id
    discovery = None

    if args.live_discovery:
        discovery = discover_capability(
            DiscoveryTask(
                goal=(
                    "Find this member's available savings balance "
                    "and report its currency."
                ),
                target_url=DemoServer.url,
                inputs=MemberLookupInputs(member_id="DEMO-101"),
                allow_human_takeover=True,
            ),
            dataset_id=args.dataset,
        )

        check_balance(
            discovery.outputs,
            members=members,
            member_id="DEMO-101",
        )

        discovery_events = read_completed_log(discovery.run_id)

        require_action_purposes(
            discovery_events,
            completed_event="step_completed",
        )

        for event_name in (
            "verification_started",
            "verification_passed",
        ):
            require_event_purpose(
                discovery_events,
                event_name,
                "verify_account_result",
            )

        finish_events = [
            event for event in discovery_events
            if event["event"] == "action_proposed"
            and event["action"] == "finish"
        ]

        require_event_purpose(
            finish_events,
            "action_proposed",
            "report_observed_result",
        )

        require_event_order(
            discovery_events,
            [
                "verification_passed",
                "capability_saved",
                "run_completed",
            ],
        )

        capability_id = discovery.capability_id
        print(f"\nPASS: discovery — {discovery.run_id}")

    capability = load_saved_capability(capability_id)

    if (
        discovery is not None
        and capability.source_run_id != discovery.run_id
    ):
        raise AssertionError(
            "Saved capability does not reference its discovery run."
        )

    inputs = MemberLookupInputs(member_id="DEMO-202")

    normal_replay = replay_capability(
        inputs,
        dataset_id=args.dataset,
        capability_id=capability_id,
        notice_mode="off",
        allow_human_takeover=False,
    )

    normal_events = check_replay(
        normal_replay,
        members=members,
        member_id=inputs.member_id,
        source_run_id=capability.source_run_id,
    )

    assert normal_replay.kind == "replay_result"

    if any(
        event["event"] == "handoff_started"
        for event in normal_events
    ):
        raise AssertionError(
            "Normal replay unexpectedly required takeover."
        )

    if normal_replay.replay_result.recovery_events:
        raise AssertionError("Clean replay reported a recovery event.")

    print(f"\nPASS: normal replay — {normal_replay.run_id}")

    # Replace only the operator-control implementation.
    with patch(
        "automation.replay.HumanTakeover",
        ScriptedHumanTakeover,
    ):
        takeover_replay = replay_capability(
            inputs,
            dataset_id=args.dataset,
            capability_id=capability_id,
            notice_mode="persistent",
            allow_human_takeover=True,
        )

    takeover_events = check_replay(
        takeover_replay,
        members=members,
        member_id=inputs.member_id,
        source_run_id=capability.source_run_id,
    )

    assert takeover_replay.kind == "replay_result"

    if [
        event.outcome
        for event in takeover_replay.replay_result.recovery_events
    ] != ["exhausted", "recovered_by_human"]:
        raise AssertionError(
            "Takeover replay did not expose its recovery sequence."
        )

    for event_name, purpose in (
        ("recovery_started", "dismiss_blocking_notice"),
        ("recovery_exhausted", "dismiss_blocking_notice"),
        ("handoff_started", "request_manual_repair"),
        ("handoff_resume_rejected", "validate_resume_checkpoint"),
        ("handoff_resumed", "resume_after_validation"),
    ):
        require_event_purpose(
            takeover_events,
            event_name,
            purpose,
        )
    

    require_event_order(
        takeover_events,
        [
            "handoff_started",
            "handoff_resume_rejected",
            "manual_action",
            "handoff_resumed",
            "handoff_ended",
            "verification_passed",
            "run_completed",
        ],
    )

    for name in [
        "handoff_started",
        "handoff_resume_rejected",
        "handoff_resumed",
    ]:
        count = sum(
            event["event"] == name
            for event in takeover_events
        )

        if count != 1:
            raise AssertionError(
                f"Expected one {name} event; found {count}."
            )

    recorded_resolution = any(
        event["event"] == "manual_action"
        and (event.get("human_action") or {}).get("kind") == "click"
        and (event.get("human_action") or {}).get("target")
        == "manual_resolution_button"
        for event in takeover_events
    )

    if not recorded_resolution:
        raise AssertionError(
            "The manual-resolution click was not recorded."
        )

    print(
        "\nPASS: rejected Resume → repair → successful Resume "
        f"— {takeover_replay.run_id}"
    )
    print(f"Capability: {capability_id}")
    print("\nAll workflow checks passed.")


if __name__ == "__main__":
    main()
