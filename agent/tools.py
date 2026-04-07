"""
agent/tools.py – Pydantic v2 schemas and Gemini-compatible Tool definitions.
Fixed: Ultra-strict JSON Schema with correctly nested properties.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Tool: BatchCreateContracts  (Flow 1 – Ingestion)
# ──────────────────────────────────────────────

class TaskInput(BaseModel):
    developer_alias: str = Field(
        ..., description="Name or alias of the developer."
    )
    description: str = Field(..., description="Task description extracted from the transcript.")
    time_estimate: Optional[str] = Field(
        None, description="Estimated duration, e.g. '2h', '30 min'. Null if not mentioned."
    )


class BatchCreateContractsInput(BaseModel):
    """
    Structured data extracted from a raw meeting transcript.
    """
    meeting_context: str = Field(
        ..., description="1-3 sentence summary of the overall meeting/discussion context."
    )
    tasks: List[TaskInput] = Field(
        ..., description="List of individual tasks with their owner aliases."
    )


# ──────────────────────────────────────────────
# Tool: UpdateTaskStatus  (Flow 3 – SLA response)
# ──────────────────────────────────────────────

class UpdateTaskStatusInput(BaseModel):
    """
    Structured data after processing a developer's free-text status update.
    """
    task_id: int = Field(..., description="Primary key of the Task record to update.")
    new_status: str = Field(
        ...,
        description="New status: 'done', 'in_progress', or 'blocked'.",
    )
    blocker_note: Optional[str] = Field(
        None,
        description="Short description of the blocker. Required when new_status='blocked'.",
    )


# ──────────────────────────────────────────────
# Gemini Schema Helper – Ultra-Strict
# ──────────────────────────────────────────────

def get_gemini_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """
    Converts a Pydantic model to a JSON schema compatible with strict Gemini versions.
    Inlines all definitions and removes $defs, anyOf, oneOf, allOf, title, and default.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", schema.pop("definitions", {}))
    
    ALLOWED_KEYS = {"type", "properties", "items", "required", "description"}

    def visitor(obj: Any) -> Any:
        if isinstance(obj, list):
            return [visitor(item) for item in obj]
        
        if not isinstance(obj, dict):
            return obj

        # [1] Inlining $ref
        if "$ref" in obj:
            ref_name = obj["$ref"].split("/")[-1]
            return visitor(defs.get(ref_name, {}))

        # [2] Handle Union-like anyOf/oneOf/allOf
        for key in ["anyOf", "oneOf", "allOf"]:
            if key in obj:
                options = obj[key]
                # Filter out null (Optional[T])
                primary = [o for o in options if o.get("type") != "null"]
                if primary:
                    return visitor(primary[0])
                return visitor(options[0])

        # [3] Keep ONLY allowed keys in the schema object itself
        new_obj = {}
        for k, v in obj.items():
            if k == "properties" and isinstance(v, dict):
                # When visiting 'properties', we don't whitelist the keys inside (property names)
                new_obj["properties"] = {pk: visitor(pv) for pk, pv in v.items()}
            elif k in ALLOWED_KEYS:
                new_obj[k] = visitor(v)
            
        return new_obj

    return visitor(schema)


def get_gemini_tools() -> list:
    """Returns a list of tools including function declarations for Gemini."""
    return [
        {
            "function_declarations": [
                {
                    "name": "BatchCreateContracts",
                    "description": (
                        "Extract meeting context and per-developer task lists from a raw transcript."
                    ),
                    "parameters": get_gemini_schema(BatchCreateContractsInput),
                },
                {
                    "name": "UpdateTaskStatus",
                    "description": (
                        "Update the status of a specific task based on the developer's free-text reply."
                    ),
                    "parameters": get_gemini_schema(UpdateTaskStatusInput),
                },
            ]
        }
    ]
