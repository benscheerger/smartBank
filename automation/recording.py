from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Page

from automation.actions import ClickAction, FillAction
from automation.capability import (
    InputReference,
    LiteralText,
    MemberLookupInputs,
    RecordedButtonClick,
    RecordedFill,
    RecordedLinkClick,
    TextTemplate,
)
from automation.evidence import ActionPurpose
from automation.policy import RequestScope, check_action, check_url


class RecordingError(Exception):
    """An action cannot be represented by this capability format."""


def parameterize_path(
    path: str,
    inputs: MemberLookupInputs,
) -> TextTemplate:
    parts = []

    for index, segment in enumerate(path.split("/")):
        if index > 0:
            parts.append(LiteralText(kind="literal", value="/"))

        if segment == inputs.member_id:
            parts.append(
                InputReference(kind="input", name="member_id")
            )
        elif segment:
            parts.append(LiteralText(kind="literal", value=segment))

    if not parts:
        raise RecordingError("Cannot record an empty path.")

    return TextTemplate(parts=parts)


def record_action(
    page: Page,
    action: FillAction | ClickAction,
    inputs: MemberLookupInputs,
    scope: RequestScope,
) -> RecordedFill | RecordedButtonClick | RecordedLinkClick:
    check_url(page.url, scope)
    check_action(action, scope)

    if isinstance(action, FillAction):
        if action.label != "Member ID" or action.value != inputs.member_id:
            raise RecordingError(
                "This capability only supports filling the requested member ID."
            )

        return RecordedFill(
            kind="fill",
            label=action.label,
            value=TextTemplate(
                parts=[
                    InputReference(kind="input", name="member_id")
                ]
            ),
        )

    if action.role == "button":
        return RecordedButtonClick(
            kind="click_button",
            name=action.name,
        )

    target = page.get_by_role("link", name=action.name, exact=True)
    href = target.get_attribute("href")

    if href is None:
        raise RecordingError("Cannot record a link without a destination.")

    destination = urljoin(page.url, href)
    check_url(destination, scope)

    parsed = urlsplit(destination)

    if parsed.query or parsed.fragment:
        raise RecordingError(
            "Recording links with queries or fragments is not supported yet."
        )

    return RecordedLinkClick(
        kind="click_link",
        href=parameterize_path(parsed.path, inputs),
    )

def recorded_action_purpose(
    action: RecordedFill | RecordedButtonClick | RecordedLinkClick,
    inputs: MemberLookupInputs,
) -> ActionPurpose:
    if isinstance(action, RecordedFill):
        if action.label == "Member ID":
            return "enter_member_id"

    elif isinstance(action, RecordedButtonClick):
        if action.name == "Search":
            return "submit_member_search"

    elif isinstance(action, RecordedLinkClick):
        path = action.href.resolve(inputs)

        if path == f"/members/{inputs.member_id}":
            return "open_member_details"

        if path == (
            f"/members/{inputs.member_id}/accounts/savings"
        ):
            return "open_savings_account"

    return "execute_recorded_step"
