import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from automation.actions import ClickAction, FillAction
from automation.policy import (
    DEFAULT_POLICY_PATH,
    PolicyViolation,
    RequestScope,
    check_action,
    check_request,
    check_url,
    load_policy,
)


def assert_blocked(operation):
    try:
        operation()
    except PolicyViolation:
        return

    raise AssertionError("Expected the operation to be blocked.")


def main():
    scope = RequestScope(member_id="DEMO-101")

    for invalid_scope in (
        lambda: RequestScope(member_id="INVALID"),
        lambda: RequestScope(
            member_id="DEMO-101",
            account_type=cast(Any, "checking"),
        ),
    ):
        try:
            invalid_scope()
        except ValueError:
            pass
        else:
            raise AssertionError("Accepted an invalid request scope.")

    search = ClickAction(
        kind="click",
        role="button",
        name="Search",
    )
    transfer = ClickAction(
        kind="click",
        role="button",
        name="Transfer",
    )
    member_fill = FillAction(
        kind="fill",
        label="Member ID",
        value="DEMO-101",
    )
    wrong_member_fill = FillAction(
        kind="fill",
        label="Member ID",
        value="DEMO-202",
    )

    check_action(search, scope)
    check_action(member_fill, scope)
    check_request("http://127.0.0.1:8000/", "GET", scope)
    check_url(
        "http://127.0.0.1:8000/?member_id=DEMO-101",
        scope,
    )
    check_url("http://127.0.0.1:8000/members/DEMO-101", scope)
    check_url(
        "http://127.0.0.1:8000/members/DEMO-101/accounts/savings",
        scope,
    )

    assert_blocked(
        lambda: check_url("https://example.com/", scope)
    )
    assert_blocked(
        lambda: check_request(
            "http://127.0.0.1:8000/",
            "POST",
            scope,
        )
    )
    assert_blocked(lambda: check_action(wrong_member_fill, scope))

    blocked_urls = (
        "http://127.0.0.1:8000/?member_id=DEMO-202",
        "http://127.0.0.1:8000/?member_id=",
        (
            "http://127.0.0.1:8000/"
            "?member_id=DEMO-101&member_id=DEMO-101"
        ),
        "http://127.0.0.1:8000/?member_id=DEMO-101&extra=1",
        "http://127.0.0.1:8000/members/DEMO-202",
        (
            "http://127.0.0.1:8000/members/"
            "DEMO-101/accounts/checking"
        ),
        "http://127.0.0.1:8000/members/DEMO-101?extra=1",
        "http://127.0.0.1:8000/members/DEMO-101#private",
    )

    for url in blocked_urls:
        assert_blocked(lambda url=url: check_url(url, scope))

    data = json.loads(
        DEFAULT_POLICY_PATH.read_text(encoding="utf-8")
    )
    data["allowed_path_patterns"] = [r".*"]
    data["allowed_button_names"] = ["Dismiss notice", "Transfer"]

    with TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "policy.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        custom_policy = load_policy(path)

        # A custom base policy cannot broaden the active request scope.
        assert_blocked(
            lambda: check_url(
                "http://127.0.0.1:8000/members/DEMO-202",
                scope,
                policy=custom_policy,
            )
        )

        # Search was removed from this configuration.
        assert_blocked(
            lambda: check_action(
                search,
                scope,
                policy=custom_policy,
            )
        )

        # Transfer is permitted by name but explicitly blocked as risky.
        assert_blocked(
            lambda: check_action(
                transfer,
                scope,
                policy=custom_policy,
            )
        )

    print("Configurable policy checks passed.")


if __name__ == "__main__":
    main()
