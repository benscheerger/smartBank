import json
from urllib.parse import parse_qs, urljoin, urlsplit
from automation.recovery import (
    RecoveryBudget,
    RecoveryLimitExceeded,
    recover_known_notice,
)
from automation.handoff import (
    HumanTakeover,
    HumanTakeoverCancelled,
    HumanTakeoverTimedOut,
)
from automation.handoff import TakeoverPanelFactory

from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from automation.recording import recorded_action_purpose

from automation.actions import (
    BrowserAction,
    ClickAction,
    FillAction,
    LinkClickAction,
)
from automation.business_outcomes import detect_member_not_found
from automation.capability import (
    Capability,
    MemberLookupInputs,
    RecordedButtonClick,
    RecordedFill,
    RecordedLinkClick,
)
from automation.evidence import ActionKind, RunLog, save_failure_evidence
from automation.executor import execute_action
from automation.observation import observe_page
from automation.policy import PolicyViolation, check_url
from automation.results import (
    FailureCode,
    RecoveryEvent,
    ReplayBusinessOutcome,
    ReplayFailure,
    ReplayResult,
    ReplaySuccess,
)
from automation.verification import VerificationError, verify_balance


class ReplayError(Exception):
    """Replay cannot interpret a recorded operation."""


class CheckpointMismatch(ReplayError):
    """Replay reached a different path than expected."""


def resolve_action(
    action: RecordedFill | RecordedButtonClick | RecordedLinkClick,
    inputs: MemberLookupInputs,
) -> BrowserAction:
    if isinstance(action, RecordedFill):
        return FillAction(
            kind="fill",
            label=action.label,
            value=action.value.resolve(inputs),
        )

    if isinstance(action, RecordedButtonClick):
        return ClickAction(
            kind="click",
            role="button",
            name=action.name,
        )

    if isinstance(action, RecordedLinkClick):
        return LinkClickAction(
            kind="click_link",
            href=action.href.resolve(inputs),
        )

    raise ReplayError("Unsupported recorded action.")


def describe_location(url: str, expected_member_id: str) -> str:
    """Describe route structure without returning IDs or raw URLs."""
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")

    member_id = None

    if parsed.path == "/":
        page_kind = "search"
        values = parse_qs(parsed.query).get("member_id", [])
        if len(values) == 1:
            member_id = values[0]

    elif len(parts) == 2 and parts[0] == "members":
        page_kind = "member_details"
        member_id = parts[1]

    elif (
        len(parts) == 4
        and parts[0] == "members"
        and parts[2] == "accounts"
    ):
        page_kind = "account_details"
        member_id = parts[1]

    else:
        page_kind = "other"

    matches = (
        member_id == expected_member_id
        if member_id is not None
        else None
    )

    return (
        f"page={page_kind}; "
        f"requested_member_matches={matches}"
    )


def describe_observed_state(
    page: Page,
    inputs: MemberLookupInputs,
    action: BrowserAction | None,
) -> str:
    """Return safe diagnostic facts rather than raw page contents."""
    try:
        matches = None

        if isinstance(action, FillAction):
            matches = page.get_by_label(
                action.label, exact=True
            ).count()

        elif isinstance(action, ClickAction):
            matches = page.get_by_role(
                action.role, name=action.name, exact=True
            ).count()

        elif isinstance(action, LinkClickAction):
            selector = f"a[href={json.dumps(action.href)}]"
            matches = page.locator(selector).count()

        location = describe_location(page.url, inputs.member_id)
        return f"{location}; target_matches={matches}"

    except (PlaywrightError, ValueError):
        return "Page state could not be inspected."


def classify_failure(error: Exception) -> FailureCode:
    if isinstance(error, PolicyViolation):
        return "policy_violation"
    if isinstance(error, RecoveryLimitExceeded):
        return "recovery_exhausted"
    if isinstance(error, HumanTakeoverCancelled):
        return "human_takeover_cancelled"
    if isinstance(error, HumanTakeoverTimedOut):
        return "human_takeover_timed_out"
    if isinstance(error, CheckpointMismatch):
        return "checkpoint_mismatch"
    if isinstance(error, VerificationError):
        return "verification_failed"
    if isinstance(error, PlaywrightTimeoutError):
        return "target_timeout"
    if isinstance(error, PlaywrightError):
        return "browser_error"
    return "unsupported_action"


