from playwright.sync_api import expect, sync_playwright
from automation.actions import ClickAction, FillAction
from automation.executor import execute_action
from automation.network import install_request_guard
from automation.policy import PolicyViolation, RequestScope
from automation.verification import VerificationError, verify_balance

def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            slow_mo=300,
        )

        try:
            scope = RequestScope(member_id="DEMO-101")
            context = browser.new_context(service_workers="block")
            blocked_requests = install_request_guard(context, scope)
            page = context.new_page()
            page.set_default_timeout(5000)

            # Open the member-search page.
            page.goto("http://127.0.0.1:8000")

            actions = [
                FillAction(
                    kind="fill",
                    label="Member ID",
                    value="DEMO-101",
                ),
                ClickAction(
                    kind="click",
                    role="button",
                    name="Search",
                ),
                ClickAction(
                    kind="click",
                    role="link",
                    name="DEMO-101 — Alex Example",
                ),
                ClickAction(
                    kind="click",
                    role="link",
                    name="Savings",
                ),
            ]

            for action in actions:
                execute_action(page, action, scope)

            # Verify that we reached the expected account.
            expect(
                page.get_by_role(
                    "heading",
                    name="Savings Account",
                    exact=True,
                )
            ).to_be_visible()

            expect(page.get_by_text("DEMO-101", exact=True)).to_be_visible()
            expect(page.get_by_text("1250.00", exact=True)).to_be_visible()
            expect(page.get_by_text("USD", exact=True)).to_be_visible()

            print("Browser check passed.")
            
            result = verify_balance(
                page,
                "DEMO-101",
                "savings",
                scope,
            )
            assert result.member_id == "DEMO-101"
            assert result.currency == "USD"
            print("Result verification passed.")

            try:
                verify_balance(page, "DEMO-202", "savings", scope)
            except VerificationError:
                print("Wrong-member verification correctly rejected.")
            else:
                raise AssertionError(
                    "Verification accepted the wrong member."
                )            

            blocked_before = len(blocked_requests)

            request_failed = page.evaluate("""
                async () => {
                    try {
                        await fetch("/", {method: "POST"});
                        return false;
                    } catch {
                        return true;
                    }
                }
            """)

            assert request_failed, "Forbidden request unexpectedly reached a response."
            assert len(blocked_requests) > blocked_before, (
                "The request failed without a recorded policy block."
            )

            print("Network policy check passed:", blocked_requests[-1])

            page.set_content(
                '<a href="/members/DEMO-202">Other member</a>'
            )

            try:
                execute_action(
                    page,
                    ClickAction(
                        kind="click",
                        role="link",
                        name="Other member",
                    ),
                    scope,
                )
            except PolicyViolation:
                pass
            else:
                raise AssertionError(
                    "A model-selected wrong-member link was allowed."
                )

            blocked_before = len(blocked_requests)
            page.evaluate(
                """
                () => [
                    "/members/DEMO-202",
                    "/members/DEMO-101/accounts/checking",
                ].forEach(path => {
                    const frame = document.createElement("iframe");
                    frame.src = path;
                    document.body.append(frame);
                })
                """
            )
            page.wait_for_timeout(500)

            assert len(blocked_requests) == blocked_before + 2
            assert all(
                "DEMO-202" not in reason
                for reason in blocked_requests
            )

        finally:
            browser.close()


if __name__ == "__main__":
    main()
