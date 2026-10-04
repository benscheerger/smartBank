from typing import Any

from playwright.sync_api import (
    BrowserContext,
    Error,
    Route,
    WebSocketRoute,
)

from automation.policy import PolicyViolation, RequestScope, check_request


def install_request_guard(
    context: BrowserContext,
    scope: RequestScope,
) -> list[str]:
    blocked_requests: list[str] = []

    def handle_request(route: Route) -> None:
        request = route.request

        try:
            check_request(request.url, request.method, scope)

        except PolicyViolation as error:
            blocked_requests.append(str(error))
            route.abort("blockedbyclient")
            return

        try:
            response = route.fetch(
                max_redirects=0,
                max_retries=0,
                timeout=5000,
            )

        except Error:
            route.abort("failed")
            return

        try:
            if 300 <= response.status < 400:
                blocked_requests.append("HTTP redirects are blocked.")
                route.abort("blockedbyclient")
                return

            route.fulfill(response=response)

        finally:
            response.dispose()

    def handle_web_socket(route: WebSocketRoute) -> Any:
        blocked_requests.append(
            "WebSocket connections are blocked by policy."
        )

        # Playwright's sync close call cannot be nested inside its own
        # WebSocket route callback. Returning the implementation coroutine
        # lets the route dispatcher await the close on its event loop.
        implementation = getattr(route, "_impl_obj")
        return implementation.close(
            code=1008,
            reason="Blocked by automation policy.",
        )

    context.route_web_socket("**/*", handle_web_socket)
    context.route("**/*", handle_request)
    return blocked_requests
