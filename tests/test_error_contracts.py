import json
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import Mock, patch

from playwright.sync_api import Page, Playwright, sync_playwright

from automation.actions import (
    ClickAction,
    FillAction,
    FinishAction,
    NextAction,
)
from automation.capability import (
    Capability,
    LiteralText,
    MemberLookupInputs,
    TextTemplate,
)
from automation.discovery import (
    DiscoveryStepLimitReached,
    run_discovery,
)
from automation.evidence import (
    EvidenceEvent,
    FailureEvidence,
    ModelCallMetadata,
    RunLog,
)
from automation.jobs import (
    DiscoveryTask,
    discover_capability,
    load_saved_capability,
    replay_capability,
)
from automation.network import install_request_guard
from automation.planner import ModelProposal
from automation.policy import PolicyViolation, RequestScope
from automation.replay import run_replay
from automation.takeover_controls import ConsoleTakeover, TakeoverCommand
from automation.verification import VerificationError
from demo_app.server import DemoServer, DemoServerStartError


PanelFactory = Callable[..., ConsoleTakeover]


class ScriptedPanel(ConsoleTakeover):
    def __init__(
        self,
        *,
        page: Page,
        command: TakeoverCommand,
        repair_notice: bool = False,
        advance_workflow: bool = False,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.page = page
        self.command: TakeoverCommand = command
        self.repair_notice = repair_notice
        self.advance_workflow = advance_workflow
        self.sent = False

    def poll_command(self):
        if not self.sent:
            if self.repair_notice:
                self.page.get_by_role(
                    "button",
                    name="Resolve notice manually",
                    exact=True,
                ).click()

            if self.advance_workflow:
                self.page.get_by_role(
                    "button",
                    name="Search",
                    exact=True,
                ).click()

            accepted = self.submit(
                self.command,
                run_id=self.run_id,
                step=self.step,
            )
            assert accepted, "Scripted takeover command was rejected."
            self.sent = True

        return super().poll_command()


class ExpiredPanel(ConsoleTakeover):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.deadline = time.monotonic() - 1


class FakeOpenAI:
    def __init__(self, **kwargs: Any):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def panel_factory(
    page: Page,
    *,
    command: TakeoverCommand = "resume",
    repair_notice: bool = False,
    advance_workflow: bool = False,
    expired: bool = False,
) -> PanelFactory:
    def factory(**kwargs: Any) -> ConsoleTakeover:
        kwargs["url"] = "Scripted operator test"

        if expired:
            return ExpiredPanel(**kwargs)

        return ScriptedPanel(
            page=page,
            command=command,
            repair_notice=repair_notice,
            advance_workflow=advance_workflow,
            **kwargs,
        )

    return factory


def read_events(path: Path) -> list[EvidenceEvent]:
    return [
        EvidenceEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def require_order(
    events: list[EvidenceEvent],
    expected: list[str],
) -> None:
    names = [event.event for event in events]
    position = 0

    for name in expected:
        try:
            position = names.index(name, position) + 1
        except ValueError:
            raise AssertionError(
                f"Missing or out-of-order event: {name}"
            ) from None


def run_replay_case(
    playwright: Playwright,
    directory: Path,
    capability: Capability,
    *,
    member_id: str = "DEMO-202",
    notice_mode: str = "off",
    allow_human_takeover: bool = False,
    panel: Callable[[Page], PanelFactory] | None = None,
) -> tuple[Any, list[EvidenceEvent], FailureEvidence | None]:
    scope = RequestScope(member_id=member_id)

    with DemoServer(
        dataset_id="members",
        notice_mode=notice_mode,
    ) as demo:
        browser = playwright.chromium.launch(headless=True)

        try:
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.set_default_timeout(2000)

            with RunLog(
                directory=directory,
                mode="replay",
                target_url=demo.url,
                source_run_id=capability.source_run_id,
                dataset_id="members",
            ) as log:
                result = run_replay(
                    page=page,
                    capability=capability,
                    inputs=MemberLookupInputs(member_id=member_id),
                    scope=scope,
                    blocked_requests=blocked_requests,
                    base_url=demo.url,
                    log=log,
                    allow_human_takeover=allow_human_takeover,
                    panel_factory=panel(page) if panel else None,
                )
                log_path = log.path

        finally:
            browser.close()

    events = read_events(log_path)
    failure_path = log_path.with_suffix(".failure.json")
    failure = (
        FailureEvidence.model_validate_json(
            failure_path.read_text(encoding="utf-8")
        )
        if failure_path.exists()
        else None
    )
    return result, events, failure


def require_terminal(
    events: list[EvidenceEvent],
    expected: str,
) -> None:
    terminal = [
        event.event
        for event in events
        if event.event in {"run_completed", "run_failed"}
    ]
    assert terminal == [expected]
    assert events[-1].event == expected


def check_replay_contracts(
    playwright: Playwright,
    directory: Path,
) -> None:
    capability = load_saved_capability("get_savings_balance")

    clean, clean_events, clean_failure = run_replay_case(
        playwright,
        directory,
        capability,
    )
    assert clean.status == "success"
    assert clean.recovery_events == []
    assert clean_failure is None
    require_terminal(clean_events, "run_completed")

    automatic, automatic_events, automatic_failure = run_replay_case(
        playwright,
        directory,
        capability,
        notice_mode="dismissible",
    )
    assert automatic.status == "success"
    assert [
        event.outcome for event in automatic.recovery_events
    ] == ["recovered_automatically"]
    assert automatic_failure is None
    require_order(
        automatic_events,
        ["recovery_started", "recovery_completed", "run_completed"],
    )
    require_terminal(automatic_events, "run_completed")

    missing, missing_events, missing_failure = run_replay_case(
        playwright,
        directory,
        capability,
        member_id="DEMO-999",
    )
    assert missing.status == "business_outcome"
    assert missing.code == "member_not_found"
    assert missing.recovery_events == []
    assert missing_failure is None
    require_order(missing_events, ["member_not_found", "run_completed"])
    require_terminal(missing_events, "run_completed")

    exhausted, exhausted_events, exhausted_failure = run_replay_case(
        playwright,
        directory,
        capability,
        notice_mode="persistent",
    )
    assert exhausted.status == "failure"
    assert exhausted.code == "recovery_exhausted"
    assert [
        event.outcome for event in exhausted.recovery_events
    ] == ["exhausted"]
    assert exhausted_failure is not None
    assert exhausted_failure.failure.status == "failure"
    assert exhausted_failure.failure.code == "recovery_exhausted"
    require_order(
        exhausted_events,
        ["recovery_exhausted", "failure_evidence_saved", "run_failed"],
    )
    require_terminal(exhausted_events, "run_failed")

    recovered, recovered_events, recovered_failure = run_replay_case(
        playwright,
        directory,
        capability,
        notice_mode="persistent",
        allow_human_takeover=True,
        panel=lambda page: panel_factory(
            page,
            repair_notice=True,
        ),
    )
    assert recovered.status == "success"
    assert [
        event.outcome for event in recovered.recovery_events
    ] == ["exhausted", "recovered_by_human"]
    assert recovered_failure is None
    require_order(
        recovered_events,
        [
            "recovery_exhausted",
            "handoff_started",
            "manual_action",
            "handoff_resumed",
            "handoff_ended",
            "run_completed",
        ],
    )
    require_terminal(recovered_events, "run_completed")

    cancelled, cancelled_events, cancelled_failure = run_replay_case(
        playwright,
        directory,
        capability,
        notice_mode="persistent",
        allow_human_takeover=True,
        panel=lambda page: panel_factory(page, command="cancel"),
    )
    assert cancelled.status == "failure"
    assert cancelled.code == "human_takeover_cancelled"
    assert [
        event.outcome for event in cancelled.recovery_events
    ] == ["exhausted", "cancelled"]
    assert cancelled_failure is not None
    require_order(
        cancelled_events,
        ["handoff_cancelled", "handoff_ended", "run_failed"],
    )
    require_terminal(cancelled_events, "run_failed")

    timed_out, timeout_events, timeout_failure = run_replay_case(
        playwright,
        directory,
        capability,
        notice_mode="persistent",
        allow_human_takeover=True,
        panel=lambda page: panel_factory(page, expired=True),
    )
    assert timed_out.status == "failure"
    assert timed_out.code == "human_takeover_timed_out"
    assert [
        event.outcome for event in timed_out.recovery_events
    ] == ["exhausted", "timed_out"]
    assert timeout_failure is not None
    require_order(
        timeout_events,
        ["handoff_timed_out", "handoff_ended", "run_failed"],
    )
    require_terminal(timeout_events, "run_failed")

    mismatch_capability = capability.model_copy(deep=True)
    mismatch_capability.steps[0].checkpoint.expected_path = TextTemplate(
        parts=[LiteralText(kind="literal", value="/unexpected")]
    )
    mismatch, mismatch_events, mismatch_failure = run_replay_case(
        playwright,
        directory,
        mismatch_capability,
    )
    assert mismatch.status == "failure"
    assert mismatch.code == "checkpoint_mismatch"
    assert mismatch.recovery_events == []
    assert mismatch_failure is not None
    assert mismatch_failure.failure.status == "failure"
    assert mismatch_failure.failure.code == "checkpoint_mismatch"
    require_order(
        mismatch_events,
        ["step_started", "failure_evidence_saved", "run_failed"],
    )
    require_terminal(mismatch_events, "run_failed")


def check_replay_job_contracts(directory: Path) -> None:
    inputs = MemberLookupInputs(member_id="DEMO-202")
    private_message = "PRIVATE_JOB_EXCEPTION_MESSAGE"

    with patch(
        "automation.jobs.load_saved_capability",
        side_effect=ValueError(private_message),
    ):
        load_failure = replay_capability(
            inputs,
            dataset_id="members",
            capability_id="missing-capability",
        )

    assert load_failure.kind == "job_failure"
    assert load_failure.code == "capability_load_failed"
    assert load_failure.stage == "capability_load"
    assert load_failure.run_id is None
    assert private_message not in load_failure.model_dump_json()

    with patch(
        "automation.jobs.RunLog.__enter__",
        side_effect=OSError(private_message),
    ):
        evidence_failure = replay_capability(
            inputs,
            dataset_id="members",
            capability_id="get_savings_balance",
        )

    assert evidence_failure.kind == "job_failure"
    assert evidence_failure.code == "evidence_failed"
    assert evidence_failure.stage == "evidence"
    assert evidence_failure.run_id is None
    assert private_message not in evidence_failure.model_dump_json()

    run_directory = directory / "evidence" / "runs"

    with (
        patch("automation.jobs.PROJECT_ROOT", directory),
        patch.object(
            DemoServer,
            "__enter__",
            side_effect=DemoServerStartError(private_message),
        ),
    ):
        environment_failure = replay_capability(
            inputs,
            dataset_id="members",
            capability_id="get_savings_balance",
        )

    assert environment_failure.kind == "job_failure"
    assert environment_failure.code == "environment_failed"
    assert environment_failure.stage == "environment"
    assert environment_failure.run_id is not None
    assert private_message not in environment_failure.model_dump_json()

    environment_events = read_events(
        run_directory / f"{environment_failure.run_id}.jsonl"
    )
    require_terminal(environment_events, "run_failed")

    with (
        patch("automation.jobs.PROJECT_ROOT", directory),
        patch(
            "automation.jobs.run_replay",
            side_effect=RuntimeError(private_message),
        ),
    ):
        unexpected_failure = replay_capability(
            inputs,
            dataset_id="members",
            capability_id="get_savings_balance",
        )

    assert unexpected_failure.kind == "job_failure"
    assert unexpected_failure.code == "unexpected_job_error"
    assert unexpected_failure.stage == "replay"
    assert unexpected_failure.run_id is not None
    assert private_message not in unexpected_failure.model_dump_json()

    unexpected_events = read_events(
        run_directory / f"{unexpected_failure.run_id}.jsonl"
    )
    require_terminal(unexpected_events, "run_failed")


def proposal(
    action: FillAction | ClickAction | FinishAction,
    index: int,
) -> ModelProposal:
    return ModelProposal(
        request=NextAction(action=action),
        metadata=ModelCallMetadata(
            model="deterministic-test-model",
            response_id=f"response-{index}",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        ),
    )


def check_discovery_escalation(
    playwright: Playwright,
    directory: Path,
) -> None:
    inputs = MemberLookupInputs(member_id="DEMO-101")
    scope = RequestScope(member_id=inputs.member_id)
    log_path: Path | None = None
    proposals = [
        proposal(
            FillAction(
                kind="fill",
                label="Member ID",
                value=inputs.member_id,
            ),
            index,
        )
        for index in range(1, 9)
    ]
    proposals.append(
        proposal(
            FinishAction(
                kind="finish",
                summary="The deterministic discovery is complete.",
            ),
            9,
        )
    )

    with DemoServer(dataset_id="members") as demo:
        browser = playwright.chromium.launch(headless=True)

        try:
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.goto(demo.url)
            original_context = page.context

            with RunLog(
                directory=directory,
                mode="discovery",
                target_url=demo.url,
                dataset_id="members",
            ) as log:
                with patch(
                    "automation.discovery.propose_action",
                    side_effect=proposals,
                ):
                    discovery = run_discovery(
                        client=cast(Any, object()),
                        page=page,
                        goal="Find the savings balance.",
                        inputs=inputs,
                        scope=scope,
                        blocked_requests=blocked_requests,
                        log=log,
                        max_steps=8,
                        allow_human_takeover=True,
                        panel_factory=panel_factory(page),
                    )
                log_path = log.path

            assert page.context is original_context
            assert context.pages == [page]

        finally:
            browser.close()

    assert log_path is not None
    events = read_events(log_path)
    assert len(discovery.steps) == 8
    assert all(step.action.kind == "fill" for step in discovery.steps)
    assert not any(event.event == "manual_action" for event in events)
    action_steps = [
        event.step
        for event in events
        if event.event == "action_proposed"
    ]
    assert action_steps == list(range(1, 10))
    require_order(
        events,
        [
            "discovery_blocked",
            "handoff_started",
            "handoff_resumed",
            "handoff_ended",
            "action_proposed",
            "run_completed",
        ],
    )


def check_discovery_resume_rejection(
    playwright: Playwright,
    directory: Path,
) -> None:
    inputs = MemberLookupInputs(member_id="DEMO-101")
    scope = RequestScope(member_id=inputs.member_id)
    first = proposal(
        FillAction(
            kind="fill",
            label="Member ID",
            value=inputs.member_id,
        ),
        1,
    )
    log_path: Path | None = None

    with DemoServer(dataset_id="members") as demo:
        browser = playwright.chromium.launch(headless=True)

        try:
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.goto(demo.url)

            try:
                with RunLog(
                    directory=directory,
                    mode="discovery",
                    target_url=demo.url,
                    dataset_id="members",
                ) as log:
                    log_path = log.path

                    with patch(
                        "automation.discovery.propose_action",
                        return_value=first,
                    ):
                        run_discovery(
                            client=cast(Any, object()),
                            page=page,
                            goal="Find the savings balance.",
                            inputs=inputs,
                            scope=scope,
                            blocked_requests=blocked_requests,
                            log=log,
                            max_steps=1,
                            allow_human_takeover=True,
                            panel_factory=panel_factory(
                                page,
                                advance_workflow=True,
                            ),
                        )
            except VerificationError:
                pass
            else:
                raise AssertionError(
                    "Discovery resumed after a workflow-changing action."
                )

        finally:
            browser.close()

    assert log_path is not None
    events = read_events(log_path)
    require_order(
        events,
        [
            "manual_action",
            "handoff_resume_rejected",
            "handoff_ended",
            "run_failed",
        ],
    )
    assert any(
        event.human_action is not None
        and event.human_action.target == "search_button"
        for event in events
    )


def check_discovery_scope_rejection(
    playwright: Playwright,
    directory: Path,
) -> None:
    inputs = MemberLookupInputs(member_id="DEMO-101")
    scope = RequestScope(member_id=inputs.member_id)
    log_path: Path | None = None
    planner = Mock(
        return_value=proposal(
            ClickAction(
                kind="click",
                role="link",
                name="Other member",
            ),
            1,
        )
    )

    with DemoServer(dataset_id="members") as demo:
        browser = playwright.chromium.launch(headless=True)

        try:
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.goto(demo.url)
            page.set_content(
                '<a href="/members/DEMO-202">Other member</a>'
            )

            try:
                with RunLog(
                    directory=directory,
                    mode="discovery",
                    target_url=demo.url,
                    dataset_id="members",
                ) as log:
                    log_path = log.path

                    with patch(
                        "automation.discovery.propose_action",
                        planner,
                    ):
                        run_discovery(
                            client=cast(Any, object()),
                            page=page,
                            goal="Find the savings balance.",
                            inputs=inputs,
                            scope=scope,
                            blocked_requests=blocked_requests,
                            log=log,
                        )
            except PolicyViolation:
                pass
            else:
                raise AssertionError(
                    "Discovery followed a wrong-member link."
                )

            assert planner.call_count == 1
            assert page.url == demo.url
            assert not blocked_requests

        finally:
            browser.close()

    assert log_path is not None
    events = read_events(log_path)
    require_order(events, ["model_requested", "action_proposed", "run_failed"])
    assert not any(event.event == "step_started" for event in events)


def check_discovery_failure_capture(directory: Path) -> None:
    sensitive_values = (
        "PRIVATE_TEST_CUSTOMER",
        "PRIVATE_TEST_MEMBER",
        "PRIVATE_TEST_TOKEN",
        "PRIVATE_TEST_BALANCE",
    )
    run_id = None

    def remember_run(value: str) -> None:
        nonlocal run_id
        run_id = value

    def fail_discovery(**kwargs: Any):
        page = kwargs["page"]
        page.set_content(
            """
            <h1>PRIVATE_TEST_CUSTOMER</h1>
            <label for="member-id">Member ID</label>
            <input id="member-id" value="PRIVATE_TEST_MEMBER">
            <a href="/PRIVATE_TEST_TOKEN">Savings</a>
            <p>PRIVATE_TEST_BALANCE</p>
            """
        )
        raise DiscoveryStepLimitReached(8)

    task = DiscoveryTask(
        goal="Find this member's savings balance.",
        target_url=DemoServer.url,
        inputs=MemberLookupInputs(member_id="DEMO-101"),
        allow_human_takeover=False,
    )

    with (
        patch("automation.jobs.PROJECT_ROOT", directory),
        patch("automation.jobs.OpenAI", FakeOpenAI),
        patch("automation.jobs.run_discovery", fail_discovery),
    ):
        try:
            discover_capability(
                task,
                dataset_id="members",
                on_run_started=remember_run,
            )
        except DiscoveryStepLimitReached:
            pass
        else:
            raise AssertionError("Discovery step exhaustion did not fail.")

    assert run_id is not None
    run_path = directory / "evidence" / "runs" / f"{run_id}.jsonl"
    failure_path = run_path.with_suffix(".failure.json")
    text = failure_path.read_text(encoding="utf-8")

    for value in sensitive_values:
        assert value not in text

    evidence = FailureEvidence.model_validate_json(text)
    assert evidence.schema_version == "1.1"
    assert evidence.failure.status == "discovery_failure"
    assert evidence.failure.code == "step_limit"
    assert evidence.dom is not None
    assert evidence.known_controls is not None
    assert evidence.known_controls.member_id_fields == 1

    events = read_events(run_path)
    require_order(events, ["failure_evidence_saved", "run_failed"])
    require_terminal(events, "run_failed")


def check_websocket_policy(playwright: Playwright) -> None:
    scope = RequestScope(member_id="DEMO-101")

    with DemoServer(dataset_id="members") as demo:
        browser = playwright.chromium.launch(headless=True)

        try:
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.goto(demo.url)

            page.evaluate(
                """
                () => {
                    window.__webSocketPolicyOutcome = "pending";
                    setTimeout(() => {
                        const socket = new WebSocket(
                            "ws://127.0.0.1:8000/blocked-test"
                        );
                        socket.addEventListener("open", () => {
                            window.__webSocketPolicyOutcome = "open";
                        });
                        socket.addEventListener("error", () => {
                            window.__webSocketPolicyOutcome = "error";
                        });
                        socket.addEventListener("close", () => {
                            window.__webSocketPolicyOutcome = "closed";
                        });
                    }, 0);
                }
                """
            )

            page.wait_for_function(
                "window.__webSocketPolicyOutcome !== 'pending'",
                timeout=2000,
            )
            outcome = page.evaluate(
                "window.__webSocketPolicyOutcome"
            )

            assert outcome != "open"
            assert blocked_requests[-1] == (
                "WebSocket connections are blocked by policy."
            )

        finally:
            browser.close()


def main() -> None:
    with TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)

        with sync_playwright() as playwright:
            check_replay_contracts(playwright, directory)
            check_discovery_escalation(playwright, directory)
            check_discovery_resume_rejection(playwright, directory)
            check_discovery_scope_rejection(playwright, directory)
            check_websocket_policy(playwright)

        check_replay_job_contracts(directory)
        check_discovery_failure_capture(directory)

    print("Recovery, escalation, and evidence contract checks passed.")


if __name__ == "__main__":
    main()
