from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from playwright.sync_api import sync_playwright

from automation.network import install_request_guard
from automation.observation import observe_page
from automation.planner import MODEL, propose_action
from automation.policy import PolicyViolation, check_url


def main():
    key_file = (
        Path.home()
        / ".config"
        / "computer-use-automation"
        / ".env"
    )
    load_dotenv(key_file, override=True)

    goal = (
        "Find member DEMO-101 and return their savings "
        "account's available balance and currency."
    )
    start_url = "http://127.0.0.1:8000/"

    with OpenAI(timeout=30.0, max_retries=0) as client:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)

            try:
                context = browser.new_context(service_workers="block")
                blocked_requests = install_request_guard(context)

                page = context.new_page()
                page.set_default_timeout(5000)

                check_url(start_url)
                page.goto(start_url)
                observation = observe_page(page)

                if blocked_requests:
                    raise PolicyViolation(blocked_requests[-1])

                proposal = propose_action(
                    client=client,
                    goal=goal,
                    observation=observation,
                )

                print(f"Model: {MODEL}")
                print(proposal.request.model_dump_json(indent=2))
                print(proposal.metadata.model_dump_json(indent=2))
                print("Proposal only; no action was executed.")

            finally:
                browser.close()


if __name__ == "__main__":
    main()
