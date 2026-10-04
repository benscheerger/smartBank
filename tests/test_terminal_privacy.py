import ast
import io
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from automation.actions import (
    ClickAction,
    FillAction,
    FinishAction,
    NextAction,
)
from automation.capability import MemberLookupInputs
from automation.discovery import run_discovery
from automation.evidence import ModelCallMetadata, RunLog
from automation.jobs import replay_capability
from automation.network import install_request_guard
from automation.planner import ModelProposal
from automation.policy import RequestScope
from automation.results import (
    ReplayBusinessOutcome,
    ReplayFailure,
    ReplaySuccess,
)
from automation.terminal import (
    REDACTED,
    discovery_result_lines,
    exception_line,
    observation_lines,
    proposal_lines,
    replay_result_lines,
    run_cli,
)
from automation.verification import BalanceResult
from demo_app.server import DemoServer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMBER_ID = "DEMO-101"
MEMBER_NAME = "Alex Example"
BALANCE = "1250.00"
MODEL_SUMMARY = "PRIVATE_MODEL_SUMMARY"
EXCEPTION_MESSAGE = "PRIVATE_EXCEPTION_MESSAGE"


def require_absent(text: str, *values: str) -> None:
    for value in values:
        if value in text:
            raise AssertionError(f"Terminal output exposed {value!r}.")


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


def check_formatter_contract() -> None:
    success = ReplaySuccess(
        outputs=BalanceResult(
            member_id=MEMBER_ID,
            account_type="savings",
            available_balance=Decimal(BALANCE),
            currency="USD",
        ),
        recovery_events=[],
    )
    business = ReplayBusinessOutcome(
        code="member_not_found",
        member_id=MEMBER_ID,
        step=2,
        recovery_events=[],
    )
    failure = ReplayFailure(
        code="verification_failed",
        step=4,
        expected=MODEL_SUMMARY,
        observed=EXCEPTION_MESSAGE,
        error_type="VerificationError",
        recovery_events=[],
    )

    rendered = "\n".join(
        (
            *discovery_result_lines(
                run_id="safe-run-id",
                dataset_id="members",
                capability_id="safe-capability-id",
            ),
            *replay_result_lines(success),
            *replay_result_lines(business),
            *replay_result_lines(failure),
            *proposal_lines(
                action="fill",
                metadata=ModelCallMetadata(
                    model="deterministic-test-model",
                    response_id="safe-response-id",
                    input_tokens=1,
                    output_tokens=2,
                    total_tokens=3,
                ),
            ),
            *observation_lines(
                url=(
                    "http://127.0.0.1:8000/members/"
                    f"{MEMBER_ID}/accounts/savings"
                ),
                snapshot_chars=123,
            ),
            exception_line(RuntimeError(EXCEPTION_MESSAGE)),
        )
    )

    require_absent(
        rendered,
        MEMBER_ID,
        BALANCE,
        MODEL_SUMMARY,
        EXCEPTION_MESSAGE,
    )
    assert "Replay status: success" in rendered
    assert "Outcome code: member_not_found" in rendered
    assert "Failure code: verification_failed" in rendered
    assert "Action: fill" in rendered
    assert "Page kind: account_details" in rendered
    assert "Failed: RuntimeError" in rendered

    stdout = io.StringIO()
    stderr = io.StringIO()

    def fail() -> None:
        raise RuntimeError(EXCEPTION_MESSAGE)

    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            run_cli(fail)
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("The failing CLI did not exit nonzero.")

    assert stdout.getvalue() == ""
    assert stderr.getvalue().strip() == "Failed: RuntimeError"
    require_absent(stderr.getvalue(), EXCEPTION_MESSAGE)


def check_discovery_output(directory: Path) -> None:
    scope = RequestScope(member_id=MEMBER_ID)
    proposals = [
        proposal(
            FillAction(
                kind="fill",
                label="Member ID",
                value=MEMBER_ID,
            ),
            1,
        ),
        proposal(
            ClickAction(kind="click", role="button", name="Search"),
            2,
        ),
        proposal(
            ClickAction(
                kind="click",
                role="link",
                name=f"{MEMBER_ID} — {MEMBER_NAME}",
            ),
            3,
        ),
        proposal(
            ClickAction(kind="click", role="link", name="Savings"),
            4,
        ),
        proposal(
            FinishAction(
                kind="finish",
                summary=(
                    f"{MODEL_SUMMARY}: {MEMBER_ID} {MEMBER_NAME} "
                    f"{BALANCE} USD"
                ),
            ),
            5,
        ),
    ]

    with (
        DemoServer(dataset_id="members") as demo,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(headless=True)

        try:
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.goto(demo.url)

            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                RunLog(
                    directory=directory,
                    mode="discovery",
                    target_url=demo.url,
                    dataset_id="members",
                ) as log,
                patch(
                    "automation.discovery.propose_action",
                    side_effect=proposals,
                ),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                run_discovery(
                    client=cast(Any, object()),
                    page=page,
                    goal="Find the requested savings balance.",
                    inputs=MemberLookupInputs(member_id=MEMBER_ID),
                    scope=scope,
                    blocked_requests=blocked_requests,
                    log=log,
                )

        finally:
            browser.close()

    rendered = stdout.getvalue() + stderr.getvalue()
    require_absent(
        rendered,
        MEMBER_ID,
        MEMBER_NAME,
        BALANCE,
        MODEL_SUMMARY,
    )
    assert stderr.getvalue() == ""

    for purpose in (
        "enter_member_id",
        "submit_member_search",
        "open_member_details",
        "open_savings_account",
        "report_observed_result",
    ):
        assert f"purpose={purpose}" in rendered


def check_replay_output(directory: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        patch("automation.jobs.PROJECT_ROOT", directory),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        result = replay_capability(
            MemberLookupInputs(member_id="DEMO-202"),
            dataset_id="members",
            capability_id="get_savings_balance",
            notice_mode="off",
            allow_human_takeover=False,
        )

    assert result.replay_result.status == "success"
    rendered = stdout.getvalue() + stderr.getvalue()
    require_absent(
        rendered,
        "DEMO-202",
        "Jordan Sample",
        "3875.50",
    )
    assert stderr.getvalue() == ""
    assert f"Member: {REDACTED}" in rendered
    assert "purpose=enter_member_id" in rendered
    assert "purpose=open_savings_account" in rendered


def check_print_sources() -> None:
    forbidden = (
        "model_dump",
        ".summary",
        ".member_id",
        "observation",
        "output_text",
        ".available_balance",
        ".outputs",
    )

    for directory in (
        PROJECT_ROOT / "automation",
        PROJECT_ROOT / "demo_app",
        PROJECT_ROOT / "scripts",
    ):
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                if isinstance(node.func, ast.Name) and node.func.id == "print":
                    rendered = ast.unparse(node)

                    if any(value in rendered for value in forbidden):
                        raise AssertionError(
                            f"Unsafe terminal serialization in {path.name}: "
                            f"{rendered}"
                        )

                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("print_")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "traceback"
                ):
                    raise AssertionError(
                        f"Raw traceback output remains in {path.name}."
                    )


def main() -> None:
    check_formatter_contract()
    check_print_sources()

    with TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        check_discovery_output(directory)
        check_replay_output(directory)

    print("Terminal privacy checks passed.")


if __name__ == "__main__":
    main()
