import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from openai import OpenAI
from playwright.sync_api import sync_playwright
from pydantic import Field, field_validator

from automation.actions import StrictModel
from automation.capability import Capability, MemberLookupInputs
from automation.discovery import run_discovery
from automation.evidence import RunLog
from automation.handoff import TakeoverPanelFactory
from automation.network import install_request_guard
from automation.planner import MODEL
from automation.policy import PolicyViolation, check_url
from automation.replay import run_replay
from automation.results import ReplayResult
from automation.verification import BalanceResult, verify_balance
from demo_app.server import DemoServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_DIRECTORY = (
    PROJECT_ROOT / "evidence" / "capabilities"
)


class DiscoveryTask(StrictModel):
    goal: str = Field(min_length=1, max_length=1000)
    inputs: MemberLookupInputs

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


class ReplayJobResult(StrictModel):
    run_id: str
    dataset_id: str
    capability_id: str
    replay_result: ReplayResult


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

    required_metadata = {"schema_version", "output_type"}

    if (
        not isinstance(artifact_data, dict)
        or not required_metadata.issubset(artifact_data)
    ):
        raise ValueError(
            "Artifact is missing required contract metadata."
        )

    return Capability.model_validate(artifact_data)


def discover_capability(
    task: DiscoveryTask,
    *,
    dataset_id: str,
    on_run_started: Callable[[str], None] | None = None,
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

    goal = (
        f"Task: {task.goal}\n"
        f"Target member ID: {inputs.member_id}\n"
        f"Account type: {account_type}."
    )

    with RunLog(
        directory=PROJECT_ROOT / "evidence" / "runs",
        mode="discovery",
        dataset_id=dataset_id,
    ) as log:
        if on_run_started is not None:
            on_run_started(log.run_id)

        print(f"Evidence log: {log.path}")
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
                            context
                        )

                        page = context.new_page()
                        page.set_default_timeout(5000)

                        check_url(demo.url)
                        page.goto(demo.url)

                        discovery = run_discovery(
                            client=client,
                            page=page,
                            goal=goal,
                            inputs=inputs,
                            blocked_requests=blocked_requests,
                            log=log,
                            max_steps=8,
                        )

                        log.emit(
                            "verification_started",
                            purpose="verify_account_result",
                        )

                        result = verify_balance(
                            page=page,
                            expected_member_id=inputs.member_id,
                            expected_account_type=account_type,
                        )

                        if blocked_requests:
                            raise PolicyViolation(blocked_requests[-1])

                        log.emit(
                            "verification_passed",
                            purpose="verify_account_result",
                        )

                        capability = Capability(
                            source_run_id=discovery.run_id,
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

                        # Preserve the existing replay default.
                        latest_path = (
                            capability_directory
                            / "get_savings_balance.json"
                        )
                        latest_path.write_text(
                            serialized,
                            encoding="utf-8",
                        )

                        log.emit("capability_saved")

                        print("\nModel summary:")
                        print(discovery.finish.summary)
                        print(f"\nSaved capability: {artifact_path}")

                        return DiscoveryJobResult(
                            run_id=log.run_id,
                            dataset_id=dataset_id,
                            capability_id=capability_id,
                            model_summary=discovery.finish.summary,
                            outputs=result,
                        )
                    finally:
                        browser.close()


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
    capability = load_saved_capability(capability_id)

    with RunLog(
        directory=PROJECT_ROOT / "evidence" / "runs",
        mode="replay",
        source_run_id=capability.source_run_id,
        dataset_id=dataset_id,
    ) as log:
        if on_run_started is not None:
            on_run_started(log.run_id)

        print(f"Evidence log: {log.path}")
        print(f"Replaying: {capability.name}")
        print(f"Dataset: {dataset_id}")
        print(f"Member: {inputs.member_id}")

        with DemoServer(
            dataset_id=dataset_id,
            notice_mode=notice_mode,
        ) as demo:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=False,
                    slow_mo=300,
                )

                try:
                    context = browser.new_context(
                        service_workers="block"
                    )
                    blocked_requests = install_request_guard(
                        context
                    )

                    page = context.new_page()
                    page.set_default_timeout(5000)

                    result = run_replay(
                        page=page,
                        capability=capability,
                        inputs=inputs,
                        blocked_requests=blocked_requests,
                        base_url=demo.url,
                        log=log,
                        allow_human_takeover=allow_human_takeover,
                        panel_factory=panel_factory,
                    )

                    return ReplayJobResult(
                        run_id=log.run_id,
                        dataset_id=dataset_id,
                        capability_id=capability_id,
                        replay_result=result,
                    )

                finally:
                    browser.close()