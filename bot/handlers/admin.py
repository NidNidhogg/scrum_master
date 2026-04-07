"""
bot/handlers/admin.py – Flow 1: Lead uploads a meeting transcript.
Switching from Markdown to HTML for robust message formatting.
"""
from __future__ import annotations

import logging
import os
import traceback
from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.markdown import hbold, hcode, hitalic
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core import agent
from db import repository as repo
from db.models import UserRole

logger = logging.getLogger(__name__)
router = Router()

LEAD_TG_ID: int = int(os.getenv("LEAD_TG_ID", "0"))


def _is_lead(tg_id: int) -> bool:
    return tg_id == LEAD_TG_ID


# ──────────────────────────────────────────────
# Keyboards & Formatting
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
                InlineKeyboardButton(text="🚀 Send to Team", callback_data="bulk_send"),
                InlineKeyboardButton(text="🗑️ Discard", callback_data="bulk_discard"),
            ]
        ]
    )


def _format_draft_message(developer_name: str, tasks: list, meeting_context: str) -> str:
    task_lines = []
    for i, t in enumerate(tasks):
        desc = escape(t.description)
        est = f" ({escape(t.time_estimate)})" if t.time_estimate else ""
        task_lines.append(f"  {i+1}. {desc}{est}")
    
    tasks_text = "\n".join(task_lines)
    ctx_text = hitalic(f"Context: {escape(meeting_context)}") if meeting_context else ""
    
    return (
        f"📋 {hbold(f'Daily Plan – {date.today()}')}\n"
        f"Hi {escape(developer_name)}! Here's your plan for today:\n\n"
        f"{tasks_text}\n\n"
        f"{ctx_text}\n\n"
        f"Please confirm or edit your tasks:"
    )


def _format_final_review_message(contracts: list, skipped: list[str]) -> str:
    parts = [f"🧐 {hbold(f'Final Review – Today’s Drafts')} ({date.today()})\n"]
    
    for c in contracts:
        name = escape(c.user.full_name or c.user.username or f"User #{c.user.tg_id}")
        parts.append(f"👤 {hbold(name)}")
        for i, t in enumerate(c.tasks):
            desc = escape(t.description)
            est = f" ({escape(t.time_estimate or 'no est')})"
            parts.append(f"  {i+1}. {desc}{est}")
        parts.append("")
    
    if skipped:
        parts.append(f"⚠️ {hbold('Unresolved Aliases (No Contract):')}")
        for s in set(skipped):
            parts.append(f"  • {escape(s)}")
        parts.append(f"\n{hitalic('Tip: Use /alias <nickname> <tg_id> to fix resolution.')}")
    
    parts.append(f"\n{hitalic('Ready to launch the Handshake flow?')}")
    return "\n".join(parts)


# ──────────────────────────────────────────────
# Flow 0 – Start & Registration
# ──────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    print(f"DEBUG: Received a /start from: {message.from_user.id}")
    await register_user(message, session)


@router.message(Command("register"))
async def register_user(message: Message, session: AsyncSession) -> None:
    user, created = await repo.get_or_create_user(
        session,
        tg_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        role=UserRole.lead if _is_lead(message.from_user.id) else UserRole.developer,
    )
    if created:
        await message.reply(f"✅ Registered as {hbold(user.role.value)}.", parse_mode="HTML")
    else:
        await message.reply("ℹ️ You are already registered.")


@router.message(Command("alias"))
async def cmd_alias(
    message: Message, 
    session: AsyncSession, 
    command: CommandObject
) -> None:
    if not _is_lead(message.from_user.id):
        return
    if not command.args:
        await message.reply(f"💡 Usage: {hcode('/alias <name> <numeric_id_or_@username>')}", parse_mode="HTML")
        return
    parts = command.args.split()
    if len(parts) < 2:
        await message.reply(f"❌ Usage: {hcode('/alias <name> <numeric_id>')}", parse_mode="HTML")
        return
    alias_name = parts[0]
    target_identifier = parts[1]
    print(f"DEBUG: Name={alias_name}, ID_str={target_identifier}")
    target_user = None
    if target_identifier.isdigit():
        target_user = await repo.get_user_by_tg_id(session, int(target_identifier))
    elif target_identifier.startswith("@"):
        username = target_identifier.lstrip("@")
        target_user = await repo.get_user_by_username(session, username)
    if not target_user:
        await message.reply(f"❌ User '{escape(target_identifier)}' not found in DB.", parse_mode="HTML")
        return
    await repo.add_alias(session, user_id=target_user.id, alias=alias_name)
    await session.commit()
    await message.reply(f"✅ Linked {hcode(alias_name)} to user {target_user.tg_id}.", parse_mode="HTML")


