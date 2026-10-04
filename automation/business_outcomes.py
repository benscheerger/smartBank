from typing import Literal
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Page

from automation.actions import StrictModel
from automation.policy import RequestScope, check_url
from automation.verification import VerificationError


class MemberNotFoundResult(StrictModel):
    status: Literal["member_not_found"] = "member_not_found"
    member_id: str


def detect_member_not_found(
    page: Page,
    expected_member_id: str,
    scope: RequestScope,
) -> MemberNotFoundResult | None:
    marker = page.locator('[data-outcome="member_not_found"]')
    count = marker.count()

    if count == 0:
        return None

    if count != 1:
        raise VerificationError("Ambiguous member-not-found outcome.")

    if not marker.is_visible():
        return None

    displayed_input = page.get_by_label(
        "Member ID",
        exact=True,
    ).input_value()

    current_url = page.url
    check_url(current_url, scope)

    parsed = urlsplit(current_url)
    query = parse_qs(parsed.query)

    if (
        parsed.path != "/"
        or query.get("member_id") != [expected_member_id]
        or displayed_input != expected_member_id
    ):
        raise VerificationError(
            "The missing-member outcome does not match the requested search."
        )

    return MemberNotFoundResult(member_id=expected_member_id)
