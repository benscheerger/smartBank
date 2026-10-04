from playwright.sync_api import sync_playwright

from automation.observation import observe_page
from automation.terminal import observation_lines, run_cli


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)

        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(5000)

            page.goto("http://127.0.0.1:8000")

            while True:
                observation = observe_page(page)

                for line in observation_lines(
                    url=observation["url"],
                    snapshot_chars=len(observation["snapshot"]),
                ):
                    print(line)

                command = input(
                    "\nNavigate in the browser, then press Enter "
                    "to observe again. Type q to quit: "
                )

                if command.strip().lower() == "q":
                    break

        finally:
            browser.close()


if __name__ == "__main__":
    run_cli(main)
