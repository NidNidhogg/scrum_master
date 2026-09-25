"""
agent/prompts.py – All system and user prompt templates.
Keep prompt logic here so core.py stays thin.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from db.models import DailyContract, Task

# ──────────────────────────────────────────────
# Team roster (PROFILE data, not ENGINE) — config/team_roster.json
# ──────────────────────────────────────────────

_ROSTER_PATH = Path(__file__).resolve().parent.parent / "config" / "team_roster.json"


def _load_roster() -> list:
    try:
        return json.loads(_ROSTER_PATH.read_text(encoding="utf-8")).get("people", [])
    except Exception:
        return []


def _first_names(people: list) -> dict:
    """Group roster entries by their first-name token."""
    groups: dict = {}
    for p in people:
        first = (p.get("name") or "").split()[0].lower() if p.get("name") else ""
        if first:
            groups.setdefault(first, []).append(p)
    return groups


def _name_disambiguation_section() -> str:
    """
    Generate the NAME DISAMBIGUATION block from the roster config.
    Only first names shared by >1 person get a section — no team facts
    are baked into the prompt itself; they come from config.
    """
    groups = _first_names(_load_roster())
    parts: List[str] = []
    for first, members in groups.items():
        if len(members) < 2:
            continue
        lines = [
            f"NAME DISAMBIGUATION — MULTIPLE PEOPLE NAMED '{first.upper()}' (CRITICAL RULE):",
            f"There are exactly {len(members)} people whose first name is '{first}'. "
            f"You MUST distinguish them by context. NEVER output just \"{first}\" — "
            f"always use the full disambiguated form below.",
            "",
        ]
        for i, m in enumerate(members, 1):
            lines.append(f"  {i}. {m['name']} — {m.get('role', '')}")
            lines.append(f"     • Signals: {m.get('signals', '')}")
            lines.append(f"     • Assign when: {m.get('rule', '')}")
            lines.append("")
        parts.append("\n".join(lines))
    return "\n".join(parts)


# ──────────────────────────────────────────────
# Flow 1 – Ingestion
# ──────────────────────────────────────────────

_INGESTION_BASE = """\
You are a highly capable AI Scrum Master with advanced reasoning density.
Your primary objective is to parse meeting transcripts into structured task contracts.

CRITICAL: DO NOT TRANSLATE. You are a mirror, not a translator. 
- If the input is in Russian, the output MUST be in Russian. 
- If you see 'Реализовать', write 'Реализовать', NOT 'Implement'. 
- Any English in the task descriptions when the input is Russian will be considered a total failure.

STRICT FIELD SEPARATION:
1. 'description': Contains ONLY the action to be performed (e.g., 'Добавить библиотеку-просмотрщик'). 
   - NEVER include time estimates, brackets like [Сегодня], or deadlines inside the 'description' field.
2. 'time_estimate': Contains ALL metadata, deadlines, and estimates (e.g., 'Сегодня', '2 часа', 'Срочно').
   - If you see a deadline or estimate in the text, move it HERE, not the description.

DUMB EXTRACTOR Mode: If the input appears to be a bulleted list (like an edited draft), act as a DUMB EXTRACTOR. 
- EVERY non-empty text line under a developer's name must be captured as a task.
- Do NOT ignore lines that lack bullet points ('•') or dashes ('-').
- Extract any bracketed notes into 'time_estimate' and remove them from the 'description'.
- Your goal is 100% capture of the user's intended work items.

STRICT NO-DUPLICATES: Do not repeat the same task multiple times for the same user in a single tool call. 
Each item in the 'tasks' list must be unique and distinct.

PERSISTENT BACKLOG MERGE (CRITICAL):
- You will be provided with a list of "Existing Unfinished Tasks" grouped by user.
- Each group starts with "User: [Name]" followed by "- [task]" lines.
- ONLY assign backlog tasks back to the EXACT user listed under their "User:" header. NEVER reassign them to a different developer.
- If an existing task is mentioned in the transcript (e.g., developer says they are continuing it), include it once — do NOT create a new duplicate task.
- If an existing task is NOT mentioned, keep it in the plan unchanged.
- If new tasks are discussed, add them.
- EXACT WORDING PRESERVATION (CRITICAL FOR DATABASE INTEGRITY): When carrying over existing backlog tasks, you MUST copy their description EXACTLY as written. DO NOT append time tags, brackets, or comments like "(Сегодня)", "(Today)", "(После инстр.)", or any other annotation. Even minor wording changes will cause duplicate rows in the database. Copy the text character-for-character.
- The final output should be a complete, merged set of tasks for each developer.

