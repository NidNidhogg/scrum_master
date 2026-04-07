"""
bot/handlers/admin.py – Ingestion Handler.

Latest fixes:
- LEAD RESOLUTION: Uses get_all_users to resolve Lead names too.
- RACE CONDITION: Explicit commit in Repo + Sequential async handling.
- FUZZY DEDUP: Strip [...] and (...) meta.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from html import escape
from io import BytesIO

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.markdown import hbold, hcode, hitalic
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core import agent
from bot.states import AdminStates
from db import repository as repo
from db.models import UserRole

logger = logging.getLogger(__name__)
router = Router()

LEAD_TG_ID: int = int(os.getenv("LEAD_TG_ID", "0"))


def _is_lead(tg_id: int) -> bool:
    return tg_id == LEAD_TG_ID


def _normalize(text: str) -> str:
    """Canonical dedup key: strip brackets/parens, collapse whitespace, lowercase."""
    import re
    stripped = re.sub(r'\[.*?\]|\(.*?\)', '', text)
    return re.sub(r'\s+', ' ', stripped).strip().lower()


# ──────────────────────────────────────────────
# Keyboards
# ──────────────────────────────────────────────

def _build_handshake_kb(contract_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{contract_id}"),
                InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{contract_id}"),
            ]
        ]
    )


def _build_final_review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Send to Team", callback_data="draft_send"),
                InlineKeyboardButton(text="✏️ Edit Drafts", callback_data="draft_edit"),
            ],
            [
                InlineKeyboardButton(text="🗑 Discard", callback_data="draft_discard"),
            ],
        ]
    )


# ──────────────────────────────────────────────
# Message Formatters
# ──────────────────────────────────────────────

def _format_draft_message(developer_name: str, tasks: list, meeting_context: str) -> str:
    task_lines = []
    for i, t in enumerate(tasks):
        desc = escape(t.description)
        est = f" ({escape(t.time_estimate)})" if t.time_estimate else ""
        task_lines.append(f"  {i + 1}. {desc}{est}")
    tasks_text = "\n".join(task_lines)
    ctx = hitalic(f"Context: {escape(meeting_context)}") if meeting_context else ""
    return (
        f"📋 {hbold(f'Daily Plan – {date.today()}')}\n"
        f"Hi {escape(developer_name)}! Here's your plan for today:\n\n"
        f"{tasks_text}\n\n{ctx}\n\n"
        f"Please confirm or edit your tasks:"
    )


def _format_final_review_message(contracts: list) -> str:
    parts = [f"🧐 {hbold(f'Final Review – Today’s Drafts')} ({date.today()})\n"]
    
    sorted_contracts = sorted(
        contracts, 
        key=lambda c: (0 if c.user_id else 1, (c.user.full_name or "") if c.user else (c.developer_alias or ""))
    )

    for c in sorted_contracts:
        if c.user:
            name_str = escape(c.user.full_name or c.user.username or f"User #{c.user.tg_id}")
            header = f"👤 {hbold(name_str)}"
        else:
            name_raw = escape(c.developer_alias or 'Unknown')
            header = f"👤 {hbold(f'{name_raw} (Unresolved)')}"
            
        parts.append(header)
        for i, t in enumerate(c.tasks):
            desc = escape(t.description)
            est = f" ({escape(t.time_estimate or 'no est')})"
            parts.append(f"  {i + 1}. {desc}{est}")
        parts.append("")

    if any(not c.user_id for c in contracts):
        parts.append(f"⚠️ {hitalic('Some users are unresolved. Link them with /alias to send their plans.')}")
    
    parts.append(f"\n{hitalic('Ready to launch the Handshake flow?')}")
    return "\n".join(parts)


def _format_drafts_for_editing(contracts: list) -> str:
    """Formats drafts as clean plain-text for lead copy-edit."""
    parts = ["— Copy and edit the text below —\n"]
    # Sort resolved first
    sorted_c = sorted(contracts, key=lambda x: (0 if x.user_id else 1))
    for c in sorted_c:
        if c.user:
            name = escape(c.user.full_name or c.user.username or f"User #{c.user.tg_id}")
        else:
            name = escape(c.developer_alias or 'Unknown')
            
        parts.append(f"— {name}")
        for t in c.tasks:
            est = f" [{escape(t.time_estimate)}]" if t.time_estimate else ""
            parts.append(f"• {escape(t.description)}{est}")
        parts.append("")
    
    parts.append("\n" + hitalic("Send the updated text back to me.\nTo cancel, send /cancel."))
    return "\n".join(parts)


# ──────────────────────────────────────────────
# Ingestion Logic
# ──────────────────────────────────────────────

async def _process_transcript(
    raw_text: str,
    session: AsyncSession,
    status_message: Message,
) -> None:
    today = date.today()
    print(f"DEBUG: Triggering ruthlessly atomic cleanup for {today}...")
    await repo.discard_daily_plans(session) # Already commits
    print(f"DEBUG: Database wiped and committed for {today}.")

    try:
        parsed = await agent.parse_transcript(raw_text)
    except Exception as exc:
        logger.exception("Transcript parsing failed: %s", exc)
        await status_message.edit_text(f"❌ Gemini parsing error: {escape(str(exc)[:200])}")
        return

    # Load resolution data - fetch ALL USERS to include Lead
    all_aliases = await repo.get_all_aliases(session)
    all_users = await repo.get_all_users(session)

    seen_task_hashes: set[str] = set()

    for task_input in parsed.tasks:
        assignee_raw = task_input.developer_alias
        
        # IDENTITY RESOLUTION: Aliases -> FullName -> Username (ALL roles)
        found_user = None
        for alias_obj in all_aliases:
            if alias_obj.alias.lower() == assignee_raw.lower() or alias_obj.alias.lower() in assignee_raw.lower():
                found_user = alias_obj.user
                break
        if not found_user:
            for u in all_users:
                if assignee_raw.lower() in (u.full_name or "").lower(): found_user = u; break
        if not found_user:
            for u in all_users:
                if assignee_raw.lower() in (u.username or "").lower(): found_user = u; break

        user_id = found_user.id if found_user else None

        # BATCH DEDUP
        dedup_key = f"{user_id or assignee_raw.lower()}_{_normalize(task_input.description)}"
        if dedup_key in seen_task_hashes:
            print(f"DEBUG: Batch dedup skip — '{task_input.description[:50]}'")
            continue
        seen_task_hashes.add(dedup_key)

        contract, _ = await repo.get_or_create_contract(
            session, 
            user_id=user_id, 
            developer_alias=assignee_raw if not user_id else None,
            meeting_context=parsed.meeting_context,
            lead_tg_id=LEAD_TG_ID
        )
        await repo.add_task(
            session,
            contract_id=contract.id,
            description=task_input.description,
            time_estimate=task_input.time_estimate,
        )

    await session.commit()
    contracts = await repo.get_contracts_for_date(session)
    drafts = [c for c in contracts if c.status.value == "draft"]

    if not drafts:
        await status_message.edit_text("⚠️ No draft tasks created.")
        return

    await status_message.edit_text(_format_final_review_message(drafts), parse_mode="HTML", reply_markup=_build_final_review_kb())


# ──────────────────────────────────────────────
# Command Handlers
# ──────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    await _register(message, session)

@router.message(Command("register"))
async def _register(message: Message, session: AsyncSession) -> None:
    user, created = await repo.get_or_create_user(
        session,
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        role=UserRole.lead if _is_lead(message.from_user.id) else UserRole.developer,
    )
    if created: await message.reply(f"✅ Registered as {hbold(user.role.value)}.", parse_mode="HTML")
    else: await message.reply("ℹ️ Already registered.")

@router.message(Command("alias"))
async def cmd_alias(message: Message, session: AsyncSession, command: CommandObject) -> None:
    if not _is_lead(message.from_user.id): return
    if not command.args or len(command.args.split()) < 2:
        await message.reply(f"💡 Usage: {hcode('/alias &lt;name&gt; &lt;tg_id_or_@username&gt;')}", parse_mode="HTML")
        return
    parts = command.args.split()
    alias_name, target_id = parts[0], parts[1]
    target_user = None
    if target_id.isdigit(): target_user = await repo.get_user_by_tg_id(session, int(target_id))
    elif target_id.startswith("@"): target_user = await repo.get_user_by_username(session, target_id.lstrip("@"))
    if not target_user: await message.reply("❌ User not found."); return
    res = await repo.add_alias(session, user_id=target_user.id, alias=alias_name)
    if not res: await message.reply("ℹ️ Alias already exists."); return
    await session.commit(); await message.reply(f"✅ Linked {hcode(alias_name)}."); return

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state(): await state.clear(); await message.reply("✅ Cancelled.")
    else: await message.reply("ℹ️ Nothing to cancel.")

@router.message(Command("daily"), StateFilter(None))
@router.message(F.text, F.chat.type == "private", ~F.text.startswith("/"), StateFilter(None))
async def handle_transcript_text(message: Message, session: AsyncSession, command: CommandObject = None) -> None:
    if not _is_lead(message.from_user.id): return
    raw_text = (command.args if (command and command.args) else message.text) or ""
    if len(raw_text) < 10: return await message.reply("Too short.")
    status_msg = await message.reply("🔍 Parsing...")
    await _process_transcript(raw_text, session, status_msg)

@router.message(F.document, F.chat.type == "private", StateFilter(None))
async def handle_transcript_file(message: Message, session: AsyncSession) -> None:
    if not _is_lead(message.from_user.id): return
    doc = message.document
    if not (doc.file_name.lower().endswith(".txt") or doc.mime_type == "text/plain"): return await message.reply("Upload .txt.")
    status_msg = await message.reply("📄 Downloading...")
    try:
        file_info = await message.bot.get_file(doc.file_id)
        buf = BytesIO()
        await message.bot.download_file(file_info.file_path, destination=buf)
        raw_text = buf.getvalue().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("File download failed")
        return await status_msg.edit_text("❌ File download failed.")
    await status_msg.edit_text("🔍 Parsing..."); await _process_transcript(raw_text, session, status_msg)


# ──────────────────────────────────────────────
# Callbacks
# ──────────────────────────────────────────────

@router.callback_query(F.data == "draft_send")
async def handle_send_to_team(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_lead(callback.from_user.id): return
    contracts = await repo.get_contracts_for_date(session)
    resolved = [c for c in contracts if c.status.value == "draft" and c.user_id is not None]
    if not resolved: return await callback.answer("No resolved plans.", show_alert=True)
    for c in resolved: await repo.activate_contract(session, c.id)
    sent = 0
    for c in resolved:
        if c.user.notification_chat_id:
            msg = _format_draft_message(c.user.full_name or str(c.user.tg_id), c.tasks, c.meeting_context or "")
            try: await callback.bot.send_message(c.user.notification_chat_id, text=msg, parse_mode="HTML", reply_markup=_build_handshake_kb(c.id)); sent += 1
            except Exception: pass
    await session.commit(); await callback.message.edit_text(f"🚀 Sent to {sent}.", parse_mode="HTML"); await callback.answer("Sent!")

@router.callback_query(F.data == "draft_edit")
async def handle_draft_edit(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_lead(callback.from_user.id): return
    contracts = await repo.get_contracts_for_date(session)
    drafts = [c for c in contracts if c.status.value == "draft"]
    if not drafts: return await callback.answer("Empty.")
    await callback.message.answer(_format_drafts_for_editing(drafts), parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_draft_edit); await callback.answer()

@router.message(F.text, StateFilter(AdminStates.waiting_for_draft_edit))
async def handle_draft_edit_sub(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_lead(message.from_user.id): return
    await state.clear(); print(f"DEBUG: Edit submitted..."); 
    status_msg = await message.reply("🔄 Re-parsing your edits...")
    await _process_transcript(message.text, session, status_msg)

@router.callback_query(F.data == "draft_discard")
async def handle_discard_drafts(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_lead(callback.from_user.id): return
    count = await repo.discard_daily_plans(session); await session.commit(); await callback.message.edit_text(f"🗑 Discarded {count}."); await callback.answer()
