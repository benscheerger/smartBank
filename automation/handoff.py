import json
import webbrowser

from automation.takeover_controls import (
    ConsoleTakeover,
    OperatorPanel,
)
import time
from typing import Any, Protocol

from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from pydantic import ValidationError

from automation.actions import LinkClickAction
from automation.evidence import HumanAction, RunLog
from automation.policy import (
    PolicyViolation,
    check_action,
    check_url,
)
from automation.verification import VerificationError


class HumanTakeoverCancelled(VerificationError):
    pass


class HumanTakeoverTimedOut(VerificationError):
    pass


MANUAL_RECORDER = """
(() => {
    if (window.top !== window) return;
    if (window.__automationManualRecorderInstalled) return;

    window.__automationManualRecorderInstalled = true;

    function classifyTarget(element) {
        if (!element) return "other";

        if (element.tagName === "INPUT") {
            const labels = Array.from(element.labels || []);
            const isMemberField = labels.some(
                label => label.textContent.trim() === "Member ID"
            );
            return isMemberField ? "member_id_field" : "other";
        }

        if (element.tagName === "BUTTON") {
            const text = element.textContent.trim();

            if (text === "Search") return "search_button";
            if (text === "Dismiss notice") {
                return "dismiss_notice_button";
            }
            if (text === "Resolve notice manually") {
                return "manual_resolution_button";
            }
        }

        if (
            element.tagName === "A" &&
            element.textContent.trim() === "Savings"
        ) {
            return "savings_link";
        }

        return "other";
    }

    function record(event) {
        if (!event.isTrusted) return;
        if (!(event.target instanceof Element)) return;

        const element = event.target.closest("input, button, a");

        void window.__recordHumanAction({
            kind: event.type,
            target: classifyTarget(element),
        }).catch(() => {});
    }

    for (const kind of ["click", "input", "change"]) {
        document.addEventListener(kind, record, true);
    }
})();
"""

class TakeoverPanelFactory(Protocol):
    def __call__(
        self,
        *,
        run_id: str,
        member_id: str,
        step: int,
        timeout_seconds: float,
    ) -> OperatorPanel | ConsoleTakeover:
        ...