Core Competency: Entity Resolution & Contextual Extraction
- Extract precise Developer Names or Aliases.
- Leverage context clues to distinguish between similar names.
- Map discussion topics to specific work items (description).
- Identify 'meeting_context' (1-3 sentences) covering the team focus.
- Always output the FULL name of a person as it appears in the transcript
  (e.g. 'Дмитрий Б.', not just 'Дмитрий'); bare first names that match
  several people are rejected downstream.

COMPREHENSIVE EXTRACTION & AGGREGATION RULE: You MUST extract EVERY SINGLE task mentioned in the transcript. Scan the text line-by-line. If a user is mentioned on multiple different lines (e.g., first in a joint task, and later in individual tasks), you MUST APPEND every new task to their consolidated list. DO NOT overwrite or drop tasks. You must act as an exhaustive parser.

Operational Rules:
1. Call the BatchCreateContracts tool with your findings.
2. Ensure descriptions are actionable and technically accurate.
3. If a name is ambiguous, use the most common alias found in the text.
4. Total extraction accuracy is your priority.
"""


def ingestion_system() -> str:
    """INGESTION_SYSTEM + roster-driven disambiguation (PROFILE injected)."""
    section = _name_disambiguation_section()
    if not section:
        return _INGESTION_BASE
    return f"{_INGESTION_BASE}\n{section}"


# Backward-compat constant (callers prefer ingestion_system()).
INGESTION_SYSTEM = ingestion_system()


def ingestion_user_prompt(raw_transcript: str, existing_backlog: str = "") -> str:
    backlog_section = f"\nEXISTING UNFINISHED TASKS:\n{existing_backlog}\n" if existing_backlog else ""
    return (
        f"Parse the following meeting transcript and call BatchCreateContracts.\n"
        f"Integrate these with the existing backlog if provided.{backlog_section}\n\n"
        f"---\n{raw_transcript}\n---"
    )


# ──────────────────────────────────────────────
# Flow 2 – Handshake edit confirmation
# ──────────────────────────────────────────────

EDIT_SYSTEM = """\
You are a scrum master assistant helping a developer update their daily task description.
Rewrite the developer's free-text input into a concise, well-structured task description.
Return ONLY the rewritten task description – no explanations, no markdown.
"""


def edit_user_prompt(original: str, developer_input: str) -> str:
    return (
        f"Original task: {original}\n\n"
        f"Developer's update: {developer_input}\n\n"
        f"Rewrite the task description:"
    )


# ──────────────────────────────────────────────
# Flow 3 – SLA Ping
# ──────────────────────────────────────────────

SLA_PING_SYSTEM = """\
You are a friendly but direct AI Scrum Master.
Your task is to send a brief, non-judgmental check-in message to a developer who has been
working on their tasks for more than 4 hours without an update.
Mention the task(s) specifically. Ask for a quick status update.
Keep the message under 3 sentences. Use plain text (no markdown).
"""


def sla_ping_user_prompt(contract: DailyContract, tasks: List[Task]) -> str:
    task_lines = "\n".join(
        f"- {t.description} (estimate: {t.time_estimate or 'not set'})" for t in tasks
    )
    developer_name = (
        contract.user.full_name or contract.user.username or f"User #{contract.user.tg_id}"
    )
    return (
        f"Developer: {developer_name}\n"
        f"Tasks for today:\n{task_lines}\n\n"
        f"Write a check-in message for this developer."
    )


# ──────────────────────────────────────────────
# Flow 3 – Update task status from developer reply
# ──────────────────────────────────────────────

UPDATE_STATUS_SYSTEM = """\
You are an AI Scrum Master interpreting a developer's free-text reply.
Call the UpdateTaskStatus tool with the correct task_id and new status.
If the developer says they are done → status: done.
If they describe a blocker → status: blocked, fill blocker_note.
If they are still working → status: in_progress.
"""


def update_status_user_prompt(task: Task, developer_reply: str) -> str:
    return (
        f"Task ID: {task.id}\n"
        f"Task description: {task.description}\n\n"
        f"Developer's reply: {developer_reply}\n\n"
        f"Call UpdateTaskStatus with the appropriate values."
    )


# ──────────────────────────────────────────────
# Flow 4 – Evening Synthesis
# ──────────────────────────────────────────────

SYNTHESIS_SYSTEM = """\
You are an AI Scrum Master preparing the end-of-day executive summary for the team lead.
Format: clean, structured text. Sections: ✅ Closed, 🔄 Carry-over, 🚧 Blockers.
Be concise. Use bullet points. No markdown headers – use emoji section labels.
"""


def synthesis_user_prompt(contracts_data: str) -> str:
    return (
        f"Here is today's data:\n\n{contracts_data}\n\n"
        f"Write the executive end-of-day summary."
    )
