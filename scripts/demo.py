"""Discover a savings-balance workflow, then replay it for another member."""

from automation.capability import MemberLookupInputs
from automation.jobs import (
    DiscoveryTask,
    discover_capability,
    replay_capability,
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

    print("\nDiscovery result:")
    print(discovery.model_dump_json(indent=2))

    replay = replay_capability(
        MemberLookupInputs(member_id="DEMO-202"),
        dataset_id="members",
        capability_id=discovery.capability_id,
        notice_mode="off",
        allow_human_takeover=False,
    )

    print("\nReplay result:")
    print(replay.model_dump_json(indent=2))

    if replay.replay_result.status != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
