import argparse
import subprocess
import sys
from pathlib import Path

from demo_app.server import DemoServer, DemoServerStartError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TIMEOUT_SECONDS = 600

SERVER_TESTS = (
    "tests.test_browser",
    "tests.test_business_outcomes",
)


def run_test(module: str, *extra_args: str) -> str:
    print(f"\nRunning {module}", flush=True)

    try:
        result = subprocess.run(
            [sys.executable, "-u", "-m", module, *extra_args],
            cwd=PROJECT_ROOT,
            timeout=TEST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
    except OSError as exc:
        print(
            f"Could not start test: {type(exc).__name__}",
            flush=True,
        )
        status = "ERROR"
    else:
        status = (
            "PASS"
            if result.returncode == 0
            else f"FAIL (exit {result.returncode})"
        )

    print(f"{status}: {module}", flush=True)
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-discovery",
        action="store_true",
        help="Include fresh discovery using the model API.",
    )
    args = parser.parse_args()

    results: list[tuple[str, str]] = []

    # These tests do not require the demo server.
    for module in (
        "tests.test_capabilities",
        "tests.test_policy",
        "tests.test_failure_evidence",
    ):
        results.append((module, run_test(module)))

    server_available = True

    # These tests expect the default dataset and no notice.
    try:
        with DemoServer(
            dataset_id="members",
            notice_mode="off",
        ):
            for module in SERVER_TESTS:
                results.append((module, run_test(module)))

    except DemoServerStartError as exc:
        server_available = False
        print(
            f"\nDemo server setup failed: {exc}",
            flush=True,
        )

        for module in SERVER_TESTS:
            results.append((module, "BLOCKED"))

    # The shared server is stopped before the workflow test,
    # which starts and stops its own demo servers.
    workflow_module = "tests.test_workflows"

    if server_available:
        workflow_args = (
            ("--live-discovery",)
            if args.live_discovery
            else ()
        )

        results.append(
            (
                workflow_module,
                run_test(workflow_module, *workflow_args),
            )
        )
    else:
        results.append((workflow_module, "BLOCKED"))

    print("\nTest summary", flush=True)

    for module, status in results:
        print(f"  {status}: {module}", flush=True)

    passed = sum(
        status == "PASS"
        for _, status in results
    )

    print(
        f"\n{passed}/{len(results)} modules passed.",
        flush=True,
    )

    raise SystemExit(
        0
        if all(status == "PASS" for _, status in results)
        else 1
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTest run interrupted.", flush=True)
        raise SystemExit(130)
