"""
bot/main.py – Entry point.

Wires together:
  - Dispatcher + routers
  - DbSessionMiddleware (DI)
  - DB table creation
  - APScheduler
  - Polling startup
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from bot.handlers import admin, dev_actions
from bot.middlewares.db_session import DbSessionMiddleware
from db.database import init_db
from worker.scheduler import create_scheduler

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TG_TOKEN: str = os.getenv("TG_TOKEN", "")


async def main() -> None:
    if not TG_TOKEN:
        raise RuntimeError("TG_TOKEN is not set in .env")

    # ── DB ─────────────────────────────────────
    logger.info("Initialising database…")
    await init_db()

    # ── Bot & Dispatcher ───────────────────────
    bot = Bot(
        token=TG_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # ── Middleware ─────────────────────────────
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    # ── Routers (order matters for filter priority) ─
    # dev_actions must come before admin so non-lead private messages hit the right router
    dp.include_router(dev_actions.router)
    dp.include_router(admin.router)

    # ── Scheduler ──────────────────────────────
    scheduler = create_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started with %d jobs.", len(scheduler.get_jobs()))

    # ── Start polling ──────────────────────────
    logger.info("Bot is running. Press Ctrl+C to stop.")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
