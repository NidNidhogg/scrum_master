"""
worker/scheduler.py – APScheduler cron jobs.

Jobs:
  1. missing_persons_radar  – 11:00 daily – alert lead about devs without a plan
  2. sla_monitor            – every 30 min – ping overdue active contracts
  3. evening_synthesis      – 18:00 daily – generate & send daily summary
"""
from __future__ import annotations

import logging
import os
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent.core import agent
from db.database import AsyncSessionLocal
from db import repository as repo

logger = logging.getLogger(__name__)

TZ: str = os.getenv("TZ", "Europe/Moscow")
LEAD_TG_ID: int = int(os.getenv("LEAD_TG_ID", "0"))


# ──────────────────────────────────────────────
# Job 1 – Missing Persons Radar (11:00)
# ──────────────────────────────────────────────

async def missing_persons_radar(bot) -> None:
    logger.info("[Radar] Running missing persons check…")
    async with AsyncSessionLocal() as session:
        active_devs = await repo.get_active_developers(session)
        contracts_today = await repo.get_contracts_for_date(session)
        users_with_contract = {c.user_id for c in contracts_today}

        missing = [u for u in active_devs if u.id not in users_with_contract]
        if not missing:
            logger.info("[Radar] All developers have contracts today.")
            return

        names = ", ".join(
            u.full_name or u.username or str(u.tg_id) for u in missing
        )
        alert = (
            f"🚨 *Missing Persons Radar* – {date.today()}\n\n"
            f"The following developers have NO daily plan yet:\n{names}\n\n"
            f"Please check the meeting transcript or contact them directly."
        )
        if LEAD_TG_ID:
            await bot.send_message(LEAD_TG_ID, alert, parse_mode="Markdown")


# ──────────────────────────────────────────────
# Job 2 – SLA Monitor (Every hour during working hours)
# ──────────────────────────────────────────────

async def sla_monitor(bot) -> None:
    logger.info("[SLA] Running SLA monitor…")
    async with AsyncSessionLocal() as session:
        # threshold_hours=4 per requirements
        overdue = await repo.get_overdue_active_contracts(session, threshold_hours=4)
        if not overdue:
            logger.info("[SLA] No idle users found.")
            return

        for contract in overdue:
            user = contract.user
            if not user or not user.notification_chat_id:
                continue

            # Check if there are still unfinished tasks
            has_pending = any(t.status.value != "done" for t in contract.tasks)
            if not has_pending:
                continue

            name = user.full_name or user.username or "dev"
            ping_text = f"Hey {name}, it's been 4 hours since the last update. Any progress on your tasks? Let me know if you're stuck! 🤖"

            try:
                await bot.send_message(user.notification_chat_id, ping_text)
                await repo.mark_sla_ping_sent(session, contract.id)
                await session.commit()
                logger.info("[SLA] Pinged idle user %s (chat %s)", user.tg_id, user.notification_chat_id)
            except Exception:
                logger.warning("[SLA] Failed to DM user %s", user.tg_id)
                continue


# ──────────────────────────────────────────────
# Job 3 – Evening Synthesis (18:00)
# ──────────────────────────────────────────────

async def evening_synthesis(bot) -> None:
    logger.info("[Synthesis] Generating evening summary…")
    async with AsyncSessionLocal() as session:
        contracts = await repo.get_contracts_for_date(session)
        if not contracts:
            logger.info("[Synthesis] No contracts today, skipping.")
            return

        data_lines: list[str] = []
        for contract in contracts:
            user = contract.user
            if user is None:
                name = contract.developer_alias or f"Contract #{contract.id}"
            else:
                name = user.full_name or user.username or str(user.tg_id)
            data_lines.append(f"Developer: {name}")
            for task in contract.tasks:
                data_lines.append(
                    f"  - [{task.status.value}] {task.description}"
                    + (f" | Blocker: {task.blocker_note}" if task.blocker_note else "")
                )
            data_lines.append("")

        contracts_data = "\n".join(data_lines)

        intraday = await repo.get_intraday_updates_today(session)
        if intraday:
            data_lines.append("=== Raw developer updates ===")
            for upd in intraday:
                u_name = upd.user.full_name or upd.user.username or str(upd.user.tg_id)
                data_lines.append(f"[{u_name}] {upd.raw_text}")
            contracts_data = "\n".join(data_lines)

    try:
        summary = await agent.generate_evening_summary(contracts_data)
    except Exception:
        logger.exception("[Synthesis] LLM synthesis failed")
        return

    if LEAD_TG_ID:
        await bot.send_message(
            LEAD_TG_ID,
            f"📊 *Daily Summary – {date.today()}*\n\n{summary}",
            parse_mode="Markdown",
        )


# ──────────────────────────────────────────────
# Scheduler setup
# ──────────────────────────────────────────────

def create_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)

    scheduler.add_job(
        missing_persons_radar,
        CronTrigger(hour=11, minute=0, timezone=TZ),
        kwargs={"bot": bot},
        id="missing_persons_radar",
        replace_existing=True,
    )

    scheduler.add_job(
        sla_monitor,
        # Every hour at :00 during working hours 10-19
        CronTrigger(hour="10-19", minute=0, timezone=TZ),
        kwargs={"bot": bot},
        id="sla_monitor",
        replace_existing=True,
    )

    scheduler.add_job(
        evening_synthesis,
        CronTrigger(hour=18, minute=0, timezone=TZ),
        kwargs={"bot": bot},
        id="evening_synthesis",
        replace_existing=True,
    )

    return scheduler
