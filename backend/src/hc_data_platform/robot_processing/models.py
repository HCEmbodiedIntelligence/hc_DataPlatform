from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Draft(Strict):
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=120)
    software: str = Field(min_length=1, max_length=120)
    source_format: Literal["MCAP"] = "MCAP"
    template_id: str | None = None
    configuration: McapProcessingConfig


class Revision(Strict):
    expected_revision: int = Field(ge=1)


class Validation(Revision):
    # Sample locators are scoped and loaded by the server; no client-supplied pass flag.
    project_id: str
    region_code: str
    raw_source_id: str
    source_path: str


class Publish(Revision):
    make_default: bool = True


class Template(Strict):
    template_id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,128}$")
    name: str = Field(min_length=1, max_length=120)
    model_version_id: str = Field(min_length=1, max_length=128)
    software: str = Field(min_length=1, max_length=120)
    configuration: McapProcessingConfig


class ConfigReference(Strict):
    scheme_id: str
    version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
