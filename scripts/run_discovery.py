from automation.capability import MemberLookupInputs
from automation.jobs import DiscoveryTask, discover_capability
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

    print("\nDiscovery result:")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
