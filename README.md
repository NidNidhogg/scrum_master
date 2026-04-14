# 🤖 AI Scrum Master Bot

**AI Scrum Master** is an intelligent Telegram bot designed to automate daily stand-ups and task lifecycle management. It transforms unstructured meeting transcripts into actionable task contracts and ensures team accountability through automated "Handshake" workflows and SLA monitoring.

---

## ✨ Key Features

*   **Smart Ingestion:** High-fidelity extraction of tasks and context from raw transcripts using Google Gemini.
*   **The Handshake Flow:** Automated distribution of daily plans via DMs for developer approval and activation.
*   **Real-time Transparency:** Once a developer approves their plan, a summary is instantly broadcasted to the team's Telegram group.
*   **4-Hour SLA Monitor:** Automatically pings developers who haven't updated their tasks within 4 working hours (10:00–19:00).
*   **Ad-hoc Tasking (/todo):** Instantly assign tasks outside meetings; follows a separate "Manual Handshake" flow to prevent collisions with daily plans.
*   **Identity Resolution:** Advanced "Smart Match" algorithm that links speech aliases (e.g., "Dimas", "Дмитрий Б.") to registered database users.

---

## 🚀 Core Workflows

### 1. The Handshake Flow (Stand-up Automation)
1.  **Ingestion:** The Lead sends a transcript or meeting summary to the bot.
2.  **Drafting:** The bot generates draft contracts for each developer.
3.  **Review:** The Lead reviews drafts and clicks **[🚀 Send to Team]**.
4.  **Handshake:** Developers receive a private DM with their tasks and an **[✅ Approve]** button.
5.  **Activation & Broadcast:** Upon approval, the contract becomes `active`, and a message is sent to the team group:
    *   *Daily Stand-up:* `✅ [Dev] approved their daily plan`
    *   *Manual Task:* `🔥 [Dev] took a manual task: [Description]`
6.  **Dashboard:** The developer receives a list of interactive buttons in their DM to close tasks as they finish.

### 2. ad-hoc Tasks
If a task comes up outside of the daily meeting, the Lead can use:
`/todo "Developer Name" Task description`
The bot will immediately resolve the developer and send them a Handshake request for that specific task.

---

## 🛠 Admin Commands

| Command | Usage | Description |
| :--- | :--- | :--- |
| `/alias` | `/alias "Name" 12345` | Links a transcript name (with spaces) to a Telegram ID. Updates if exists. |
| `/todo` | `/todo "Name" Description` | Manually assigns a task to a user. Supports quoted names. |
| `/chatid` | `/chatid` | Returns the current Chat ID (useful for setting `TEAM_CHAT_ID`). |
| `/daily` | `/daily <text>` | Parses the provided text as a meeting transcript. |
| `/register` | `/register` | (All users) Registers the current user in the system. |

---

## 🏗 Documentation

*   🏗 **[Architecture & Logic (ARCHITECTURE.md)](ARCHITECTURE.md)** — Deep dive into the AI reasoning, SLA logic, and database schema.
*   🚀 **[Local Setup & Testing (LOCAL_TESTING.md)](LOCAL_TESTING.md)** — Step-by-step guide to installation and environment variables.

---

## 🛠 Tech Stack

*   **Logic:** Python 3.13 + `aiogram` 3.x
*   **AI:** Google Gemini API (Flash 1.5/2.0)
*   **Database:** SQLite + `SQLAlchemy` 2.0
*   **Scheduling:** `APScheduler` for SLA monitoring and reporting.
