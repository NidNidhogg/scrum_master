# ARCHITECTURE.md: AI Scrum Master (V1.2)

## 1. System Vision & Philosophy
* **Product Goal:** Automation of team intraday task status monitoring with active facilitation (Handshake) and SLA control.
* **Architecture Style:** Lean Code (minimum infrastructure, maximum reuse of LLM logic).
* **Environment:** Core development is conducted in an IDE with AI agents (Antigravity). The code must be modular and predictable.
* **Tech Stack:** Python 3.11+, Telegram API (`aiogram` 3.x), Database (`SQLAlchemy` 2.0, `aiosqlite` -> `asyncpg`), LLM (`Google Gemini API`).

---

## 2. Core Business Workflows

The system operates as a Finite State Machine (FSM) around the `DailyContract` entity.

### Flow 1: Ingestion & Smart Parsing (Morning)
1.  **Trigger:** The Lead uploads a Markdown summary (from a transcriber) into the bot's direct messages (DM).
2.  **Processing:** The LLM agent (via Tool Use `BatchCreateContracts`) parses the text. It extracts:
    * Developer Names/Aliases.
    * General discussion context (`meeting_context`).
    * Tasks (`description`) and their estimates (`time_estimate`).
3.  **Entity Resolution:** The bot maps raw names to `tg_id` from the `user_aliases` table. In case of connection drops and multiple summary uploads, the bot operates in **Append Mode** (adding tasks to the existing daily plan).
4.  **State:** Tasks are saved in the DB with a `draft` status.

### Flow 2: The Handshake (Two-way Approval)
1.  The bot distributes the daily plan (Draft) to developers via DMs or by tagging them in the group.
2.  The UI provides two Inline buttons: `[Approve]` and `[Edit]`.
3.  **On Approval:** The contract status changes to `active`, and `started_at` is recorded (starting the SLA timer).
4.  **On Edit:** `aiogram FSM` is triggered. The user writes their task variant in text -> LLM updates the DB -> status changes to `active` -> The Lead receives a notification about the change.

### Flow 3: Active Monitoring & SLA (Daytime)
We abandon the strict 14:00 timer in favor of dynamic checks:
1.  **The "Missing Persons" Radar (11:00):** Cron checks all users with the `is_active=True` flag. If someone doesn't have a `DailyContract` for today, the Lead receives an alert.
2.  **SLA Monitor (Every 30 mins):** Cron checks tasks in the `in_progress` status.
    * *Condition:* `(Current Time - started_at) >= 4 hours` AND `sla_ping_sent == False`.
    * *Action:* The LLM generates a contextual question (considering the original `time_estimate` and task essence) and pings the developer.
    * After sending, the `sla_ping_sent = True` flag is set.
3.  **Response Processing:** The developer replies in plain text. The LLM, via the `update_task_status` Tool, transitions the task to `done` or `blocked` (filling in the `blocker_note`).

### Flow 4: Evening Synthesis (18:00)
1.  The bot extracts the day's data: `DailyContract`, `Tasks`, `IntradayUpdates`.
2.  The LLM aggregates an Executive Summary: closed tasks, carry-over tasks, critical blockers.
3.  The report is sent to the Lead.

---

## 3. Database Schema Design (SQLAlchemy 2.0)

Implementation strictly via `DeclarativeBase` and `Mapped` types.

* **`users`**: Basic info. Fields: `tg_id` (Unique), `role`, `is_active` (Boolean), `notification_chat_id`.
* **`user_aliases`**: For transcript Entity Resolution. Field `alias` (String). ON DELETE CASCADE relationship with `users`.
* **`daily_contracts`**: One-day plan.
    * Fields: `target_date`, `meeting_context` (Text), `status` (Enum: `draft`, `active`, `completed`).
    * SLA Fields: `started_at` (DateTime), `sla_ping_sent` (Boolean).
* **`tasks`**: Contract decomposition.
    * Fields: `description`, `time_estimate` (String), `status` (Enum: `todo`, `in_progress`, `done`, `blocked`), `blocker_note`.
* **`intraday_updates`**: Log of raw text messages from users.

---

## 4. Module Structure (Antigravity Target Structure)

```text
/scrum_master_bot
├── ARCHITECTURE.md          # This manifesto
├── .env                     # Configuration (TG_TOKEN, ANTHROPIC_API_KEY, DB_URL)
├── bot/
│   ├── main.py              # Entry point (Bot, routers, DB initialization)
│   ├── handlers/            # aiogram Routers
│   │   ├── admin.py         # Receiving summary from Lead
│   │   ├── dev_actions.py   # Handshake (Approve/Edit), ping responses
│   ├── middlewares/         # Dependency Injection (DB session passing)
├── db/
│   ├── database.py          # SQLAlchemy AsyncEngine and sessionmaker
│   ├── models.py            # Declarative entity models
│   ├── repository.py        # SQL query isolation (Data Access Layer)
├── agent/
│   ├── core.py              # Anthropic API integration
│   ├── prompts.py           # System prompts (Ingestion, SLA Ping, Synthesis)
│   ├── tools.py             # Pydantic schemas for Tool Use
├── worker/
│   ├── scheduler.py         # APScheduler/Celery for crons (SLA, Radar, Synthesis)
```
