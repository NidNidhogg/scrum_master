# 🏛 AI Scrum Master: System Architecture

This document outlines the core architecture, data flows, and design decisions of the AI Scrum Master bot.

---

## 1. Core Philosophy
The bot acts as a specialized assistant that bridges the gap between raw meeting discussions and structured developer plans. It utilizes **Google Gemini** for high-reasoning parsing while maintaining a strict, Lead-controlled workflow.

---

## 2. System Flows

### Flow 1: Ingestion & Smart Parsing (The Clean Slate)
- **Ruthless Cleanup**: Before re-parsing a date, the system performs an explicit two-step deletion: first deleting all child `Task` rows, then their parent `DailyContract` rows. This guarantees a clean slate and mitigates SQLite's default foreign key cascade limitations.
- **Smart Identity Resolution**: Ingestion logic uses a hierarchy (Alias -> Full Name -> Username) across ALL users (not just developers) to link tasks.
- **Dumb Extractor & Field Separation**:
    - **Inclusive Extractor**: EVERY non-empty line under a developer's header is captured as a task, even without bullet points or dashes.
    - **Field Separation**: The LLM strictly separates the `description` (action only, verbatim) from the `time_estimate` (designation for brackets like `[Сегодня]`, deadlines, or durations).

### Flow 2: The Handshake (Developer Check-in)
- **Activation Block**: Plans are sent to developers as `drafts`. A plan only becomes `active` once the developer approves it, starting the SLA monitor.
- **Simple Forward (The Pivot)**: If a developer proposes changes via the `✏️ Edit` button, the bot captures their verbatim text and forwards it directly to the Lead's DM for manual review. 
    - The database is **NOT** automatically updated by the AI.
    - The contract remains as a `draft`.
    - The Lead must manually apply the changes via the **Edit Drafts** menu if approved.

### Flow 3: SLA & Active Monitoring
- **Radar**: Every morning (11:00), the bot checks for developers who haven't approved their plans.
- **Ping**: Every 30 minutes, it monitors active plans for un-updated tasks (no status changes for >4 hours).

---

## 3. Database Schema Design (SQLAlchemy 2.0)

### `users`
- `tg_id`: BigInteger (Unique Index)
- `role`: Enum (Lead, Developer)
- `notification_chat_id`: For asynchronous DMs.

### `user_aliases`
- `user_id`: ForeignKey(users.id)
- `alias`: Unique string (e.g., "Dmitriy", "Dimas").

### `daily_contracts`
- `user_id`: ForeignKey(users.id, nullable=True) — Resolved developer.
- `lead_id`: ForeignKey(users.id) — **Sprint Ownership**. Identifies which Scrum Master initiated the contract, supporting shifts and multiple leads.
- `developer_alias`: Original string used in parsing (fallback).
- `status`: Enum (Draft, Active, Completed).
- `target_date`: Date of the tasks.

### `tasks`
- `contract_id`: ForeignKey(daily_contracts.id)
- `description`: Verbatim task action.
- `time_estimate`: Metadata bucket (estimates, bracketed info).
- `status`: Enum (Todo, In-progress, Done, Blocked).

---

## 4. Module Structure

- **`bot/`**: Telegram interface (Aiogram 3).
    - `handlers/admin.py`: Lead controls, atomic ingestion logic.
    - `handlers/dev_actions.py`: Developer Handshake and SLA responses.
- **`agent/`**: LLM bridge (Google Gemini API).
    - `core.py`: Gemini API integration with tool calling.
    - `prompts.py`: Strategic instructions for high-fidelity extraction.
- **`db/`**: Persistence layer.
    - `repository.py`: Atomic I/O and identity resolution.
- **`.env`**:
    - `GEMINI_API_KEY`: Required for parsing and synthesis.
    - `LEAD_TG_ID`: Primary system administrator.

---

## 5. Deployment Info
- **Tech Stack**: Python 3.13, Aiogram 3.15, SQLAlchemy 2.0, APScheduler.
- **Persistence**: SQLite (local) or PostgreSQL (production).
