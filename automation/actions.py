from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_serialization_defaults_required=True,
    )


class FillAction(StrictModel):
    kind: Literal["fill"]
    label: str = Field(min_length=1)
    value: str


class ClickAction(StrictModel):
    kind: Literal["click"]
    role: Literal["button", "link"]
    name: str = Field(min_length=1)


class FinishAction(StrictModel):
    kind: Literal["finish"]
    summary: str = Field(min_length=1)


class NextAction(StrictModel):
    action: FillAction | ClickAction | FinishAction

class LinkClickAction(StrictModel):
    kind: Literal["click_link"]
    href: str = Field(min_length=1)


BrowserAction = FillAction | ClickAction | LinkClickAction
