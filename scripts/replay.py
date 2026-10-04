import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from automation.capability import (
    MemberLookupInputs,
    parse_capability_artifact,
)
from automation.evidence import RunLog
from automation.network import install_request_guard
from automation.replay import run_replay
from demo_app.server import DemoServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--member-id", required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument(
        "--human-takeover",
        action="store_true",
        help="Allow one operator takeover after exhausted notice recovery.",
    )
    args = parser.parse_args()

    inputs = MemberLookupInputs(member_id=args.member_id)

    project_root = Path(__file__).resolve().parents[1]

    artifact_path = args.artifact or (
        project_root
        / "evidence"
        / "capabilities"
        / "get_savings_balance.json"
    )

    artifact_data = json.loads(
        artifact_path.read_text(encoding="utf-8")
    )

    capability = parse_capability_artifact(artifact_data)

    with RunLog(
        directory=project_root / "evidence" / "runs",
        mode="replay",
        target_url=DemoServer.url,
        source_run_id=capability.source_run_id,
    ) as log:
        print(f"Evidence log: {log.path}")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False,
                slow_mo=300,
            )

            try:
                context = browser.new_context(
                    service_workers="block"
                )
                blocked_requests = install_request_guard(context)

                page = context.new_page()
                page.set_default_timeout(5000)

                print(f"Replaying: {capability.name}")
                print(f"Member: {inputs.member_id}")

                result = run_replay(
                    page=page,
                    capability=capability,
                    inputs=inputs,
                    blocked_requests=blocked_requests,
                    base_url=DemoServer.url,
                    log=log,
                    allow_human_takeover=args.human_takeover,
                )

                print("\nReplay result:")
                print(result.model_dump_json(indent=2))

            finally:
                browser.close()

    if result.status == "failure":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
