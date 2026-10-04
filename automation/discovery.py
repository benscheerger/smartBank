from dataclasses import dataclass
from urllib.parse import urlsplit

from openai import OpenAI
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation.actions import FinishAction
from automation.capability import (
    CapabilityStep,
    MemberLookupInputs,
    PageCheckpoint,
)
from automation.evidence import InterventionReason, RunLog
from automation.executor import execute_action
from automation.handoff import HumanTakeover, TakeoverPanelFactory
from automation.observation import observe_page
from automation.planner import propose_action
from automation.policy import PolicyViolation, check_url
from automation.recording import (
    parameterize_path,
    record_action,
    recorded_action_purpose,
)


@dataclass
class DiscoveryRun:
    run_id: str
    finish: FinishAction
    steps: list[CapabilityStep]


class DiscoveryStepLimitReached(RuntimeError):
    def __init__(self, step: int):
        self.step = step
        super().__init__(
            f"Discovery reached its limit at step {step} "
            "without a finish proposal."
        )


class DiscoveryTargetTimeout(RuntimeError):
    def __init__(self, step: int, action: str):
        self.step = step
        self.action = action
        super().__init__(
            f"Discovery target timed out at step {step} ({action})."
        )


def run_discovery(
    client: OpenAI,
    page: Page,
    goal: str,
    inputs: MemberLookupInputs,
    blocked_requests: list[str],
    log: RunLog,
    max_steps: int = 8,
    allow_human_takeover: bool = False,
    panel_factory: TakeoverPanelFactory | None = None,
) -> DiscoveryRun:
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1.")

    run_id = log.run_id
    recorded_steps: list[CapabilityStep] = []
    handoff = HumanTakeover(
        page=page,
        log=log,
        blocked_requests=blocked_requests,
        enabled=allow_human_takeover,
        panel_factory=panel_factory,
    )

    step = 1
    remaining = max_steps
    escalated = False

    while True:
        resumed_after_timeout = False

        while remaining > 0:
            observation = observe_page(page)

            if blocked_requests:
                raise PolicyViolation(blocked_requests[-1])

            log.emit("model_requested", step=step)

            proposal = propose_action(
                client=client,
                goal=goal,
                observation=observation,
            )
            action = proposal.request.action

            log.emit(
                "action_proposed",
                step=step,
                action=action.kind,
                purpose=(
                    "report_observed_result"
                    if isinstance(action, FinishAction)
                    else None
                ),
                model_call=proposal.metadata,
            )

            print(f"\nStep {step}")
            print(action.model_dump_json(indent=2))

            if isinstance(action, FinishAction):
                return DiscoveryRun(
                    run_id=run_id,
                    finish=action,
                    steps=recorded_steps,
                )

            try:
                recorded_action = record_action(page, action, inputs)
                purpose = recorded_action_purpose(recorded_action, inputs)

                log.emit(
                    "step_started",
                    step=step,
                    action=action.kind,
                    purpose=purpose,
                )

                execute_action(page, action)
                after = observe_page(page)

            except PlaywrightTimeoutError as error:
                reason: InterventionReason = "discovery_target_timeout"
                log.emit(
                    "discovery_blocked",
                    step=step,
                    action=action.kind,
                    intervention_reason=reason,
                )

                if allow_human_takeover and not escalated:
                    handoff.resume_discovery(
                        step=step,
                        member_id=inputs.member_id,
                        goal=goal,
                        reason=reason,
                        action=action.kind,
                    )
                    escalated = True
                    remaining = max_steps
                    step += 1
                    resumed_after_timeout = True
                    break

                raise DiscoveryTargetTimeout(
                    step=step,
                    action=action.kind,
                ) from error

            if blocked_requests:
                raise PolicyViolation(blocked_requests[-1])

            check_url(after["url"])
            checkpoint = PageCheckpoint(
                expected_path=parameterize_path(
                    urlsplit(after["url"]).path,
                    inputs,
                )
            )
            recorded_steps.append(
                CapabilityStep(
                    action=recorded_action,
                    checkpoint=checkpoint,
                )
            )
            log.emit(
                "step_completed",
                step=step,
                action=action.kind,
                purpose=purpose,
            )

            step += 1
            remaining -= 1

        if resumed_after_timeout:
            continue

        completed_step = step - 1
        reason = "discovery_step_limit"
        log.emit(
            "discovery_blocked",
            step=completed_step,
            intervention_reason=reason,
        )

        if allow_human_takeover and not escalated:
            handoff.resume_discovery(
                step=completed_step,
                member_id=inputs.member_id,
                goal=goal,
                reason=reason,
                action=None,
            )
            escalated = True
            remaining = max_steps
            continue

        raise DiscoveryStepLimitReached(completed_step)
