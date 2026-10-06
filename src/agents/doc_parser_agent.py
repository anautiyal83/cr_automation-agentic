"""Step 1: Document parsing — produces unified intermediate representation."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.core.models import StepOutput, RetryContext
from src.agents import call_llm, get_model_for_step

logger = logging.getLogger(__name__)


class DocParserAgent:
    step_number = 1
    step_name = "Document Parser"

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput:
        from src.parsers.ciq_parser import parse_ciq, detect_field_types
        from src.parsers.mop_parser import parse_mop
        from src.parsers.constraints_parser import parse_constraints
        from src.parsers.command_output_parser import parse_command_outputs
        from src.core.intermediate_repr import (
            IntermediateRepresentation,
            UCMetadata,
            CIQData,
            MopData,
            MopStep,
            ConstraintsData,
            CommandOutputsData,
            CommandEntry,
            FieldTypeInfo,
            FieldConstraint,
            BusinessRule,
            CommandType,
            MopType,
        )

        doc_set = input_data.get("document_set", {})
        hil_requests: list[dict] = []

        # Parse CIQ
        ciq_result = parse_ciq(
            doc_set.get("ciq_file_path", ""),
            selected_sheets=doc_set.get("selected_sheets"),
            mandatory_columns=input_data.get("mandatory_columns"),
        )
        field_types = detect_field_types(ciq_result["headers"], ciq_result["rows"])

        # Parse MOP
        mop_result = parse_mop(doc_set.get("mop_file_path", ""))
        for section in mop_result.get("hil_needed_sections", []):
            hil_requests.append({
                "field_or_section": f"MOP Step {section['step_number']}",
                "agent_inference": section.get("description", ""),
                "question": section.get("reason", "Cannot extract command"),
            })

        # Parse constraints
        constraints_result: dict[str, Any] = {
            "field_constraints": [],
            "business_rules": [],
            "exclusions": {"sheets": [], "columns": []},
        }
        constraints_path = doc_set.get("constraints_file_path")
        if constraints_path and Path(constraints_path).exists():
            constraints_result = parse_constraints(constraints_path)
            for rule in constraints_result.get("hil_needed", []):
                hil_requests.append({
                    "field_or_section": f"Constraint: {rule['description'][:50]}",
                    "agent_inference": rule.get("extracted_rule", ""),
                    "question": f"Please clarify this business rule: {rule['description']}",
                })

        # Parse command outputs
        cmd_result = parse_command_outputs(doc_set.get("command_outputs_path", ""))

        # Build intermediate representation
        ir = IntermediateRepresentation(
            uc_metadata=UCMetadata(
                name=doc_set.get("uc_name", ""),
                source_documents={
                    "ciq_file": doc_set.get("ciq_file_path"),
                    "mop_file": doc_set.get("mop_file_path"),
                    "constraints_file": constraints_path,
                    "command_outputs_file": doc_set.get("command_outputs_path"),
                },
            ),
            ciq_data=CIQData(
                headers=ciq_result["headers"],
                rows=ciq_result["rows"],
                field_types={
                    h: FieldTypeInfo(type=field_types.get(h, "string"))
                    for h in ciq_result["headers"]
                },
            ),
            mop_data=MopData(
                mop_type=MopType(mop_result.get("mop_type", "cli")),
                steps=[
                    MopStep(
                        step_number=s["step_number"],
                        description=s.get("description", ""),
                        command_type=CommandType(s.get("command_type", "cli_command")),
                        command_template=s.get("command_template", ""),
                        expected_output_pattern=s.get("expected_output_pattern", ""),
                    )
                    for s in mop_result.get("steps", [])
                ],
            ),
            constraints=ConstraintsData(
                field_constraints=[
                    FieldConstraint(
                        field=c["field"],
                        rule_type=c.get("rule_type", "required"),
                        parameters=c.get("parameters", {}),
                    )
                    for c in constraints_result.get("field_constraints", [])
                ],
                business_rules=[
                    BusinessRule(
                        description=r["description"],
                        extracted_rule=r.get("extracted_rule"),
                        hil_required=r.get("hil_required", False),
                    )
                    for r in constraints_result.get("business_rules", [])
                ],
            ),
            command_outputs=CommandOutputsData(
                commands=[
                    CommandEntry(
                        command=c["command"],
                        expected_response=c.get("expected_response", ""),
                        response_pattern=c.get("response_pattern", ""),
                    )
                    for c in cmd_result.get("commands", [])
                ],
            ),
        )

        status = "needs_hil" if hil_requests else "passed"
        return StepOutput(
            status=status,
            output_data=json.loads(ir.model_dump_json()),
            hil_requests=hil_requests if hil_requests else None,
        )
