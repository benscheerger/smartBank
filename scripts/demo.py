"""Discover a savings-balance workflow, then replay it for another member."""

from automation.capability import MemberLookupInputs
from automation.jobs import (
    DiscoveryTask,
    discover_capability,
    replay_capability,
)
from automation.terminal import (
    discovery_result_lines,
    replay_result_lines,
    run_cli,
)
from demo_app.server import DemoServer


def main() -> None:
    task = DiscoveryTask(
        goal=(
            "Find this member's available savings balance "
            "and report its currency."
        ),
        target_url=DemoServer.url,
        inputs=MemberLookupInputs(member_id="DEMO-101"),
        allow_human_takeover=True,
    )

    discovery = discover_capability(
        task,
        dataset_id="members",
    )

    for line in discovery_result_lines(
        run_id=discovery.run_id,
        dataset_id=discovery.dataset_id,
        capability_id=discovery.capability_id,
    ):
        print(line)

    replay = replay_capability(
        MemberLookupInputs(member_id="DEMO-202"),
        dataset_id="members",
        capability_id=discovery.capability_id,
        notice_mode="off",
        allow_human_takeover=False,
    )

    for line in replay_result_lines(replay.replay_result):
        print(line)

    if replay.replay_result.status != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    run_cli(main)
