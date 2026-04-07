"""
db_verify.py – Database State Verifier

Used to visually and programmatically confirm that Phase 1.2 (Append Mode,
Entity Resolution, and Handshake transitions) are working correctly.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.database import AsyncSessionLocal, init_db
from db.models import DailyContract, UserAlias


async def verify_db_state() -> None:
    """Performs integrity and state checks on the current DB state."""
    # Ensure schema exists
    await init_db()

    async with AsyncSessionLocal() as session:
        # 1. Entity Resolution Check
        print("--- [1] Entity Resolution Check ---")
        alias_result = await session.execute(
            select(UserAlias).options(selectinload(UserAlias.user))
        )
        aliases = alias_result.scalars().all()
        if not aliases:
            print("(!) No UserAliases found.")
        else:
            for al in aliases:
                print(f"Alias: '{al.alias}' -> User ID: {al.user_id} ({al.user.full_name or 'none'})")
        print("\n")

        # 2-4. Daily Contract & Task List Verification
        print("--- [2-4] Daily Contract & State Validation ---")
        target_date = date.today()
        contract_result = await session.execute(
            select(DailyContract)
            .where(DailyContract.target_date == target_date)
            .options(
                selectinload(DailyContract.tasks),
                selectinload(DailyContract.user)
            )
        )
        contracts = contract_result.scalars().all()

        append_mode_success = False
        target_developer = "Ярослав" 
        
        if not contracts:
            print(f"(!) No contracts found for today ({target_date}).")
        else:
            for contract in contracts:
                user_name = contract.user.full_name or contract.user.username or f"#{contract.user.tg_id}"
                print(f"Developer: {user_name}")
                print(f"  Status: {contract.status.value}")
                print(f"  Context: {(contract.meeting_context or 'None')[:80]}...")
                print(f"  Started At: {contract.started_at}")
                
                tasks = contract.tasks
                print(f"  Tasks ({len(tasks)}):")
                for task in tasks:
                    print(f"    - [{task.status.value}] {task.description} (est: {task.time_estimate or 'None'})")

                if target_developer.lower() in user_name.lower():
                    descriptions = [t.description.lower() for t in tasks]
                    has_sum1 = any("визуализацию связей" in d for d in descriptions)
                    has_sum2 = any("загрузить код бэкенда" in d or "загрузить код бэкэнда" in d for d in descriptions)
                    if has_sum1 and has_sum2:
                        append_mode_success = True
                
                print("-" * 30)

        print("\n--- [5] Append Mode Success Check ---")
        if append_mode_success:
            print("✅ Append Mode SUCCESS: Multiple task summaries consolidated for " + target_developer)
        else:
            print("❌ Append Mode INCOMPLETE: Specified tasks not found in a single contract.")


if __name__ == "__main__":
    asyncio.run(verify_db_state())
