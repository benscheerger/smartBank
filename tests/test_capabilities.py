import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import ValidationError

from automation.capability import (
    CAPABILITY_SCHEMA_VERSION,
    build_input_schema,
    build_output_schema,
    parse_capability_artifact,
)
from automation.console import ConsoleController, create_console_app
from automation.evidence import EvidenceEvent, RunLog
from demo_app.server import DemoServer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_DIRECTORY = PROJECT_ROOT / "evidence" / "capabilities"
RUN_DIRECTORY = PROJECT_ROOT / "evidence" / "runs"


def require_rejected(data: Any, description: str) -> None:
    try:
        parse_capability_artifact(data)
    except (ValidationError, ValueError):
        return

    raise AssertionError(f"Accepted {description} capability artifact.")


def check_embedded_schemas() -> None:
    input_schema = build_input_schema()
    member_id = input_schema["properties"]["member_id"]

    if input_schema["required"] != ["member_id"]:
        raise AssertionError("member_id is not the required input.")

    if member_id["type"] != "string":
        raise AssertionError("member_id is not typed as a string.")

    if member_id["pattern"] != r"^DEMO-[0-9]+$":
        raise AssertionError("member_id has the wrong pattern.")

    output_schema = build_output_schema()
    mapping = output_schema["discriminator"]["mapping"]

    if set(mapping) != {"success", "business_outcome", "failure"}:
        raise AssertionError("Output result variants are incomplete.")

    balance = output_schema["$defs"]["BalanceResult"]

    if balance["properties"]["available_balance"]["type"] != "string":
        raise AssertionError("Serialized balances are not strings.")

    for definition in mapping.values():
        name = definition.removeprefix("#/$defs/")
        required = output_schema["$defs"][name]["required"]

        if "status" not in required:
            raise AssertionError(
                f"The {name} discriminator is not required."
            )


def check_saved_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    paths = sorted(CAPABILITY_DIRECTORY.glob("*.json"))

    if not paths:
        raise AssertionError("No saved capability artifacts were found.")

    sample = None
    legacy_sample = None
    found_legacy = False

    for path in paths:
        original = path.read_bytes()
        data = json.loads(original)
        capability = parse_capability_artifact(data)

        if path.read_bytes() != original:
            raise AssertionError(f"Loading modified {path.name}.")

        if capability.schema_version != CAPABILITY_SCHEMA_VERSION:
            raise AssertionError(f"Did not upgrade {path.name} in memory.")

        if data.get("schema_version") == "1.1":
            found_legacy = True

            if legacy_sample is None:
                legacy_sample = data

        if sample is None:
            sample = capability.model_dump(mode="json")

    if not found_legacy or legacy_sample is None:
        raise AssertionError("No legacy artifact exercised compatibility.")

    if sample is None:
        raise AssertionError("No capability was available for round-tripping.")

    return sample, legacy_sample


def check_validation(
    sample: dict[str, Any],
    legacy_sample: dict[str, Any],
) -> None:
    restored = parse_capability_artifact(sample)

    if restored.model_dump(mode="json") != sample:
        raise AssertionError("Version 1.2 did not round-trip.")

    missing_input = deepcopy(sample)
    missing_input.pop("input_schema")
    require_rejected(missing_input, "missing-input-schema")

    missing_output = deepcopy(sample)
    missing_output.pop("output_schema")
    require_rejected(missing_output, "missing-output-schema")

    changed = deepcopy(sample)
    changed["input_schema"]["properties"]["member_id"]["pattern"] = ".*"
    require_rejected(changed, "changed-schema")

    changed_output = deepcopy(sample)
    changed_output["output_schema"]["$defs"]["BalanceResult"][
        "properties"
    ]["currency"]["type"] = "integer"
    require_rejected(changed_output, "changed-output-schema")

    unsupported = deepcopy(sample)
    unsupported["schema_version"] = "99.0"
    require_rejected(unsupported, "unsupported-version")

    extra_field = deepcopy(sample)
    extra_field["unexpected"] = True
    require_rejected(extra_field, "extra-field")

    malformed_legacy = deepcopy(legacy_sample)
    malformed_legacy.pop("output_type")
    require_rejected(malformed_legacy, "malformed-legacy")

    require_rejected([], "non-object")


def check_discovery_target() -> None:
    controller = ConsoleController()
    controller.origin = "http://localhost"
    token = "test-token"
    app = create_console_app(controller, token)
    client = app.test_client()
    headers = {
        "Origin": controller.origin,
        "X-Console-Token": token,
    }

    page = client.get("/", base_url=controller.origin)

    if page.status_code != 200:
        raise AssertionError("The console page did not render.")

    html = page.get_data(as_text=True)

    if 'id="target"' not in html or DemoServer.url not in html:
        raise AssertionError("The console does not show the discovery target.")

    request_body = {
        "dataset_id": "members",
        "task": {
            "goal": "Find this member's savings balance.",
            "target_url": DemoServer.url,
            "inputs": {"member_id": "DEMO-101"},
        },
    }

    missing_target = deepcopy(request_body)
    missing_target["task"].pop("target_url")

    unsupported_target = deepcopy(request_body)
    unsupported_target["task"]["target_url"] = "https://example.com/"

    for body in (missing_target, unsupported_target):
        response = client.post(
            "/api/discovery",
            json=body,
            headers=headers,
            base_url=controller.origin,
        )

        if response.status_code != 400:
            raise AssertionError("The console accepted an invalid target.")

    accepted = client.post(
        "/api/discovery",
        json=request_body,
        headers=headers,
        base_url=controller.origin,
    )

    if accepted.status_code != 202:
        raise AssertionError("The console rejected the supported target.")

    if accepted.get_json()["target_url"] != DemoServer.url:
        raise AssertionError("The console did not retain the target URL.")


def check_evidence_compatibility() -> None:
    for path in RUN_DIRECTORY.glob("*.jsonl"):
        original = path.read_bytes()

        for line in original.splitlines():
            if line.strip():
                EvidenceEvent.model_validate_json(line)

        if path.read_bytes() != original:
            raise AssertionError(f"Reading modified {path.name}.")

    legacy = EvidenceEvent(
        schema_version="1.0",
        timestamp="2026-01-01T00:00:00+00:00",
        elapsed_ms=0,
        run_id="legacy-test",
        mode="discovery",
        source_run_id=None,
        event="run_started",
        step=None,
        action=None,
        error_type=None,
    )

    if legacy.target_url is not None:
        raise AssertionError("Legacy evidence gained target metadata.")

    with TemporaryDirectory() as directory:
        with RunLog(
            directory=Path(directory),
            mode="discovery",
            target_url=DemoServer.url,
        ) as log:
            path = log.path

        events = [
            EvidenceEvent.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]

    if not events or any(
        event.schema_version != "1.1"
        or event.target_url != DemoServer.url
        for event in events
    ):
        raise AssertionError("New evidence omitted the target URL.")


def main() -> None:
    check_embedded_schemas()
    sample, legacy_sample = check_saved_artifacts()
    check_validation(sample, legacy_sample)
    check_discovery_target()
    check_evidence_compatibility()
    print("Capability contract checks passed.")


if __name__ == "__main__":
    main()
