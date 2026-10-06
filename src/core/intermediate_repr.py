from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional


class CommandType(str, Enum):
    CLI_COMMAND = "cli_command"
    REST_API_CALL = "rest_api_call"


class MopType(str, Enum):
    CLI = "cli"
    REST = "rest"
    MIXED = "mixed"


class RuleType(str, Enum):
    REQUIRED = "required"
    REGEX = "regex"
    RANGE = "range"
    ENUM = "enum"
    CROSS_FIELD = "cross_field"


class FieldTypeInfo(BaseModel):
    type: str  # string, integer, float, boolean, email, ip_address, etc.
    validation: str = ""  # auto-inferred or HIL-provided validation rule
    hil_confirmed: bool = False


class UCMetadata(BaseModel):
    name: str
    description: str = ""
    uc_type: str = ""
    source_documents: dict[str, str | None] = Field(default_factory=dict)
    # keys: ciq_file, mop_file, constraints_file, command_outputs_file


class CIQData(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    field_types: dict[str, FieldTypeInfo] = Field(default_factory=dict)


class MopVariable(BaseModel):
    name: str
    source_field: str  # CIQ header name


class MopStep(BaseModel):
    step_number: int
    description: str = ""
    command_type: CommandType
    command_template: str  # With {variable} placeholders
    expected_output_pattern: str = ""
    variables: list[MopVariable] = Field(default_factory=list)
    pre_checks: list[str] = Field(default_factory=list)
    post_checks: list[str] = Field(default_factory=list)


class MopData(BaseModel):
    mop_type: MopType = MopType.CLI
    steps: list[MopStep] = Field(default_factory=list)


class FieldConstraint(BaseModel):
    field: str
    rule_type: RuleType
    parameters: dict = Field(default_factory=dict)


class BusinessRule(BaseModel):
    description: str
    extracted_rule: str | None = None
    hil_required: bool = False


class ConstraintsData(BaseModel):
    field_constraints: list[FieldConstraint] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)


class CommandEntry(BaseModel):
    command: str
    expected_response: str = ""
    response_pattern: str = ""  # Regex or template
    variables_in_response: list[str] = Field(default_factory=list)


class CommandOutputsData(BaseModel):
    commands: list[CommandEntry] = Field(default_factory=list)


class MockEntry(BaseModel):
    command_pattern: str
    response_template: str
    variables: dict = Field(default_factory=dict)


class SimulatorConfig(BaseModel):
    mock_entries: list[MockEntry] = Field(default_factory=list)


class IntermediateRepresentation(BaseModel):
    """Unified intermediate representation consumed by all pipeline agent steps."""
    uc_metadata: UCMetadata
    ciq_data: CIQData = Field(default_factory=CIQData)
    mop_data: MopData = Field(default_factory=MopData)
    constraints: ConstraintsData = Field(default_factory=ConstraintsData)
    command_outputs: CommandOutputsData = Field(default_factory=CommandOutputsData)
    simulator_config: SimulatorConfig = Field(default_factory=SimulatorConfig)
