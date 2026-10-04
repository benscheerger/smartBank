from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

from automation.evidence import (
    FailureEvidence,
    save_failure_evidence,
    RunLog,
)
from automation.results import ReplayFailure
from demo_app.server import DemoServer


def main():
    with TemporaryDirectory() as temporary_directory:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            try:
                page = browser.new_page()
                page.set_content("""
                    <h1>PRIVATE_TEST_CUSTOMER</h1>

                    <label for="member-id">Member ID</label>
                    <input
                        id="member-id"
                        value="PRIVATE_TEST_MEMBER"
                        data-token="PRIVATE_TEST_TOKEN"
                    >

                    <a href="/members/PRIVATE_TEST_MEMBER?token=PRIVATE_TEST_TOKEN">
                        Savings
                    </a>

                    <p>PRIVATE_TEST_BALANCE</p>
                """)

                failure = ReplayFailure(
                    code="target_timeout",
                    step=4,
                    expected="Find one recorded target.",
                    observed="Target matches: zero.",
                    error_type="TimeoutError",
                    recovery_events=[],
                )

                with RunLog(
                    directory=Path(temporary_directory),
                    mode="replay",
                    target_url=DemoServer.url,
                ) as log:
                    path = save_failure_evidence(page, log, failure)
                    text = path.read_text(encoding="utf-8")

                    for value in (
                        "PRIVATE_TEST_CUSTOMER",
                        "PRIVATE_TEST_MEMBER",
                        "PRIVATE_TEST_TOKEN",
                        "PRIVATE_TEST_BALANCE",
                    ):
                        assert value not in text, (
                            "Sensitive test data appeared in evidence."
                        )

                    evidence = FailureEvidence.model_validate_json(text)

                    assert evidence.capture_error is None
                    assert evidence.dom is not None
                    assert evidence.dom.nodes
                    assert evidence.known_controls is not None
                    assert evidence.known_controls.member_id_fields == 1
                    assert evidence.known_controls.savings_links == 1

                    log.mark_failed(
                        error_type=failure.error_type,
                        step=4,
                        action="click_link",
                    )

                print("Failure evidence privacy check passed.")

            finally:
                browser.close()


if __name__ == "__main__":
    main()
