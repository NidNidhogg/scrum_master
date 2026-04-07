# 🤖 AI Scrum Master Bot

**AI Scrum Master** is an intelligent Telegram bot designed to automate morning daily meetings, task tracking, and team facilitation. It leverages the Google Gemini API to transform unstructured meeting transcripts into clear, actionable task plans.

## 📖 Documentation (Navigation)

Detailed project information is organized into the following documents:

* 🏗 **[Architecture & Logic (ARCHITECTURE.md)](ARCHITECTURE.md)** — Deep dive into the Finite State Machine (FSM), database schema, and core business flows.
* 🚀 **[Local Setup & Testing (LOCAL_TESTING.md)](LOCAL_TESTING.md)** — A step-by-step guide to configuring `.env`, installing dependencies, and running the MVP locally.

## ✨ Key Features (MVP v1.2)

* **Smart Ingestion:** LLM-powered extraction of tasks, time estimates, and context from raw text transcripts.
* **Fuzzy Deduplication:** Robust protection against task duplication when re-uploading summaries for the same date.
* **The Handshake:** Interactive distribution of daily plans to developers via DMs with `[✅ Approve]` or `[✏️ Edit]` functionality.
* **Sprint Ownership:** Native support for multiple Leads (Scrum Masters) operating in shifts.

## 🛠 Tech Stack

* **Language:** Python 3.11+
* **Bot Framework:** `aiogram` 3.x
* **Database:** SQLite + `SQLAlchemy` 2.0 (ORM) + `alembic` (migrations)
* **AI / LLM:** Google Gemini API
