from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Never
from urllib.parse import urlsplit

from automation.evidence import (
    ActionKind,
    ActionPurpose,
    ModelCallMetadata,
)
from automation.results import (
    ReplayBusinessOutcome,
    ReplayFailure,
    ReplaySuccess,
)

if TYPE_CHECKING:
    from automation.jobs import ReplayJobResult

REDACTED = "[redacted]"


def action_progress(
    *,
    step: int,
    action: ActionKind,
    purpose: ActionPurpose,
    total: int | None = None,
) -> str:
    position = str(step) if total is None else f"{step}/{total}"
    return f"Step {position}: action={action}; purpose={purpose}"


def redacted_member() -> str:
    return f"Member: {REDACTED}"


def discovery_result_lines(
    *,
    run_id: str,
    dataset_id: str,
    capability_id: str,
) -> tuple[str, ...]:
    return (
        "Discovery status: success",
        f"Run ID: {run_id}",
        f"Dataset: {dataset_id}",
        f"Capability: {capability_id}",
        "Verified output is available in the caller result.",
    )


def replay_result_lines(
    result: ReplaySuccess | ReplayBusinessOutcome | ReplayFailure,
) -> tuple[str, ...]:
    lines = [f"Replay status: {result.status}"]

    if isinstance(result, ReplayBusinessOutcome):
        lines.append(f"Outcome code: {result.code}")
    elif isinstance(result, ReplayFailure):
        lines.extend(
            (
                f"Failure code: {result.code}",
                f"Error type: {result.error_type}",
            )
        )

    outcomes = ", ".join(
        event.outcome for event in result.recovery_events
    )
    lines.append(f"Recovery outcomes: {outcomes or 'none'}")
    return tuple(lines)


def replay_job_result_lines(
    result: ReplayJobResult,
) -> tuple[str, ...]:
    if result.kind == "job_failure":
        return (
            "Replay job status: failure",
            f"Failure code: {result.code}",
            f"Failure stage: {result.stage}",
            f"Error type: {result.error_type}",
        )

    return replay_result_lines(result.replay_result)


def model_call_lines(
    metadata: ModelCallMetadata,
) -> tuple[str, ...]:
    return (
        f"Provider: {metadata.provider}",
        f"Model: {metadata.model}",
        f"Response ID: {metadata.response_id}",
        f"Input tokens: {metadata.input_tokens}",
        f"Output tokens: {metadata.output_tokens}",
        f"Total tokens: {metadata.total_tokens}",
    )


def proposal_lines(
    *,
    action: ActionKind,
    metadata: ModelCallMetadata,
) -> tuple[str, ...]:
    return (f"Action: {action}", *model_call_lines(metadata))


def observation_lines(
    *,
    url: str,
    snapshot_chars: int,
) -> tuple[str, ...]:
    path = urlsplit(url).path
    parts = path.strip("/").split("/")

    if path == "/":
        page_kind = "search"
    elif len(parts) == 2 and parts[0] == "members":
        page_kind = "member_details"
    elif (
        len(parts) == 4
        and parts[0] == "members"
        and parts[2] == "accounts"
    ):
        page_kind = "account_details"
    else:
        page_kind = "other"

    return (
        f"Page kind: {page_kind}",
        f"Observation characters: {snapshot_chars}",
    )


def exception_line(error: BaseException) -> str:
    return f"Failed: {type(error).__name__}"


def run_cli(main: Callable[[], None]) -> Never:
    try:
        main()
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - sanitize the CLI boundary.
        print(exception_line(error), file=sys.stderr)
        raise SystemExit(1) from None

    raise SystemExit(0)
