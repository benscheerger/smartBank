import json
from urllib.parse import urljoin

from playwright.sync_api import Page

from automation.actions import (
    BrowserAction,
    ClickAction,
    FillAction,
    LinkClickAction,
)
from automation.policy import (
    PolicyViolation,
    RequestScope,
    check_action,
    check_url,
)


def execute_action(
    page: Page,
    action: BrowserAction,
    scope: RequestScope,
) -> None:
    check_url(page.url, scope)
    check_action(action, scope)

    if isinstance(action, FillAction):
        page.get_by_label(action.label, exact=True).fill(action.value)

    elif isinstance(action, ClickAction):
        target = page.get_by_role(
            action.role,
            name=action.name,
            exact=True,
        )

        if action.role == "link":
            href = target.get_attribute("href")

            if href is None:
                raise PolicyViolation("Link has no inspectable destination.")

            check_url(urljoin(page.url, href), scope)

        target.click()

    elif isinstance(action, LinkClickAction):
        check_url(urljoin(page.url, action.href), scope)

        # Match an actual link in the UI by its exact recorded destination.
        selector = f"a[href={json.dumps(action.href)}]"
        page.locator(selector).click()
