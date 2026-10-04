import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from openai import OpenAI
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from pydantic import Field, field_validator

from automation.actions import StrictModel
from automation.capability import (
    Capability,
    MemberLookupInputs,
    build_input_schema,
    build_output_schema,
    parse_capability_artifact,
)
from automation.discovery import (
    DiscoveryStepLimitReached,
    DiscoveryTargetTimeout,
    run_discovery,
)
from automation.evidence import (
    DiscoveryFailure,
    RunLog,
    save_failure_evidence,
)
from automation.handoff import (
    HumanTakeoverCancelled,
    HumanTakeoverTimedOut,
)
from automation.handoff import TakeoverPanelFactory
from automation.network import install_request_guard
from automation.planner import MODEL
from automation.policy import PolicyViolation, RequestScope, check_url
from automation.recording import RecordingError
from automation.replay import describe_observed_state, run_replay
from automation.results import ReplayResult
from automation.terminal import redacted_member
from automation.verification import (
    BalanceResult,
    VerificationError,
    verify_balance,
)
from demo_app.server import DemoServer, DemoTargetUrl


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_DIRECTORY = (
    PROJECT_ROOT / "evidence" / "capabilities"
)


class DiscoveryTask(StrictModel):
    goal: str = Field(min_length=1, max_length=1000)
    target_url: DemoTargetUrl
    inputs: MemberLookupInputs
    allow_human_takeover: bool = False

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Goal must not be blank.")

        return value


class DiscoveryJobResult(StrictModel):
    status: Literal["success"] = "success"
    run_id: str
    dataset_id: str
    capability_id: str
    model_summary: str
    outputs: BalanceResult


class ReplayJobCompleted(StrictModel):
    kind: Literal["replay_result"] = "replay_result"
    run_id: str
    dataset_id: str
    capability_id: str
    replay_result: ReplayResult


ReplayJobFailureCode = Literal[
    "capability_load_failed",
    "evidence_failed",
    "environment_failed",
    "unexpected_job_error",
]

ReplayJobStage = Literal[
    "capability_load",
    "evidence",
    "run_start_callback",
    "environment",
    "replay",
]


class ReplayJobFailure(StrictModel):
    kind: Literal["job_failure"] = "job_failure"
    run_id: str | None
    dataset_id: str
    capability_id: str
    code: ReplayJobFailureCode
    stage: ReplayJobStage
    expected: str
    observed: str
    error_type: str


ReplayJobResult = Annotated[
    ReplayJobCompleted | ReplayJobFailure,
    Field(discriminator="kind"),
]


def capability_paths() -> dict[str, Path]:
    directory = CAPABILITY_DIRECTORY.resolve()
    paths: dict[str, Path] = {}

    for path in directory.glob("*.json"):
        resolved = path.resolve()

        if resolved.is_file() and resolved.parent == directory:
            paths[path.stem] = resolved

    return paths


def list_capabilities() -> list[dict[str, str]]:
    paths = capability_paths()

    ordered_ids = sorted(
        paths,
        key=lambda name: (
            name != "get_savings_balance",
            -paths[name].stat().st_mtime,
        ),
    )

    return [
        {
            "id": capability_id,
            "label": (
                "Latest saved capability"
                if capability_id == "get_savings_balance"
                else capability_id
            ),
        }
        for capability_id in ordered_ids
    ]


def load_saved_capability(capability_id: str) -> Capability:
    path = capability_paths().get(capability_id)

    if path is None:
        raise ValueError("Unknown saved capability.")

    artifact_data: Any = json.loads(
        path.read_text(encoding="utf-8")
    )

    return parse_capability_artifact(artifact_data)


