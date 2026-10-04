from automation.capability import MemberLookupInputs
from automation.jobs import DiscoveryTask, discover_capability
from automation.terminal import discovery_result_lines, run_cli
from demo_app.server import DemoServer


def main():
    task = DiscoveryTask(
        goal=(
            "Find the requested member's available "
            "savings balance and currency."
        ),
        target_url=DemoServer.url,
        inputs=MemberLookupInputs(
            member_id="DEMO-101",
        ),
        allow_human_takeover=True,
    )

    result = discover_capability(
        task,
        dataset_id="members",
    )

    for line in discovery_result_lines(
        run_id=result.run_id,
        dataset_id=result.dataset_id,
        capability_id=result.capability_id,
    ):
        print(line)


if __name__ == "__main__":
    run_cli(main)