class HumanTakeover:
    def __init__(
        self,
        *,
        page: Page,
        log: RunLog,
        blocked_requests: list[str],
        enabled: bool,
        timeout_seconds: float = 180,
        panel_factory: TakeoverPanelFactory | None = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("Takeover timeout must be positive.")

        self.page = page
        self.log = log
        self.blocked_requests = blocked_requests
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

        self._active = False
        self._used = False
        self._step: int | None = None
        self._recorded_actions = 0
        self._recording_limit_reported = False
        self.panel_factory = panel_factory

        if enabled:
            page.expose_binding(
                "__recordHumanAction",
                self._record_action,
            )
            page.add_init_script(script=MANUAL_RECORDER)
            page.evaluate(MANUAL_RECORDER)

    def _record_action(
        self,
        source: dict[str, Any],
        payload: Any,
    ) -> None:
        if not self._active:
            return

        if source["page"] is not self.page:
            return

        if source["frame"] != self.page.main_frame:
            return

        try:
            action = HumanAction.model_validate(payload)
        except ValidationError:
            return

        if self._recorded_actions >= 100:
            if not self._recording_limit_reported:
                self.log.emit(
                    "manual_recording_limit",
                    step=self._step,
                )
                self._recording_limit_reported = True
            return

        self.log.emit(
            "manual_action",
            step=self._step,
            human_action=action,
        )
        self._recorded_actions += 1

    def _sync_notice_owner(self) -> None:
        if self.page.is_closed():
            return

        owner = "human" if self._active else "automation"

        try:
            self.page.evaluate(
                """
                (owner) => {
                    const root = document.documentElement;

                    if (root && root.dataset.noticeOwner !== owner) {
                        root.dataset.noticeOwner = owner;
                    }
                }
                """,
                owner,
            )
        except PlaywrightError:
            # Navigation may replace the document.
            # The takeover loop retries on its next iteration.
            pass

    def _check_session(self) -> None:
        if self.page.is_closed():
            raise VerificationError(
                "The original browser page was closed."
            )

        if self.blocked_requests:
            raise PolicyViolation(
                "A browser request was blocked during takeover."
            )

        check_url(self.page.url)

        if self.page.context.pages != [self.page]:
            raise VerificationError(
                "Takeover requires the original single-tab session."
            )

    def validate_member_details(
        self,
        *,
        member_id: str,
        expected_path: str,
        pending_action: LinkClickAction,
    ) -> None:
        self._check_session()

        member_path = f"/members/{member_id}"

        if expected_path != member_path:
            raise VerificationError(
                "This takeover supports the member-details checkpoint."
            )

        # Browser calls also process pending navigation/events.
        self.page.locator("body").aria_snapshot()

        self._check_session()

        if urlsplit(self.page.url).path != expected_path:
            raise VerificationError(
                "The browser is not at the required checkpoint."
            )

        member_marker = self.page.get_by_text(
            f"Member ID: {member_id}",
            exact=True,
        )

        if (
            member_marker.count() != 1
            or not member_marker.is_visible()
        ):
            raise VerificationError(
                "The displayed member does not match the request."
            )

        notices = self.page.get_by_role(
            "dialog",
            name="Service notice",
            exact=True,
        )

        if any(
            notices.nth(index).is_visible()
            for index in range(notices.count())
        ):
            raise VerificationError(
                "The service notice is still visible."
            )

        check_action(pending_action)
        check_url(
            urljoin(self.page.url, pending_action.href)
        )

        selector = f"a[href={json.dumps(pending_action.href)}]"
        target = self.page.locator(selector)

        if target.count() != 1:
            raise VerificationError(
                "The recorded link is not uniquely available."
            )

        # Check readiness without performing the recorded click.
        target.click(trial=True, timeout=1000)

    def restore_member_details(
        self,
        *,
        step: int,
        member_id: str,
        expected_path: str,
        pending_action: LinkClickAction,
    ) -> None:
        if not self.enabled:
            raise VerificationError(
                "Human takeover is disabled."
            )

        if self._used:
            raise VerificationError(
                "The run has already used its human takeover."
            )
        
        factory = self.panel_factory or OperatorPanel

        panel = factory(
            run_id=self.log.run_id,
            member_id=member_id,
            step=step,
            timeout_seconds=self.timeout_seconds,
        )

        self._used = True
        self._step = step

        with panel:
            self._active = True

            self.log.emit(
                "handoff_started",
                step=step,
                action="click_link",
                purpose="request_manual_repair",
            )

            try:
                print(
                    f"\nHuman takeover at step {step}: "
                    "automatic notice recovery was exhausted."
                )
                print(f"Operator panel: {panel.url}")
                print(
                    "Repair the banking browser, then use "
                    "Resume or Cancel in the operator panel."
                )

                if self.panel_factory is None:
                    try:
                        webbrowser.open(panel.url)
                    except webbrowser.Error:
                        print("Open the operator panel URL manually.")

                while True:
                    self._check_session()
                    self._sync_notice_owner()

                    if time.monotonic() >= panel.deadline:
                        panel.set_status(
                            "timed_out",
                            "Takeover expired. The run will stop.",
                        )
                        self.log.emit(
                            "handoff_timed_out",
                            step=step,
                            purpose="stop_after_takeover_timeout",
                        )
                        raise HumanTakeoverTimedOut(
                            "Human takeover expired."
                        )

                    command = panel.poll_command()

                    if command == "cancel":
                        panel.set_status(
                            "cancelled",
                            "The operator cancelled the run.",
                        )
                        self.log.emit(
                            "handoff_cancelled",
                            step=step,
                            purpose="stop_after_cancellation",
                        )
                        raise HumanTakeoverCancelled(
                            "The operator cancelled takeover."
                        )

                    if command == "resume":
                        try:
                            self.validate_member_details(
                                member_id=member_id,
                                expected_path=expected_path,
                                pending_action=pending_action,
                            )
                        except (
                            VerificationError,
                            PlaywrightError,
                        ) as exc:
                            self.log.emit(
                                "handoff_resume_rejected",
                                step=step,
                                error_type=type(exc).__name__,
                                purpose="validate_resume_checkpoint",
                            )

                            panel.set_status(
                                "human",
                                "Resume rejected. Restore the "
                                "requested member-details page, "
                                "remove the notice, and leave the "
                                "recorded link available.",
                            )
                            continue

                        self.log.emit(
                            "handoff_resumed",
                            step=step,
                            purpose="resume_after_validation",
                        )

                        panel.set_status(
                            "resumed",
                            "Checkpoint validated. Automation will continue "
                            "in the banking browser.",
)
                        return

                    # Process browser events and guarded requests
                    # while waiting for an operator command.
                    self.page.wait_for_timeout(100)

            finally:
                self._active = False
                self._sync_notice_owner()

                self.log.emit(
                    "handoff_ended",
                    step=step,
                )