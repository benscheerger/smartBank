from playwright.sync_api import sync_playwright

from automation.actions import ClickAction, FillAction
from automation.business_outcomes import detect_member_not_found
from automation.executor import execute_action
from automation.network import install_request_guard
from automation.policy import RequestScope


def search_member(page, member_id, scope):
    execute_action(
        page,
        FillAction(
            kind="fill",
            label="Member ID",
            value=member_id,
        ),
        scope,
    )
    execute_action(
        page,
        ClickAction(
            kind="click",
            role="button",
            name="Search",
        ),
        scope,
    )


def lookup_member(browser, member_id):
    scope = RequestScope(member_id=member_id)
    context = browser.new_context(service_workers="block")
    blocked_requests = install_request_guard(context, scope)

    try:
        page = context.new_page()
        page.set_default_timeout(5000)
        page.goto("http://127.0.0.1:8000/")
        search_member(page, member_id, scope)
        outcome = detect_member_not_found(page, member_id, scope)
        assert not blocked_requests
        return outcome
    finally:
        context.close()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)

        try:
            assert lookup_member(browser, "DEMO-202") is None
            print("Existing member correctly has no missing-member outcome.")

            outcome = lookup_member(browser, "DEMO-999")

            assert outcome is not None
            assert outcome.status == "member_not_found"
            assert outcome.member_id == "DEMO-999"

            print("Missing-member outcome correctly detected.")

        finally:
            browser.close()


if __name__ == "__main__":
    main()
