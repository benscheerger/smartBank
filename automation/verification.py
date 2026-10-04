import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from playwright.sync_api import Page

from automation.actions import StrictModel
from automation.policy import RequestScope, check_url


class VerificationError(Exception):
    """The page does not provide a valid result for the requested task."""


class BalanceResult(StrictModel):
    member_id: str
    account_type: str
    available_balance: Decimal
    currency: str


def read_detail(page: Page, label: str) -> str:
    term = page.locator("dt").filter(
        has_text=re.compile(rf"^\s*{re.escape(label)}\s*$")
    )

    if term.count() != 1:
        raise VerificationError(
            f"Expected exactly one detail label: {label}."
        )

    # Read the <dd> immediately following this <dt>.
    value = term.locator("xpath=following-sibling::*[1][self::dd]")

    if value.count() != 1 or not value.is_visible():
        raise VerificationError(
            f"Missing or hidden detail value: {label}."
        )

    text = value.inner_text().strip()

    if not text:
        raise VerificationError(f"Empty detail value: {label}.")

    return text


def verify_balance(
    page: Page,
    expected_member_id: str,
    expected_account_type: str,
    scope: RequestScope,
) -> BalanceResult:
    # A browser call also brings Playwright's page state up to date.
    heading = page.get_by_role(
        "heading",
        name=f"{expected_account_type.capitalize()} Account",
        exact=True,
        level=1,
    )

    if heading.count() != 1 or not heading.is_visible():
        raise VerificationError("Expected account heading is missing.")

    current_url = page.url
    check_url(current_url, scope)

    expected_path = (
        f"/members/{expected_member_id}"
        f"/accounts/{expected_account_type}"
    )

    if urlsplit(current_url).path != expected_path:
        raise VerificationError(
            "The page URL does not match the requested member and account."
        )

    member_id = read_detail(page, "Member ID")

    if member_id != expected_member_id:
        raise VerificationError(
            "The displayed member ID does not match the request."
        )

    balance_text = read_detail(page, "Available balance")

    try:
        balance = Decimal(balance_text)
    except InvalidOperation as error:
        raise VerificationError("The balance is not numeric.") from error

    if not balance.is_finite():
        raise VerificationError("The balance must be a finite number.")

    currency = read_detail(page, "Currency")

    if re.fullmatch(r"[A-Z]{3}", currency) is None:
        raise VerificationError(
            "Expected a three-letter uppercase currency code."
        )

    return BalanceResult(
        member_id=member_id,
        account_type=expected_account_type,
        available_balance=balance,
        currency=currency,
    )
