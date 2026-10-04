from typing import Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from automation.actions import StrictModel
from automation.results import ReplayResult as CurrentReplayResult
from automation.verification import BalanceResult


CAPABILITY_SCHEMA_VERSION: Literal["1.3"] = "1.3"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class MemberLookupInputs(StrictModel):
    member_id: str = Field(pattern=r"^DEMO-[0-9]+$")


class LiteralText(StrictModel):
    kind: Literal["literal"]
    value: str


class InputReference(StrictModel):
    kind: Literal["input"]
    name: Literal["member_id"]


TextPart = Annotated[
    LiteralText | InputReference,
    Field(discriminator="kind"),
]


class TextTemplate(StrictModel):
    parts: list[TextPart] = Field(min_length=1)

    def resolve(self, inputs: MemberLookupInputs) -> str:
        pieces = []

        for part in self.parts:
            if isinstance(part, LiteralText):
                pieces.append(part.value)
            else:
                pieces.append(inputs.member_id)

        return "".join(pieces)


class PageCheckpoint(StrictModel):
    expected_path: TextTemplate


class RecordedFill(StrictModel):
    kind: Literal["fill"]
    label: str = Field(min_length=1)
    value: TextTemplate


class RecordedButtonClick(StrictModel):
    kind: Literal["click_button"]
    name: str = Field(min_length=1)


class RecordedLinkClick(StrictModel):
    kind: Literal["click_link"]
    href: TextTemplate


RecordedAction = Annotated[
    RecordedFill | RecordedButtonClick | RecordedLinkClick,
    Field(discriminator="kind"),
]


class CapabilityStep(StrictModel):
    action: RecordedAction
    checkpoint: PageCheckpoint


def _build_input_schema(version: str) -> dict[str, Any]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": (
            "urn:smartbank:get_savings_balance:"
            f"{version}:inputs"
        ),
        **MemberLookupInputs.model_json_schema(mode="validation"),
    }


def build_input_schema() -> dict[str, Any]:
    return _build_input_schema(CAPABILITY_SCHEMA_VERSION)


def _build_output_schema(
    version: str,
    result_type: Any,
) -> dict[str, Any]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": (
            "urn:smartbank:get_savings_balance:"
            f"{version}:outputs"
        ),
        **TypeAdapter(result_type).json_schema(mode="serialization"),
    }


def build_output_schema() -> dict[str, Any]:
    return _build_output_schema(
        CAPABILITY_SCHEMA_VERSION,
        CurrentReplayResult,
    )


# These models preserve the generated schema embedded in version 1.2
# artifacts. Their names intentionally match the original $defs keys.
class ReplaySuccess(StrictModel):
    status: Literal["success"] = "success"
    outputs: BalanceResult


class ReplayBusinessOutcome(StrictModel):
    status: Literal["business_outcome"] = "business_outcome"
    code: Literal["member_not_found"]
    member_id: str
    step: int = Field(ge=1)


LegacyFailureCode = Literal[
    "policy_violation",
    "target_timeout",
    "checkpoint_mismatch",
    "verification_failed",
    "browser_error",
    "unsupported_action",
]


class ReplayFailure(StrictModel):
    status: Literal["failure"] = "failure"
    code: LegacyFailureCode
    step: int | None
    expected: str
    observed: str
    error_type: str


LegacyReplayResult = Annotated[
    ReplaySuccess | ReplayBusinessOutcome | ReplayFailure,
    Field(discriminator="status"),
]


def _build_output_schema_v12() -> dict[str, Any]:
    return _build_output_schema("1.2", LegacyReplayResult)


class _LegacyCapability(StrictModel):
    schema_version: Literal["1.1"] = "1.1"
    name: Literal["get_savings_balance"] = "get_savings_balance"

    source_run_id: str = Field(min_length=1)
    start_path: Literal["/"] = "/"

    input_type: Literal["MemberLookupInputs"] = "MemberLookupInputs"
    output_type: Literal["ReplayResult"] = "ReplayResult"
    verifier: Literal["savings_balance_v1"] = "savings_balance_v1"

    steps: list[CapabilityStep] = Field(min_length=1)


class _CapabilityV12(StrictModel):
    schema_version: Literal["1.2"] = "1.2"
    name: Literal["get_savings_balance"] = "get_savings_balance"

    source_run_id: str = Field(min_length=1)
    start_path: Literal["/"] = "/"

    input_type: Literal["MemberLookupInputs"] = "MemberLookupInputs"
    input_schema: dict[str, Any]
    output_type: Literal["ReplayResult"] = "ReplayResult"
    output_schema: dict[str, Any]
    verifier: Literal["savings_balance_v1"] = "savings_balance_v1"

    steps: list[CapabilityStep] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_contract_schemas(self) -> Self:
        if self.input_schema != _build_input_schema("1.2"):
            raise ValueError(
                "input_schema does not match the version 1.2 contract."
            )

        if self.output_schema != _build_output_schema_v12():
            raise ValueError(
                "output_schema does not match the version 1.2 contract."
            )

        return self


class Capability(StrictModel):
    schema_version: Literal["1.3"] = CAPABILITY_SCHEMA_VERSION
    name: Literal["get_savings_balance"] = "get_savings_balance"

    source_run_id: str = Field(min_length=1)
    start_path: Literal["/"] = "/"

    input_type: Literal["MemberLookupInputs"] = "MemberLookupInputs"
    input_schema: dict[str, Any]
    output_type: Literal["ReplayResult"] = "ReplayResult"
    output_schema: dict[str, Any]
    verifier: Literal["savings_balance_v1"] = "savings_balance_v1"

    steps: list[CapabilityStep] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_contract_schemas(self) -> Self:
        if self.input_schema != build_input_schema():
            raise ValueError(
                "input_schema does not match the supported contract."
            )

        if self.output_schema != build_output_schema():
            raise ValueError(
                "output_schema does not match the supported contract."
            )

        return self


def parse_capability_artifact(data: Any) -> Capability:
    if not isinstance(data, dict):
        raise ValueError("Capability artifact must be a JSON object.")

    required_metadata = {"schema_version", "output_type"}

    if not required_metadata.issubset(data):
        raise ValueError(
            "Artifact is missing required contract metadata."
        )

    schema_version = data.get("schema_version")

    if schema_version == "1.1":
        legacy = _LegacyCapability.model_validate(data)

        return Capability(
            source_run_id=legacy.source_run_id,
            start_path=legacy.start_path,
            input_type=legacy.input_type,
            input_schema=build_input_schema(),
            output_type=legacy.output_type,
            output_schema=build_output_schema(),
            verifier=legacy.verifier,
            steps=legacy.steps,
        )

    if schema_version == "1.2":
        legacy = _CapabilityV12.model_validate(data)

        return Capability(
            source_run_id=legacy.source_run_id,
            start_path=legacy.start_path,
            input_type=legacy.input_type,
            input_schema=build_input_schema(),
            output_type=legacy.output_type,
            output_schema=build_output_schema(),
            verifier=legacy.verifier,
            steps=legacy.steps,
        )

    if schema_version == CAPABILITY_SCHEMA_VERSION:
        return Capability.model_validate(data)

    raise ValueError(
        f"Unsupported capability schema version: {schema_version!r}."
    )
