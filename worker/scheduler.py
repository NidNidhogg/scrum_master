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

SLA_THRESHOLD_HOURS = 4      # first ping after this much idle time
SLA_REPEAT_HOURS = 4         # re-ping every window while still idle
SLA_MAX_PINGS = 2            # after this many dev pings, escalate to lead


async def sla_monitor(bot) -> None:
    logger.info("[SLA] Running SLA monitor…")
    async with AsyncSessionLocal() as session:
        overdue = await repo.get_overdue_active_contracts(
            session,
            threshold_hours=SLA_THRESHOLD_HOURS,
            repeat_after_hours=SLA_REPEAT_HOURS,
        )
        if not overdue:
            logger.info("[SLA] No idle users found.")
            return

        for contract in overdue:
            user = contract.user
            if not user:
                # Unresolved names must not degrade silently — pings can
                # never reach them, so the lead hears about it instead.
                logger.warning(
                    "[SLA] Contract %s (%s) has no linked user – alerting lead.",
                    contract.id, contract.developer_alias,
                )
                if LEAD_TG_ID:
                    await bot.send_message(
                        LEAD_TG_ID,
                        f"🚨 SLA: contract #{contract.id} "
                        f"({contract.developer_alias or 'unknown'}) has no linked "
                        f"user — SLA pings cannot reach them. Resolve via /alias.",
                    )
                await repo.mark_sla_ping_sent(session, contract.id)
                await session.commit()
                continue

            # Resolve the chat to ping: prefer explicit notification_chat_id, fall back to tg_id.
            chat_id = user.notification_chat_id or user.tg_id
            if not chat_id:
                logger.warning(
                    "[SLA] User %s has neither notification_chat_id nor tg_id – skipping.",
                    user.id,
                )
                continue

            # Check if there are still unfinished tasks
            has_pending = any(t.status.value != "done" for t in contract.tasks)
            if not has_pending:
                logger.info("[SLA] Contract %s all tasks done – closing monitor.", contract.id)
                await repo.mark_sla_ping_sent(session, contract.id)
                await session.commit()
                continue

            name = user.full_name or user.username or "dev"
            pings_so_far = contract.sla_ping_count or 0

            if pings_so_far >= SLA_MAX_PINGS:
                # Dev was pinged repeatedly and stayed idle → escalate to lead.
                if LEAD_TG_ID:
                    open_descs = ", ".join(
                        t.description[:60] for t in contract.tasks
                        if t.status.value != "done"
                    )
                    await bot.send_message(
                        LEAD_TG_ID,
                        f"🚨 SLA escalation: {name} idle after "
                        f"{pings_so_far} pings. Open: {open_descs}",
                    )
                await repo.mark_sla_ping_sent(session, contract.id)
                await session.commit()
                logger.info("[SLA] Escalated contract %s to lead.", contract.id)
                continue

            ping_text = (
                f"Hey {name}, it's been a while since the last update. "
                f"Any progress on your tasks? Let me know if you're stuck! 🤖"
            )

            try:
                await bot.send_message(chat_id, ping_text)
            except Exception:
                logger.warning(
                    "[SLA] Failed to DM user %s (chat %s) – will retry next cycle.",
                    user.tg_id, chat_id,
                    exc_info=True,  # captures the real TelegramNetworkError / etc.
                )
                continue  # do NOT mark as sent; let the next hourly run retry

            # Only mark & commit after a confirmed successful send.
            count = await repo.record_sla_ping(session, contract.id)
            await session.commit()
            logger.info(
                "[SLA] Pinged user %s via chat %s (ping #%d)",
                user.tg_id, chat_id, count,
            )


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

    # misfire_grace_time: a tick missed while the process was down fires
    # late (up to 1h) instead of being dropped — covers laptop suspend.
    grace = 3600

    scheduler.add_job(
        missing_persons_radar,
        CronTrigger(hour=11, minute=0, timezone=TZ),
        kwargs={"bot": bot},
        id="missing_persons_radar",
        replace_existing=True,
        misfire_grace_time=grace,
    )

    scheduler.add_job(
        sla_monitor,
        # Every hour at :00 during working hours 10-19
        CronTrigger(hour="10-19", minute=0, timezone=TZ),
        kwargs={"bot": bot},
        id="sla_monitor",
        replace_existing=True,
        misfire_grace_time=grace,
    )

    scheduler.add_job(
        evening_synthesis,
        CronTrigger(hour=18, minute=0, timezone=TZ),
        kwargs={"bot": bot},
        id="evening_synthesis",
        replace_existing=True,
        misfire_grace_time=grace,
    )

    return scheduler