def run_replay(
    page: Page,
    capability: Capability,
    inputs: MemberLookupInputs,
    blocked_requests: list[str],
    base_url: str,
    log: RunLog,
    allow_human_takeover: bool = False,
    panel_factory: TakeoverPanelFactory | None = None,
) -> ReplayResult:
    
    step_index: int | None = None
    action_kind: ActionKind | None = None
    current_action: BrowserAction | None = None
    expected = "Navigate to the permitted entry page."
    recovery_budget = RecoveryBudget(limit=1)
    recovery_events: list[RecoveryEvent] = []

    try:
        start_url = urljoin(base_url, capability.start_path)
        check_url(start_url)
        page.goto(start_url)

        if blocked_requests:
            raise PolicyViolation(blocked_requests[-1])
        
        handoff = HumanTakeover(
            page=page,
            log=log,
            blocked_requests=blocked_requests,
            enabled=allow_human_takeover,
            panel_factory=panel_factory,
        )

        for index, step in enumerate(capability.steps, start=1):
            step_index = index
            action_kind = step.action.kind
            purpose = recorded_action_purpose(step.action, inputs)
            current_action = None
            expected = (
                f"Execute recorded {action_kind} action "
                "against a unique, actionable target."
            )

            log.emit(
                "step_started",
                step=index,
                action=action_kind,
                purpose=purpose,
            )

            action_kind = None
            expected = (
                "Clear a recognized service notice "
                "within the recovery budget."
            )

            try:
                recovery = recover_known_notice(
                    page=page,
                    log=log,
                    budget=recovery_budget,
                    step=step_index,
                    blocked_requests=blocked_requests,
                )

                if recovery is not None:
                    recovery_events.append(recovery)

            except RecoveryLimitExceeded:
                recovery_events.append(
                    RecoveryEvent(
                        outcome="exhausted",
                        step=step_index,
                    )
                )

                if not allow_human_takeover:
                    raise

                pending_action = resolve_action(
                    step.action,
                    inputs,
                )

                if not isinstance(
                    pending_action,
                    LinkClickAction,
                ):
                    raise VerificationError(
                        "This takeover supports a pending link action."
                    )

                expected_pre_action_path = (
                    capability.start_path
                    if step_index == 1
                    else capability.steps[
                        step_index - 2
                    ].checkpoint.expected_path.resolve(inputs)
                )

                expected = (
                    "Restore the requested member-details checkpoint "
                    "and an unobstructed, unique recorded link."
                )

                recovery_events.append(handoff.restore_member_details(
                    step=step_index,
                    member_id=inputs.member_id,
                    expected_path=expected_pre_action_path,
                    pending_action=pending_action,
                ))
            
            action_kind = step.action.kind
            expected = (
                f"Execute recorded {action_kind} action "
                "against a unique, actionable target."
            )

            current_action = resolve_action(step.action, inputs)
            execute_action(page, current_action)

            observation = observe_page(page)

            if blocked_requests:
                raise PolicyViolation(blocked_requests[-1])

            check_url(observation["url"])

            actual_path = urlsplit(observation["url"]).path
            expected_path = step.checkpoint.expected_path.resolve(inputs)

            expected = (
                "Match the recorded checkpoint: "
                + describe_location(expected_path, inputs.member_id)
            )

            if actual_path != expected_path:
                raise CheckpointMismatch("Page path did not match.")

            log.emit(
                "checkpoint_passed",
                step=index,
                action=action_kind,
                purpose=purpose,
            )

            print(
                f"Step {index}/{len(capability.steps)}: "
                f"{current_action.kind} — checkpoint passed"
            )

            expected = "Recognize any supported business outcome."
            business_outcome = detect_member_not_found(
                page=page,
                expected_member_id=inputs.member_id,
            )

            if blocked_requests:
                raise PolicyViolation(blocked_requests[-1])

            if business_outcome is not None:
                log.emit(
                    "member_not_found",
                    step=index,
                    action=action_kind,
                )
                return ReplayBusinessOutcome(
                    code="member_not_found",
                    member_id=business_outcome.member_id,
                    step=index,
                    recovery_events=recovery_events,
                )

        current_action = None
        action_kind = None

        expected = (
            "Clear a recognized service notice "
            "within the recovery budget."
        )

        recovery = recover_known_notice(
            page=page,
            log=log,
            budget=recovery_budget,
            step=step_index,
            blocked_requests=blocked_requests,
        )

        if recovery is not None:
            recovery_events.append(recovery)
        
        expected = (
            "Verify the requested savings account and extract "
            "a finite balance and valid currency code."
        )

        log.emit(
            "verification_started",
            purpose="verify_account_result",
        )

        result = verify_balance(
            page=page,
            expected_member_id=inputs.member_id,
            expected_account_type="savings",
        )

        if blocked_requests:
            raise PolicyViolation(blocked_requests[-1])

        log.emit(
            "verification_passed",
            purpose="verify_account_result",
        )
        return ReplaySuccess(
            outputs=result,
            recovery_events=recovery_events,
        )

    except (
        PolicyViolation,
        ReplayError,
        VerificationError,
        PlaywrightError,
    ) as error:
        if (
            isinstance(error, RecoveryLimitExceeded)
            and not (
                recovery_events
                and recovery_events[-1].outcome == "exhausted"
                and recovery_events[-1].step == step_index
            )
        ):
            recovery_events.append(
                RecoveryEvent(outcome="exhausted", step=step_index)
            )
        elif isinstance(error, HumanTakeoverCancelled):
            recovery_events.append(
                RecoveryEvent(outcome="cancelled", step=step_index)
            )
        elif isinstance(error, HumanTakeoverTimedOut):
            recovery_events.append(
                RecoveryEvent(outcome="timed_out", step=step_index)
            )

        failure = ReplayFailure(
            code=classify_failure(error),
            step=step_index,
            expected=expected,
            observed=describe_observed_state(
                page, inputs, current_action
            ),
            error_type=type(error).__name__,
            recovery_events=recovery_events,
        )

        log.mark_failed(
            error_type=failure.error_type,
            step=step_index,
            action=action_kind,
        )
        
        try:
            evidence_path = save_failure_evidence(
                page=page,
                log=log,
                failure=failure,
            )

            log.emit(
                "failure_evidence_saved",
                step=step_index,
                action=action_kind,
            )

            print(f"Failure evidence: {evidence_path}")

        except OSError:
            log.emit(
                "failure_evidence_unavailable",
                step=step_index,
                action=action_kind,
            )

            print("Failure evidence could not be written.")

        return failure