def discover_capability(
    task: DiscoveryTask,
    *,
    dataset_id: str,
    on_run_started: Callable[[str], None] | None = None,
    panel_factory: TakeoverPanelFactory | None = None,
) -> DiscoveryJobResult:
    key_file = (
        Path.home()
        / ".config"
        / "computer-use-automation"
        / ".env"
    )
    load_dotenv(key_file, override=True)

    inputs = task.inputs
    account_type = "savings"
    scope = RequestScope(
        member_id=inputs.member_id,
        account_type="savings",
    )

    goal = (
        f"Task: {task.goal}\n"
        f"Target member ID: {inputs.member_id}\n"
        f"Account type: {account_type}."
    )

    with RunLog(
        directory=PROJECT_ROOT / "evidence" / "runs",
        mode="discovery",
        target_url=task.target_url,
        dataset_id=dataset_id,
    ) as log:
        if on_run_started is not None:
            on_run_started(log.run_id)

        print(f"Evidence log: {log.path}")
        print(f"Target: {task.target_url}")
        print(f"Dataset: {dataset_id}")
        print(f"Model: {MODEL}")

        with DemoServer(
            dataset_id=dataset_id,
            notice_mode="off",
        ) as demo:
            with OpenAI(
                timeout=30.0,
                max_retries=0,
            ) as client:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=False
                    )

                    try:
                        context = browser.new_context(
                            service_workers="block"
                        )
                        blocked_requests = install_request_guard(
                            context,
                            scope,
                        )

                        page = context.new_page()
                        page.set_default_timeout(5000)

                        try:
                            check_url(task.target_url, scope)
                            page.goto(task.target_url)

                            discovery = run_discovery(
                                client=client,
                                page=page,
                                goal=goal,
                                inputs=inputs,
                                scope=scope,
                                blocked_requests=blocked_requests,
                                log=log,
                                max_steps=8,
                                allow_human_takeover=(
                                    task.allow_human_takeover
                                ),
                                panel_factory=panel_factory,
                            )

                            log.emit(
                                "verification_started",
                                purpose="verify_account_result",
                            )

                            result = verify_balance(
                                page=page,
                                expected_member_id=inputs.member_id,
                                expected_account_type=account_type,
                                scope=scope,
                            )

                            if blocked_requests:
                                raise PolicyViolation(
                                    blocked_requests[-1]
                                )

                            log.emit(
                                "verification_passed",
                                purpose="verify_account_result",
                            )

                            capability = Capability(
                                source_run_id=discovery.run_id,
                                input_schema=build_input_schema(),
                                output_schema=build_output_schema(),
                                steps=discovery.steps,
                            )

                            capability_directory = (
                                PROJECT_ROOT
                                / "evidence"
                                / "capabilities"
                            )
                            capability_directory.mkdir(
                                parents=True,
                                exist_ok=True,
                            )

                            capability_id = (
                                f"get_savings_balance_{log.run_id}"
                            )

                            serialized = (
                                capability.model_dump_json(indent=2)
                                + "\n"
                            )

                            # Preserve the artifact belonging to this run.
                            artifact_path = (
                                capability_directory
                                / f"{capability_id}.json"
                            )

                            with artifact_path.open(
                                "x",
                                encoding="utf-8",
                            ) as file:
                                file.write(serialized)

                            # Update the replay default only after the
                            # run-specific artifact is safely persisted.
                            latest_path = (
                                capability_directory
                                / "get_savings_balance.json"
                            )
                            latest_path.write_text(
                                serialized,
                                encoding="utf-8",
                            )

                            log.emit("capability_saved")

                            print(
                                f"\nSaved capability: {artifact_path}"
                            )

                            return DiscoveryJobResult(
                                run_id=log.run_id,
                                dataset_id=dataset_id,
                                capability_id=capability_id,
                                model_summary=discovery.finish.summary,
                                outputs=result,
                            )

                        except Exception as error:
                            reported_error = (
                                PolicyViolation(blocked_requests[-1])
                                if blocked_requests
                                else error
                            )
                            failure = _discovery_failure(
                                error=reported_error,
                                page=page,
                                inputs=inputs,
                                fallback_step=log.current_step,
                            )
                            log.mark_failed(
                                error_type=failure.error_type,
                                step=failure.step,
                                action=log.current_action,
                            )

                            try:
                                evidence_path = save_failure_evidence(
                                    page=page,
                                    log=log,
                                    failure=failure,
                                )
                                log.emit(
                                    "failure_evidence_saved",
                                    step=failure.step,
                                )
                                print(
                                    "Failure evidence: "
                                    f"{evidence_path}"
                                )
                            except OSError:
                                log.emit(
                                    "failure_evidence_unavailable",
                                    step=failure.step,
                                )
                                print(
                                    "Failure evidence could not be "
                                    "written."
                                )

                            if reported_error is not error:
                                raise reported_error from error

                            raise
                    finally:
                        browser.close()


