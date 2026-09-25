# 🤖 AI Scrum Master Bot

**AI Scrum Master** is a high-fidelity Telegram bot designed to automate daily stand-ups and end-to-end task lifecycle management. Built for high-performance teams, it transforms unstructured meeting transcripts into actionable developer contracts, ensuring 100% accountability through "Handshake" workflows and persistent backlog management.

---

## 🛠 Tech Stack

The system is built on a modern, asynchronous Python stack optimized for reliability and speed:

*   **Logic:** [Python 3.13+](https://www.python.org/) + [aiogram 3.x](https://docs.aiogram.dev/) (Telegram Framework).
*   **AI Reasoning:** [Google Generative AI (Gemini)](https://ai.google.dev/) for high-density transcript parsing.
*   **Database:** [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + [aiosqlite](https://github.com/omnilib/aiosqlite) (Async SQLite).
*   **Scheduling:** [APScheduler](https://apscheduler.readthedocs.io/) for automated reminders and cron-based reporting.
*   **Testing:** [pytest](https://docs.pytest.org/) with an in-memory SQLite stack for isolated validation.

---

## ✨ Key Features

*   **Automated Transcript Parsing & Task Extraction:** Uses Google Gemini to scan raw meeting notes or transcripts, identifying assignees and work items with surgical precision.
*   **Persistent Backlog:** Automatically carries over unfinished tasks from previous days, injecting them into the new daily context to ensure no work item is ever forgotten.
*   **Missing Persons Radar (Automated Reminders):** A built-in cron service that monitors task activity and sends automated nudges to developers who haven't updated their status in 4+ working hours — repeated every 4h while idle, then escalated to the lead; unresolved contracts alert the lead instead of silently skipping.
*   **Scored Identity Resolution:** Transcript names resolve by score (exact alias > full name > username > word-token); ambiguous or substring-only matches never guess — Send-to-Team is blocked until the lead fixes them via `/alias`.
*   **Team Roster Config:** Per-team facts (e.g., two people sharing a first name) live in `config/team_roster.json` and are injected into the parser prompt — not hardcoded in the prompt itself.
*   **Extraction Evals:** Golden transcript cases in `evals/cases/` score assignee accuracy and verbatim carry-over wording through the real Gemini parser (`PYTHONPATH=. .venv/bin/python evals/run_evals.py`) — regression check before any prompt change.
*   **Idempotent UI Workflows:** Resilient Telegram inline keyboards designed to prevent double-click collisions, UI loops, and database duplication.

---

## 🚀 Core Workflows

### 1. The Handshake Flow (Stand-up Automation)
1.  **Ingestion:** The Lead sends a transcript or meeting summary to the bot.
2.  **Drafting:** The bot generates draft contracts for each developer.
3.  **Review:** The Lead reviews drafts and clicks **[🚀 Send to Team]**.
4.  **Handshake:** Developers receive a private DM with their tasks and an **[✅ Approve]** button.
5.  **Activation & Broadcast:** Upon approval, the contract becomes `active`, and a message is sent to the team group.
6.  **Dashboard:** The developer receives interactive buttons in their DM to close tasks as they finish.

### 2. Ad-hoc Tasks (/todo)
If a task arises outside of the daily meeting, the Lead can use:
`/todo "Developer Name" Task description`
The bot immediately resolves the developer and initiates a "Manual Handshake" flow for that specific item.

---

## 🛠 Admin Commands

| Command | Usage | Description |
| :--- | :--- | :--- |
| `/alias` | `/alias "Name" 12345` | Links a transcript name (with spaces) to a Telegram ID. |
| `/todo` | `/todo "Name" Description` | Manually assigns a task to a user. |
| `/chatid` | `/chatid` | Returns the current Chat ID for configuration. |
| `/daily` | `/daily <text>` | Manually triggers the parsing of a meeting transcript. |
| `/register` | `/register` | Registers the current user in the local database. |

---

## 🏗 Documentation

*   🏗 **[Architecture & Logic (ARCHITECTURE.md)](ARCHITECTURE.md)** — Deep dive into the MVA philosophy, state machine, and data flows.
*   🧪 **[Local Setup & Testing (TESTING.md)](TESTING.md)** — Step-by-step guide to installation, environment variables, and running the test suite.

---

### Deployment: auto-start on macOS (launchd)

To keep the bot running on a laptop — start at login, restart on crash,
and resume polling when the lid opens — install the bundled LaunchAgent:

```bash
./deploy/install_launchagent.sh    # renders + loads ~/Library/LaunchAgents/com.scrummaster.bot.plist
```

Requires `.venv` and `.env` in the repo. Logs land in `logs/bot.{out,err}.log`;
`./deploy/uninstall_launchagent.sh` removes it. The bot's own resilience
already covers the sleep gap: on-boot SLA reconciliation, `misfire_grace_time`
on the cron jobs, and aiogram polling picking up missed updates.

## 🏁 Quick Start

To get the bot running locally for development or testing:
1.  Navigate to **[TESTING.md](TESTING.md)**.
2.  Follow **Step 1 & 2** for environment setup and `.env` configuration.
3.  Run the bot using **Step 3**.
4.  Execute the automated test suite with `PYTHONPATH=. .venv/bin/pytest`.
