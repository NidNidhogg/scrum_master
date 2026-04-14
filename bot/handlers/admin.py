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
from aiogram.exceptions import TelegramBadRequest
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

def _build_handshake_kb(contract_id: int, task_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Builds Approve/Edit buttons. 
    Uses 't:ID' for specific tasks (manual) or 'c:ID' for full contracts (daily).
    """
    suffix = f"t:{task_id}" if task_id else f"c:{contract_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{suffix}"),
                InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{suffix}"),
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

async def _resolve_user_by_name(
    session: AsyncSession,
    name_query: str,
) -> Optional[repo.User]:
    """
    Smart Match helper: Aliases -> FullName -> Username.
    """
    import re as _re
    # Clean query: strip whitespace and trailing punctuation (:, .)
    clean_query = _re.sub(r'[\s:.,]+$', '', name_query.strip())
    query_key = clean_query.lower()
    
    # Load all for matching
    all_aliases = await repo.get_all_aliases(session)
    all_users = await repo.get_all_users(session)
    
    # Pass 1: exact alias match
    for alias_obj in all_aliases:
        db_alias = alias_obj.alias.strip().lower()
        if db_alias == query_key or db_alias in query_key or query_key in db_alias:
            print(f"DEBUG: SmartMatch alias '{alias_obj.alias}' -> user {alias_obj.user.tg_id}")
            return alias_obj.user

    # Pass 2: full_name contains
    for u in all_users:
        if query_key in (u.full_name or "").strip().lower():
            print(f"DEBUG: SmartMatch full_name '{u.full_name}' for '{clean_query}'")
            return u

    # Pass 3: username contains
    for u in all_users:
        if query_key in (u.username or "").strip().lower():
            print(f"DEBUG: SmartMatch username '{u.username}' for '{clean_query}'")
            return u
            
    return None


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

    # FIX 3 - RE-LOAD SESSION: expire any cached objects so /alias updates
    #          added AFTER the session was opened are visible here.
    session.expire_all()

    seen_task_hashes: set[str] = set()

    for task_input in parsed.tasks:
        found_user = await _resolve_user_by_name(session, task_input.developer_alias)
        
        user_id = found_user.id if found_user else None
        assignee_key = (found_user.tg_id if found_user else task_input.developer_alias).lower() if not found_user else str(found_user.id)

        # BATCH DEDUP
        dedup_key = f"{assignee_key}_{_normalize(task_input.description)}"
        if dedup_key in seen_task_hashes:
            print(f"DEBUG: Batch dedup skip - '{task_input.description[:50]}'")
            continue
        seen_task_hashes.add(dedup_key)

        contract, _ = await repo.get_or_create_contract(
            session,
            user_id=user_id,
            developer_alias=task_input.developer_alias if not user_id else None,
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
    
    try:
        # Ensure session is present
        if session is None:
            await message.reply("❌ Error: Database session is missing.")
            return

        # 🔄 Refresh identity map (synchronous call, no await)
        session.expire_all()
        
        import shlex
        try:
            # shlex.split properly handles quoted strings: /alias "Dmitry B." 123456
            all_parts = shlex.split(message.text or "")
            # all_parts[0] is typically '/alias', remaining are arguments
            args = all_parts[1:]
        except ValueError:
            # Handle cases like unbalanced quotes
            args = (command.args or "").split()

        if len(args) < 2:
            await message.reply(f"💡 Usage: {hcode('/alias \"Name With Spaces\" &lt;tg_id_or_@username&gt;')}", parse_mode="HTML")
            return
            
        alias_name = args[0]
        target_id = args[1].strip().strip("'\"")
        
        print(f"DEBUG: cmd_alias - Searching for user with ID/Username: '{target_id}'")
        
        target_user = None
        if target_id.isdigit(): 
            tg_id = int(target_id)
            target_user = await repo.get_user_by_tg_id(session, tg_id)
            if not target_user:
                print(f"DEBUG: cmd_alias - User with tg_id {tg_id} NOT found in DB.")
        elif target_id.startswith("@"):
            username = target_id.lstrip("@")
            target_user = await repo.get_user_by_username(session, username)
            if not target_user:
                print(f"DEBUG: cmd_alias - User with username '@{username}' NOT found in DB.")
                
        if not target_user: 
            await message.reply(f"❌ User not found (searched: {hcode(target_id)}).") 
            return
            
        res_obj, created = await repo.upsert_alias(session, user_id=target_user.id, alias=alias_name)
        
        target_name = target_user.full_name or target_user.username or str(target_user.tg_id)
        
        await session.commit()
        
        if created:
            msg = f"✅ Linked {hcode(alias_name)} to {hbold(target_name)}."
        else:
            msg = f"🔄 Updated: alias {hcode(alias_name)} now points to {hbold(target_name)}."
            
        await message.reply(msg, parse_mode="HTML")
    except Exception as e:
        logger.exception("Error in cmd_alias")
        await message.reply(f"❌ An error occurred: {escape(str(e))}")
    return

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state(): await state.clear(); await message.reply("✅ Cancelled.")
    else: await message.reply("ℹ️ Nothing to cancel.")

@router.message(Command("chatid", "id"))
async def cmd_chatid(message: Message) -> None:
    """Utility: reply with the current chat's numeric ID. Works in DMs and groups."""
    await message.reply(f"This chat's ID is: <code>{message.chat.id}</code>", parse_mode="HTML")


@router.message(Command("todo"))
async def cmd_todo(message: Message, session: AsyncSession) -> None:
    """Manual task assignment: /todo \"Name\" Task description"""
    if not _is_lead(message.from_user.id):
        return

    import shlex
    try:
        parts = shlex.split(message.text or "")
        # parts[0] is '/todo', parts[1] is name, parts[2:] is task
        if len(parts) < 3:
            raise ValueError
        assignee_name = parts[1]
        task_desc = " ".join(parts[2:])
    except (ValueError, IndexError):
        await message.reply(f"💡 Usage: {hcode('/todo \"Name\" Task description')}", parse_mode="HTML")
        return

    session.expire_all()
    target_user = await _resolve_user_by_name(session, assignee_name)

    if not target_user:
        await message.reply(f"❌ User not found: {hcode(assignee_name)}", parse_mode="HTML")
        return

    # Create / get contract for today
    contract, _ = await repo.get_or_create_contract(
        session,
        user_id=target_user.id,
        lead_tg_id=LEAD_TG_ID
    )
    
    task = await repo.add_task(
        session,
        contract_id=contract.id,
        description=task_desc
    )
    
    if not task:
        await message.reply("ℹ️ Task already exists for this user today.")
        return

    await session.commit()
    
    # ── Handshake Flow ─────────────────────────
    if target_user.notification_chat_id:
        msg = _format_draft_message(
            target_user.full_name or str(target_user.tg_id), 
            [task], 
            "Manual task assignment"
        )
        try:
            await message.bot.send_message(
                target_user.notification_chat_id, 
                text=msg, 
                parse_mode="HTML", 
                reply_markup=_build_handshake_kb(contract.id, task_id=task.id)
            )
            await message.reply(f"🚀 Task sent to {hbold(target_user.full_name or assignee_name)}.", parse_mode="HTML")
        except Exception:
            logger.exception("Failed to send manual task handshake")
            await message.reply("❌ Task created but failed to send DM to developer.")
    else:
        await message.reply("✅ Task created in DB, but user has no notification chat ID.")

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
    if not resolved: 
        try:
            return await callback.answer("No resolved plans.", show_alert=True)
        except TelegramBadRequest:
            return
    sent = 0
    for c in resolved:
        if c.user.notification_chat_id:
            msg = _format_draft_message(c.user.full_name or str(c.user.tg_id), c.tasks, c.meeting_context or "")
            try: 
                await callback.bot.send_message(c.user.notification_chat_id, text=msg, parse_mode="HTML", reply_markup=_build_handshake_kb(c.id))
                sent += 1
            except Exception: 
                pass
    await session.commit()
    
    try:
        await callback.message.edit_text(f"🚀 Sent to {sent}.", parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            raise
            
    try:
        await callback.answer("Sent!")
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "draft_edit")
async def handle_draft_edit(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_lead(callback.from_user.id): return
    contracts = await repo.get_contracts_for_date(session)
    drafts = [c for c in contracts if c.status.value == "draft"]
    if not drafts: 
        try:
            return await callback.answer("Empty.")
        except TelegramBadRequest:
            return
    await callback.message.answer(_format_drafts_for_editing(drafts), parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_draft_edit)
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

@router.message(F.text, StateFilter(AdminStates.waiting_for_draft_edit))
async def handle_draft_edit_sub(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_lead(message.from_user.id): return
    await state.clear(); print(f"DEBUG: Edit submitted..."); 
    status_msg = await message.reply("🔄 Re-parsing your edits...")
    await _process_transcript(message.text, session, status_msg)

@router.callback_query(F.data == "draft_discard")
async def handle_discard_drafts(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_lead(callback.from_user.id): return
    count = await repo.discard_daily_plans(session)
    await session.commit()
    
    try:
        await callback.message.edit_text(f"🗑 Discarded {count}.")
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            raise
            
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass
