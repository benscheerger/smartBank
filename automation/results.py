from typing import Annotated, Literal

from pydantic import Field

from automation.actions import StrictModel
from automation.verification import BalanceResult


class RecoveryEvent(StrictModel):
    condition: Literal["blocking_notice"] = "blocking_notice"
    outcome: Literal[
        "recovered_automatically",
        "recovered_by_human",
        "exhausted",
        "cancelled",
        "timed_out",
    ]
    step: int | None


class ReplaySuccess(StrictModel):
    status: Literal["success"] = "success"
    outputs: BalanceResult
    recovery_events: list[RecoveryEvent]


class ReplayBusinessOutcome(StrictModel):
    status: Literal["business_outcome"] = "business_outcome"
    code: Literal["member_not_found"]
    member_id: str
    step: int = Field(ge=1)
    recovery_events: list[RecoveryEvent]


FailureCode = Literal[
    "policy_violation",
    "target_timeout",
    "checkpoint_mismatch",
    "verification_failed",
    "browser_error",
    "unsupported_action",
    "recovery_exhausted",
    "human_takeover_cancelled",
    "human_takeover_timed_out",
]


class ReplayFailure(StrictModel):
    status: Literal["failure"] = "failure"
    code: FailureCode
    step: int | None
    expected: str
    observed: str
    error_type: str
    recovery_events: list[RecoveryEvent]


ReplayResult = Annotated[
    ReplaySuccess | ReplayBusinessOutcome | ReplayFailure,
    Field(discriminator="status"),
]