# ──────────────────────────────────────────────
# Flow 1 – Ingestion & Smart Parsing (HTML Format)
# ──────────────────────────────────────────────

@router.message(Command("daily"))
@router.message(F.text, F.chat.type == "private", ~F.text.startswith("/"))
async def handle_transcript(
    message: Message, 
    session: AsyncSession, 
    command: CommandObject = None
) -> None:
    if not _is_lead(message.from_user.id):
        return

    raw_text = command.args if (command and command.args) else (message.text or "")
    if len(raw_text) < 10:
        await message.reply("Please provide a transcript (min 10 chars).")
        return

    print(f"DEBUG: Gemini parsing started for: {raw_text[:40]}...")
    parse_msg = await message.reply("🔍 Parsing transcript with Gemini 3.0 Flash…")

    try:
        parsed = await agent.parse_transcript(raw_text)
    except Exception as exc:
        logger.exception("Transcript parsing failed")
        await parse_msg.edit_text(f"❌ Parsing error: {escape(str(exc))}", parse_mode="HTML")
        return

    all_aliases = await repo.get_all_aliases(session)
    skipped_aliases: list[str] = []

    for task_input in parsed.tasks:
        assignee_raw = task_input.developer_alias
        found_user = None
        matched_alias_name = None
        for alias_obj in all_aliases:
            if alias_obj.alias.lower() in assignee_raw.lower():
                found_user = alias_obj.user
                matched_alias_name = alias_obj.alias
                break
        
        if found_user:
            contract, _ = await repo.get_or_create_contract(
                session, user_id=found_user.id, meeting_context=parsed.meeting_context
            )
            await repo.add_task(
                session, contract_id=contract.id, 
                description=task_input.description, 
                time_estimate=task_input.time_estimate
            )
        else:
            skipped_aliases.append(assignee_raw)

    await session.commit()

    contracts = await repo.get_contracts_for_date(session)
    drafts = [c for c in contracts if c.status.value == "draft"]
    
    if not drafts and not skipped_aliases:
        await parse_msg.edit_text("⚠️ No new draft tasks were created. Check aliases.")
        return

    msg_text = _format_final_review_message(drafts, skipped_aliases)
    await parse_msg.edit_text(msg_text, parse_mode="HTML", reply_markup=_build_final_review_kb())


# ──────────────────────────────────────────────
# Flow 2 – Manual Launch Callbacks
# ──────────────────────────────────────────────

@router.callback_query(F.data == "bulk_send")
async def handle_send_to_team(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_lead(callback.from_user.id):
         return await callback.answer("Admin only.", show_alert=True)
    contracts = await repo.activate_daily_plans(session)
    if not contracts:
        return await callback.answer("No draft plans to send.", show_alert=True)
    bot = callback.bot
    sent_count = 0
    for contract in contracts:
        user = contract.user
        if not user.notification_chat_id:
            continue
        msg_text = _format_draft_message(
            user.full_name or user.username or str(user.tg_id),
            contract.tasks,
            contract.meeting_context or ""
        )
        kb = _build_handshake_kb(contract.id)
        try:
            await bot.send_message(user.notification_chat_id, text=msg_text, parse_mode="HTML", reply_markup=kb)
            sent_count += 1
        except Exception as exc:
            logger.warning("Failed to DM user %s: %s", user.tg_id, exc)
    await session.commit()
    await callback.message.edit_text(f"🚀 {hbold('Handshake Flow Launched!')}\nSent packages to {sent_count} developer(s).", parse_mode="HTML")
    await callback.answer("Sent!")


@router.callback_query(F.data == "bulk_discard")
async def handle_discard_drafts(callback: CallbackQuery, session: AsyncSession) -> None:
    if not _is_lead(callback.from_user.id):
         return await callback.answer("Admin only.", show_alert=True)
    count = await repo.discard_daily_plans(session)
    await session.commit()
    await callback.message.edit_text(f"🗑️ Discarded {count} draft daily plan(s).")
    await callback.answer("Discarded.")
