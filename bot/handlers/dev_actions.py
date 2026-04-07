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
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core import agent
from db import repository as repo
from db.models import ContractStatus, TaskStatus

logger = logging.getLogger(__name__)
router = Router()

LEAD_TG_ID: int = int(os.getenv("LEAD_TG_ID", "0"))


# ──────────────────────────────────────────────
# FSM States
# ──────────────────────────────────────────────

class AwaitingEdit(StatesGroup):
    waiting_for_text = State()


# ──────────────────────────────────────────────
# Flow 2a – Approve button
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("approve:"))
async def handle_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    bot = callback.bot
    contract_id = int(callback.data.split(":")[1])
    contract = await repo.get_contract_today(
        session, user_id=0, target_date=None  # fetch by id below
    )
    # Direct fetch by PK
    from sqlalchemy import select
    from db.models import DailyContract
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.id == contract_id)
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    contract = result.scalar_one_or_none()

    if contract is None:
        await callback.answer("Contract not found.", show_alert=True)
        return

    if contract.user.tg_id != callback.from_user.id:
        await callback.answer("This is not your contract.", show_alert=True)
        return

    if contract.status != ContractStatus.draft:
        await callback.answer("Already processed.", show_alert=True)
        return

    # Activate contract → starts SLA timer
    await repo.activate_contract(session, contract_id)

    # Mark all tasks as in_progress
    for task in contract.tasks:
        await repo.update_task_status(session, task.id, TaskStatus.in_progress)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ Plan approved! Good luck today 🚀")
    await callback.answer()

    # Notify lead
    if LEAD_TG_ID:
        name = contract.user.full_name or contract.user.username or str(contract.user.tg_id)
        await bot.send_message(
            LEAD_TG_ID,
            f"✅ {name} approved their daily plan.",
        )


# ──────────────────────────────────────────────
# Flow 2b – Edit button (enter FSM)
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("edit:"))
async def handle_edit_start(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    contract_id = int(callback.data.split(":")[1])

    from sqlalchemy import select
    from db.models import DailyContract
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.id == contract_id)
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    contract = result.scalar_one_or_none()

    if contract is None or contract.user.tg_id != callback.from_user.id:
        await callback.answer("Not your contract.", show_alert=True)
        return

    # Store context in FSM
    await state.set_state(AwaitingEdit.waiting_for_text)
    await state.update_data(contract_id=contract_id)

    tasks_text = "\n".join(
        f"{i+1}. {t.description}" for i, t in enumerate(contract.tasks)
    )
    await callback.message.answer(
        f"Current tasks:\n{tasks_text}\n\n"
        "✏️ Write your updated task description(s) and I'll update the plan:"
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Flow 2b – Receive edited text, LLM rewrites, activates
# ──────────────────────────────────────────────

@router.message(StateFilter(AwaitingEdit.waiting_for_text))
async def handle_edit_text(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    bot = message.bot
    data = await state.get_data()
    contract_id: int = data["contract_id"]

    from sqlalchemy import select
    from db.models import DailyContract
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(DailyContract)
        .where(DailyContract.id == contract_id)
        .options(selectinload(DailyContract.tasks), selectinload(DailyContract.user))
    )
    contract = result.scalar_one_or_none()
    if not contract:
        await message.reply("Contract not found.")
        await state.clear()
        return

    original = " | ".join(t.description for t in contract.tasks)
    rewritten = await agent.rewrite_task_edit(original, message.text or "")

    # Update all tasks description (simplified: update first task; extend for multi-task)
    if contract.tasks:
        from sqlalchemy import update
        from db.models import Task
        await session.execute(
            update(Task)
            .where(Task.contract_id == contract_id)
            .values(description=rewritten)
        )

    await repo.activate_contract(session, contract_id)

    # Mark all tasks in_progress
    for task in contract.tasks:
        await repo.update_task_status(session, task.id, TaskStatus.in_progress)

    await state.clear()
    await message.reply(
        f"✅ Updated & approved!\n\n📝 New task: {rewritten}"
    )

    # Lead notification
    if LEAD_TG_ID:
        name = contract.user.full_name or contract.user.username or str(contract.user.tg_id)
        await bot.send_message(
            LEAD_TG_ID,
            f"✏️ {name} edited their daily plan:\n\n{rewritten}",
        )


# ──────────────────────────────────────────────
# Flow 3 – Developer replies to SLA ping (free-text DM)
# ──────────────────────────────────────────────

@router.message(F.text, F.chat.type == "private", StateFilter(None), ~F.text.startswith("/"))
async def handle_developer_update(
    message: Message, session: AsyncSession
) -> None:
    """
    Processes a plain text update from a developer (not in any FSM state).
    Finds their active contract and latest in_progress task, then parses via LLM.
    """
    tg_id = message.from_user.id
    if tg_id == LEAD_TG_ID:
        return  # Lead messages are handled by admin router

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
