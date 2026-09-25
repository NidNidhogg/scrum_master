"""
agent/core.py – Google Gemini API integration.
Fixed: Uses asyncio.to_thread with synchronous generate_content for REST transport stability.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from typing import List, Optional

import google.generativeai as genai
from google.api_core import exceptions
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from agent import prompts
from agent.tools import (
    get_gemini_tools,
    BatchCreateContractsInput,
    UpdateTaskStatusInput,
)
from db.models import DailyContract, Task

load_dotenv()
logger = logging.getLogger(__name__)

# Config
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
MODEL_NAME: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# Initialise Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY, transport="rest")

# ──────────────────────────────────────────
# Error Handling Logic
# ──────────────────────────────────────────
GEMINI_RETRYABLE_EXCEPTIONS = (
    exceptions.ResourceExhausted,
    exceptions.ServiceUnavailable,
    exceptions.DeadlineExceeded,
    exceptions.InternalServerError,
)


class AgentCore:
    """Thin wrapper around Google Gemini's Generative AI SDK."""

    def __init__(self) -> None:
        self.model_name = MODEL_NAME

    def _get_model(self, system_instruction: str, tools: Optional[list] = None) -> genai.GenerativeModel:
        return genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system_instruction,
            tools=tools,
        )

    # ──────────────────────────────────────────
    # Flow 1 – Parse transcript → structured tasks
    # ──────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(GEMINI_RETRYABLE_EXCEPTIONS),
    )
    async def parse_transcript(
        self, raw_transcript: str, existing_backlog: str = ""
    ) -> BatchCreateContractsInput:
        """
        Call Gemini with tool use and extract BatchCreateContractsInput.
        existing_backlog: a pre-formatted string of unfinished tasks from previous days.
        Uses asyncio.to_thread for REST transport stability.
        """
        print("DEBUG: [1] Preparing stateless Gemini request for transcript parsing...")
        try:
            # Re-initialize the model to ensure NO state/history carryover from previous calls.
            # We explicitly do NOT use model.start_chat() to maintain statelessness.
            model = self._get_model(
                system_instruction=prompts.ingestion_system(),
                tools=get_gemini_tools()
            )

            print("DEBUG: [2] Request sent (stateless / fresh instance), waiting...")
            # generate_content is a stateless call. By providing no history 
            # and using a fresh model instance, we satisfy the stateless constraint.
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    model.generate_content,
                    contents=prompts.ingestion_user_prompt(raw_transcript, existing_backlog),
                    tool_config={"function_calling_config": {"mode": "ANY"}}
                ),
                timeout=45.0
            )

            # Check candidates
            if not response.candidates or not response.candidates[0].content.parts:
                 raise ValueError("Gemini returned an empty response.")

            part = response.candidates[0].content.parts[0]
            if not hasattr(part, "function_call") or not part.function_call:
                 print(f"DEBUG: No function call. Text: {response.text[:200]}")
                 raise ValueError("Gemini did not perform a function call.")

            call = part.function_call
            print(f"DEBUG: [3] Gemini called tool: {call.name}")
            
            return BatchCreateContractsInput.model_validate(dict(call.args))

        except asyncio.TimeoutError:
            print("❌ ERROR: Gemini request TIMED OUT.")
            raise
        except Exception as exc:
            print(f"❌ ERROR: Gemini call failed: {exc}")
            traceback.print_exc()
            raise

    # ──────────────────────────────────────────
    # Flow 2 – Rewrite developer's edit input
    # ──────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(GEMINI_RETRYABLE_EXCEPTIONS),
    )
    async def rewrite_task_edit(self, original: str, developer_input: str) -> str:
        """Return the rewritten task description using Gemini."""
        model = self._get_model(system_instruction=prompts.EDIT_SYSTEM)
        response = await asyncio.to_thread(
            model.generate_content, 
            prompts.edit_user_prompt(original, developer_input)
        )
        return response.text.strip()

    # ──────────────────────────────────────────
    # Flow 3a – Generate SLA ping message
    # ──────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(GEMINI_RETRYABLE_EXCEPTIONS),
    )
    async def generate_sla_ping(self, contract: DailyContract, tasks: List[Task]) -> str:
        model = self._get_model(system_instruction=prompts.SLA_PING_SYSTEM)
        response = await asyncio.to_thread(
            model.generate_content,
            prompts.sla_ping_user_prompt(contract, tasks)
        )
        return response.text.strip()

    # ──────────────────────────────────────────
    # Flow 3b – Parse developer's status reply
    # ──────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(GEMINI_RETRYABLE_EXCEPTIONS),
    )
    async def parse_status_update(
        self, task: Task, developer_reply: str
    ) -> UpdateTaskStatusInput:
        """
        Interprets developer status update using Gemini function calling.
        """
        print("DEBUG: [1] Preparing Gemini request for status update...")
        try:
            model = self._get_model(
                system_instruction=prompts.UPDATE_STATUS_SYSTEM,
                tools=get_gemini_tools()
            )
            
            print("DEBUG: [2] Request sent via ThreadPool...")
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    model.generate_content,
                    prompts.update_status_user_prompt(task, developer_reply),
                    tool_config={"function_calling_config": {"mode": "ANY"}}
                ),
                timeout=30.0
            )

            part = response.candidates[0].content.parts[0]
            call = part.function_call
            return UpdateTaskStatusInput.model_validate(dict(call.args))
        except Exception as exc:
            print(f"❌ ERROR: Status parse failed: {exc}")
            traceback.print_exc()
            raise

    # ──────────────────────────────────────────
    # Flow 4 – Evening synthesis
    # ──────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(GEMINI_RETRYABLE_EXCEPTIONS),
    )
    async def generate_evening_summary(self, contracts_data: str) -> str:
        model = self._get_model(system_instruction=prompts.SYNTHESIS_SYSTEM)
        response = await asyncio.to_thread(
            model.generate_content,
            prompts.synthesis_user_prompt(contracts_data)
        )
        return response.text.strip()


# Module-level singleton
agent = AgentCore()
