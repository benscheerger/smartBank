from dataclasses import dataclass

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation.actions import ClickAction
from automation.evidence import RunLog
from automation.executor import execute_action
from automation.policy import PolicyViolation
from automation.results import RecoveryEvent
from automation.verification import VerificationError


class RecoveryLimitExceeded(VerificationError):
    """A known blocking notice could not be cleared safely."""


@dataclass
class RecoveryBudget:
    limit: int = 1
    attempts: int = 0

    def __post_init__(self):
        if self.limit < 0:
            raise ValueError("Recovery limit cannot be negative.")


def recover_known_notice(
    page: Page,
    log: RunLog,
    budget: RecoveryBudget,
    step: int | None,
    blocked_requests: list[str],
) -> RecoveryEvent | None:
    notice = page.get_by_role(
        "dialog",
        name="Service notice",
        exact=True,
    )

    count = notice.count()

    if count == 0:
        return None

    if count != 1:
        raise VerificationError("Ambiguous service notice.")

    if not notice.is_visible():
        return None

    if budget.attempts >= budget.limit:
        log.emit(
            "recovery_exhausted",
            step=step,
            purpose="dismiss_blocking_notice",
        )
        raise RecoveryLimitExceeded("Recovery budget exhausted.")

    dismiss = notice.get_by_role(
        "button",
        name="Dismiss notice",
        exact=True,
    )

    if dismiss.count() != 1:
        raise VerificationError("Notice has no unique dismissal control.")

    budget.attempts += 1
    log.emit(
        "recovery_started",
        step=step,
        action="click",
        purpose="dismiss_blocking_notice",
    )

    execute_action(
        page,
        ClickAction(
            kind="click",
            role="button",
            name="Dismiss notice",
        ),
    )

    if blocked_requests:
        raise PolicyViolation(blocked_requests[-1])

    try:
        notice.wait_for(state="hidden", timeout=1500)

    except PlaywrightTimeoutError as error:
        if blocked_requests:
            raise PolicyViolation(blocked_requests[-1]) from error

        log.emit(
            "recovery_exhausted",
            step=step,
            action="click",
            purpose="dismiss_blocking_notice",
        )
        raise RecoveryLimitExceeded(
            "The notice remained visible after dismissal."
        ) from error

    log.emit(
        "recovery_completed",
        step=step,
        action="click",
        purpose="dismiss_blocking_notice",
    )

    return RecoveryEvent(
        outcome="recovered_automatically",
        step=step,
    )
