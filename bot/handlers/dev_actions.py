"""
bot/handlers/dev_actions.py – Flow 2 & 3: Handshake + SLA reply processing.

FSM States:
  AwaitingEdit.waiting_for_text  – developer chose "Edit", waiting for their text
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.markdown import hbold, hitalic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent.core import agent
from db import repository as repo
from db.models import ContractStatus, DailyContract, Task, TaskStatus

logger = logging.getLogger(__name__)
router = Router()

# Temporary store for proposed task text keyed by contract_id.
# Keyed by contract_id -> raw proposed text string.
# Lives in-process; sufficient for a single-instance bot.
_pending_edits: dict[int, str] = {}

def _parse_int_env(key: str, default: int = 0) -> int:
    val = os.getenv(key, "").strip()
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

LEAD_TG_ID: int = _parse_int_env("LEAD_TG_ID")
TEAM_CHAT_ID: int = _parse_int_env("TEAM_CHAT_ID")


# ──────────────────────────────────────────────
# FSM States
# ──────────────────────────────────────────────

class AwaitingEdit(StatesGroup):
    waiting_for_text = State()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _build_dashboard_keyboard(tasks: list[Task]) -> InlineKeyboardMarkup:
    """Build an InlineKeyboard with one [🏁 Закрыть: <task_name>] button per task."""
    buttons = []
    for task in tasks:
        label = task.description[:20]
        buttons.append(
            [InlineKeyboardButton(
                text=f"🏁 Закрыть: {label}",
                callback_data=f"close_task:{task.id}",
            )]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)




async def send_team_notification(bot, contract: DailyContract) -> None:
    """
    Consolidated group notification logic.
    - Manual task: ✅ [User] accepted a new task: [Task description]
    - Daily plan:  ✅ [User] approved their daily plan:\n  • task 1\n  • task 2
    """
    if not TEAM_CHAT_ID:
        return

    dev_name = contract.user.full_name or contract.user.username or str(contract.user.tg_id)
    # Manual tasks are identified by meeting_context set in /todo
    is_manual = (contract.meeting_context == "Manual task assignment")

    if is_manual:
        task_desc = contract.tasks[0].description if contract.tasks else "Unknown task"
        team_text = (
            f"✅ {hbold(dev_name)} accepted a new task:\n"
            f"  • {hitalic(task_desc)}"
        )
    else:
        task_lines = "\n".join(f"  • {hitalic(t.description)}" for t in contract.tasks)
        team_text = (
            f"✅ {hbold(dev_name)} approved their daily plan:\n"
            f"{task_lines}"
        )

    try:
        await bot.send_message(TEAM_CHAT_ID, team_text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to send team notification for contract %s", contract.id)


# ──────────────────────────────────────────────
# Flow 2a – Approve button
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def handle_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    bot = callback.bot
    parts = callback.data.split(":")
    # Format can be approve:c:ID (daily) or approve:t:ID (manual)
    
    task_id = None
    contract = None
    
    if len(parts) == 3: # new format
        type_prefix = parts[1]
        id_val = int(parts[2])
        if type_prefix == "t":
            task_obj = await repo.get_task(session, id_val)
            if task_obj:
                contract = task_obj.contract
                task_id = task_obj.id
        else: # "c"
            result = await session.execute(
                select(DailyContract)
                .where(DailyContract.id == id_val)
                .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
            )
            contract = result.scalar_one_or_none()
    else: # legacy format approve:ID
        contract_id = int(parts[1])
        result = await session.execute(
            select(DailyContract)
            .where(DailyContract.id == contract_id)
            .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
        )
        contract = result.scalar_one_or_none()

    if contract is None:
        try:
            await callback.answer("Contract/Task not found.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    if contract.user.tg_id != callback.from_user.id:
        try:
            await callback.answer("This is not your plan.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    # ── Logic Change: Collisions ──────────────────────────────────────────
    # If it's a manual task approval (task_id is set), we only care if THAT task is done.
    # If it's a contract approval, we check the whole contract.
    if task_id:
        # Check specific task from the DB (refresh to be sure)
        task_ref = await repo.get_task(session, task_id)
        if task_ref.status != TaskStatus.todo:
            try:
                await callback.answer("Already processed! ✅", show_alert=True)
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
            return
    elif contract.status != ContractStatus.draft:
        try:
            await callback.answer("Already processed.", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        return

    # ── 1. Activate ───────────────────────────────────────────────────────
    await repo.activate_contract(session, contract.id)

    # If task_id is set, only mark THAT task. Otherwise, all tasks.
    if task_id:
        await repo.update_task_status(session, task_id, TaskStatus.in_progress)
    else:
        for t in contract.tasks:
            await repo.update_task_status(session, t.id, TaskStatus.in_progress)

    await session.commit()
    await session.refresh(contract)

    # ── 2. Immediate Broadcast to team chat (Refactored) ───────────────────
    await send_team_notification(bot, contract)

    # ── 3. UI Update ───────────────────────────────────────────────────────
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            raise

    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    # ── 3. Developer DM dashboard ──────────────────────────────────────────
    dev_tg_id = contract.user.tg_id
    dev_name = contract.user.full_name or contract.user.username or str(dev_tg_id)
    
    if contract.tasks:
        keyboard = _build_dashboard_keyboard(list(contract.tasks))
        try:
            await bot.send_message(
                dev_tg_id,
                "Твои задачи на сегодня:",
                reply_markup=keyboard,
            )
        except Exception:
            logger.exception("Failed to send developer dashboard")
    else:
        await bot.send_message(dev_tg_id, "✅ Plan approved! Good luck today 🚀")

    # Notify lead (legacy/info)
    if LEAD_TG_ID:
        try:
            await bot.send_message(
                LEAD_TG_ID,
                f"✅ {dev_name} approved their daily plan.",
            )
        except Exception:
            logger.exception("Failed to notify lead")


# ──────────────────────────────────────────────
# Flow 4 – Close task button (developer dashboard)
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("close_task:"))
async def handle_close_task(callback: CallbackQuery, session: AsyncSession) -> None:
    bot = callback.bot
    task_id = int(callback.data.split(":")[1])

    # Fetch the task with its contract and developer
    result = await session.execute(
        select(Task)
        .where(Task.id == task_id)
        .options(
            selectinload(Task.contract).selectinload(DailyContract.user),
            selectinload(Task.contract).selectinload(DailyContract.tasks),
        )
    )
    task = result.scalar_one_or_none()

    if task is None:
        try:
            await callback.answer("Task not found.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    contract = task.contract
    if contract.user.tg_id != callback.from_user.id:
        try:
            await callback.answer("This is not your task.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    if task.status == TaskStatus.done:
        try:
            await callback.answer("Already closed.", show_alert=True)
        except TelegramBadRequest:
            pass
        return

    # ── a) Mark task as done ───────────────────────────────────────────────
    await repo.update_task_status(session, task_id, TaskStatus.done)
    await session.commit()

    task_description = task.description
    dev_name = (
        contract.user.full_name
        or contract.user.username
        or str(contract.user.tg_id)
    )

    # ── b) Edit the dashboard message – remove only this button ───────────
    remaining_tasks = [
        t for t in contract.tasks
        if t.id != task_id and t.status != TaskStatus.done
    ]

    if remaining_tasks:
        new_keyboard = _build_dashboard_keyboard(remaining_tasks)
        try:
            await callback.message.edit_reply_markup(reply_markup=new_keyboard)
        except TelegramBadRequest as e:
            if "message is not modified" not in e.message:
                logger.exception("Failed to edit dashboard keyboard")
    else:
        # All tasks done – remove keyboard entirely
        try:
            await callback.message.edit_text(
                "Твои задачи на сегодня:\n\n✅ Все задачи закрыты!",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in e.message:
                logger.exception("Failed to clear dashboard keyboard")

    try:
        await callback.answer("Задача закрыта! ✅")
    except TelegramBadRequest:
        pass

    # ── c) Notify team chat ────────────────────────────────────────────────
    if TEAM_CHAT_ID:
        try:
            await bot.send_message(
                TEAM_CHAT_ID,
                f"🔥 {dev_name} закрыл задачу: {task_description}",
            )
        except Exception:
            logger.exception("Failed to send task-close notification to TEAM_CHAT_ID")


# ──────────────────────────────────────────────
# Flow 2b – Edit button (enter FSM)
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit:"))
async def handle_edit_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    parts = callback.data.split(":")
    contract = None

    if len(parts) == 3:
        type_prefix = parts[1]
        id_val = int(parts[2])
        if type_prefix == "t":
            task_obj = await repo.get_task(session, id_val)
            if task_obj: contract = task_obj.contract
        else:
            result = await session.execute(
                select(DailyContract).where(DailyContract.id == id_val)
                .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
            )
            contract = result.scalar_one_or_none()
    else: # legacy
        contract_id = int(parts[1])
        result = await session.execute(
            select(DailyContract).where(DailyContract.id == contract_id)
            .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
        )
        contract = result.scalar_one_or_none()

    if contract is None or contract.user.tg_id != callback.from_user.id:
        await callback.answer("Not your plan.", show_alert=True)
        return

    # Store context in FSM
    await state.set_state(AwaitingEdit.waiting_for_text)
    await state.update_data(contract_id=contract.id)

    tasks_text = "\n".join(
        f"{i+1}. {t.description}" for i, t in enumerate(contract.tasks)
    )
    await callback.message.answer(
        f"Current tasks:\n{tasks_text}\n\n"
        "✏️ Write your updated task description(s) and I'll update the plan:"
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Flow 2b – Receive edited text, forward to Lead
# ──────────────────────────────────────────────

@router.message(StateFilter(AwaitingEdit.waiting_for_text))
async def handle_edit_text(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    bot = message.bot
    data = await state.get_data()
    contract_id: int = data["contract_id"]

    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.id == contract_id)
        .options(selectinload(DailyContract.user))
    )
    contract = result.scalar_one_or_none()

    if not contract:
        await message.reply("Contract not found.")
        await state.clear()
        return

    developer_name = (
        contract.user.full_name
        or contract.user.username
        or f"User {contract.user.tg_id}"
    )
    proposed_text = message.text or "[No text provided]"

    # Stash proposed text so the Lead approval handler can apply it to the DB.
    _pending_edits[contract_id] = proposed_text

    # Forward to Lead exactly as requested
    if LEAD_TG_ID:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve Plan", callback_data=f"lead_approve_review:{contract_id}")
        
        forward_msg = (
            f"⚠️ {hbold(developer_name)} proposes changes to their plan:\n\n"
            f"{proposed_text}\n\n"
            f"(You can approve the current state or edit it manually)."
        )
        try:
            await bot.send_message(LEAD_TG_ID, forward_msg, parse_mode="HTML", reply_markup=kb.as_markup())
        except Exception:
            logger.exception("Failed to notify lead about proposed edit")

    await state.clear()
    await message.reply("✅ Your proposed changes have been sent to the Lead for review.")


@router.callback_query(F.data.startswith("lead_approve_review:"))
async def handle_lead_approve_edit(callback: CallbackQuery, session: AsyncSession) -> None:
    """
    Lead approves the developer's plan (often after manual edits or review).
    """
    if callback.from_user.id != LEAD_TG_ID:
        return

    contract_id = int(callback.data.split(":")[1])
    
    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.id == contract_id)
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    contract = result.scalar_one_or_none()

    if not contract or contract.status != ContractStatus.draft:
        try:
            await callback.answer("Plan already active or not found.")
        except TelegramBadRequest:
            pass
        return

    # 1. Apply proposed task descriptions (if the developer submitted an edit).
    proposed_text = _pending_edits.pop(contract_id, None)
    if proposed_text:
        # Parse the proposed text into individual task lines.
        # Strips leading numbering ("1.", "-", "•") and blank lines.
        import re
        raw_lines = proposed_text.splitlines()
        new_descriptions = [
            re.sub(r'^[\s\d]+[.\-)•]\s*', '', line).strip()
            for line in raw_lines
            if line.strip()
        ]
        # Update DB task descriptions in order; skip extras if fewer tasks than lines.
        for task, new_desc in zip(contract.tasks, new_descriptions):
            if new_desc:
                task.description = new_desc
                session.add(task)
        logger.info(
            "Applied %d proposed description(s) to contract %s",
            len(new_descriptions), contract_id,
        )

    # 2. Activate contract
    await repo.activate_contract(session, contract_id)
    for task in contract.tasks:
        await repo.update_task_status(session, task.id, TaskStatus.in_progress)

    await session.commit()
    # Refresh with relationships so send_team_notification sees updated descriptions.
    result2 = await session.execute(
        select(DailyContract)
        .where(DailyContract.id == contract_id)
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    contract = result2.scalar_one()

    # 3. Notify team (DRY Refactored logic)
    await send_team_notification(callback.bot, contract)

    # 3. Update UI
    try:
        await callback.message.edit_text(f"✅ Plan for {contract.user.full_name} activated.", reply_markup=None)
    except TelegramBadRequest:
        pass
    
    try:
        await callback.answer("Plan activated! 🚀")
    except TelegramBadRequest:
        pass


# ──────────────────────────────────────────────
# Flow 3 – Developer replies to SLA ping (free-text DM)
# ──────────────────────────────────────────────

@router.message(
    F.text,
    F.chat.type == "private",
    StateFilter(None),
    ~F.text.startswith("/"),
    F.from_user.id != LEAD_TG_ID,
)
async def handle_developer_update(
    message: Message, session: AsyncSession
) -> None:
    """
    Processes a plain text update from a developer (not in any FSM state).
    Finds their active contract and latest in_progress task, then parses via LLM.
    """
    tg_id = message.from_user.id

    user = await repo.get_user_by_tg_id(session, tg_id)
    if not user:
        await message.reply("You are not registered. Use /register first.")
        return

    await repo.log_intraday_update(session, user_id=user.id, raw_text=message.text or "")

    contract = await repo.get_contract_today(session, user_id=user.id)
    if not contract:
        await message.reply("No active plan found for today. Wait for your Lead to upload the summary.")
        return

    in_progress_tasks = [t for t in contract.tasks if t.status.value == "in_progress"]
    if not in_progress_tasks:
        await message.reply("No in-progress tasks found. Your plan may already be complete!")
        return

    # Simple heuristic: apply update to the first in-progress task
    task = in_progress_tasks[0]

    try:
        update_input = await agent.parse_status_update(task, message.text or "")
    except Exception as exc:
        logger.exception("Status update parsing failed")
        await message.reply(f"❌ Could not parse your update: {exc}")
        return

    from db.models import TaskStatus as TS
    new_status = TS(update_input.new_status)
    await repo.update_task_status(
        session,
        task_id=task.id,
        status=new_status,
        blocker_note=update_input.blocker_note,
    )

    status_emoji = {"done": "✅", "blocked": "🚧", "in_progress": "🔄"}.get(
        update_input.new_status, "ℹ️"
    )
    await message.reply(
        f"{status_emoji} Got it! Task status updated to *{update_input.new_status}*.",
        parse_mode="Markdown",
    )