def _discovery_failure(
    *,
    error: Exception,
    page: Page,
    inputs: MemberLookupInputs,
    fallback_step: int | None = None,
) -> DiscoveryFailure:
    step = getattr(error, "step", fallback_step)

    if isinstance(error, DiscoveryStepLimitReached):
        code = "step_limit"
        expected = "Finish within the bounded discovery budget."
    elif isinstance(error, DiscoveryTargetTimeout):
        code = "target_timeout"
        expected = "Reach an actionable browser target."
    elif isinstance(error, PolicyViolation):
        code = "policy_violation"
        expected = "Remain inside the configured safety policy."
    elif isinstance(error, HumanTakeoverCancelled):
        code = "human_takeover_cancelled"
        expected = "Resume from a validated discovery checkpoint."
    elif isinstance(error, HumanTakeoverTimedOut):
        code = "human_takeover_timed_out"
        expected = "Resume before the takeover timeout."
    elif isinstance(error, RecordingError):
        code = "recording_error"
        expected = "Record a supported, parameterized action."
    elif isinstance(error, VerificationError):
        code = "verification_failed"
        expected = "Verify the discovery result or resume checkpoint."
    elif isinstance(error, PlaywrightTimeoutError):
        code = "target_timeout"
        expected = "Reach the allowlisted discovery target."
    elif isinstance(error, PlaywrightError):
        code = "browser_error"
        expected = "Keep the original browser session available."
    else:
        code = "model_error"
        expected = "Receive a valid model action."

    return DiscoveryFailure(
        code=code,
        step=step,
        expected=expected,
        observed=describe_observed_state(page, inputs, None),
        error_type=type(error).__name__,
    )


def _replay_job_failure(
    *,
    error: Exception,
    stage: ReplayJobStage,
    run_id: str | None,
    dataset_id: str,
    capability_id: str,
) -> ReplayJobFailure:
    if stage == "capability_load":
        code: ReplayJobFailureCode = "capability_load_failed"
        expected = "Load and validate the requested saved capability."
        observed = "The saved capability could not be loaded."
    elif stage == "evidence":
        code = "evidence_failed"
        expected = "Create and finalize the replay evidence log."
        observed = "Replay evidence logging failed."
    elif stage == "environment":
        code = "environment_failed"
        expected = "Start and close the demo and browser environment."
        observed = "The replay environment failed."
    else:
        code = "unexpected_job_error"
        expected = "Return a structured deterministic replay result."
        observed = "Replay orchestration raised an unexpected error."

    return ReplayJobFailure(
        run_id=run_id,
        dataset_id=dataset_id,
        capability_id=capability_id,
        code=code,
        stage=stage,
        expected=expected,
        observed=observed,
        error_type=type(error).__name__,
    )


def replay_capability(
    inputs: MemberLookupInputs,
    *,
    dataset_id: str,
    capability_id: str,
    on_run_started: Callable[[str], None] | None = None,
    notice_mode: str = "off",
    allow_human_takeover: bool = False,
    panel_factory: TakeoverPanelFactory | None = None,
) -> ReplayJobResult:
    stage: ReplayJobStage = "capability_load"
    run_id = None

    try:
        capability = load_saved_capability(capability_id)
        scope = RequestScope(
            member_id=inputs.member_id,
            account_type="savings",
        )

        stage = "evidence"

        with RunLog(
            directory=PROJECT_ROOT / "evidence" / "runs",
            mode="replay",
            target_url=DemoServer.url,
            source_run_id=capability.source_run_id,
            dataset_id=dataset_id,
        ) as log:
            run_id = log.run_id

            if on_run_started is not None:
                stage = "run_start_callback"
                on_run_started(run_id)

            print(f"Evidence log: {log.path}")
            print(f"Replaying: {capability.name}")
            print(f"Target: {DemoServer.url}")
            print(f"Dataset: {dataset_id}")
            print(redacted_member())

            stage = "environment"

            with DemoServer(
                dataset_id=dataset_id,
                notice_mode=notice_mode,
            ) as demo:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=False,
                        slow_mo=300,
                    )
                    replay_raised = False

                    try:
                        context = browser.new_context(
                            service_workers="block"
                        )
                        blocked_requests = install_request_guard(
                            context,
                            scope,
                        )

                        page = context.new_page()
                        page.set_default_timeout(5000)
                        stage = "replay"

                        result = run_replay(
                            page=page,
                            capability=capability,
                            inputs=inputs,
                            scope=scope,
                            blocked_requests=blocked_requests,
                            base_url=demo.url,
                            log=log,
                            allow_human_takeover=allow_human_takeover,
                            panel_factory=panel_factory,
                        )

                    except BaseException:
                        replay_raised = True
                        raise

                    finally:
                        stage = "environment"
                        browser.close()

                        if replay_raised:
                            stage = "replay"

            completed = ReplayJobCompleted(
                run_id=run_id,
                dataset_id=dataset_id,
                capability_id=capability_id,
                replay_result=result,
            )
            stage = "evidence"
            return completed

    except Exception as error:
        return _replay_job_failure(
            error=error,
            stage=stage,
            run_id=run_id,
            dataset_id=dataset_id,
            capability_id=capability_id,
        )
