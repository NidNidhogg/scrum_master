# 🧪 AI Scrum Master: Testing Manifesto

This document outlines the complete testing strategy for the AI Scrum Master bot, covering both automated unit/integration tests and manual end-to-end scenarios.

---

## Part 1: Automated Testing (Pytest)

The project uses a structured automated testing suite to ensure high reliability, especially for database operations and business logic state transitions.

### 1. The Environment
We use **pytest** with an **in-memory SQLite database** (`sqlite+aiosqlite:///:memory:`). This ensures:
- **Isolation:** Each test runs on a fresh database schema.
- **Speed:** No disk I/O for database operations, resulting in instant execution.
- **Cleanliness:** No temporary files are left behind after testing.

### 2. Running the Suite
To execute all automated tests, run the following command from the root directory:

```bash
PYTHONPATH=. .venv/bin/pytest
```

### 3. The Testing Pyramid
Our suite is organized into three distinct levels of validation:

*   **`conftest.py`**: The foundation. Contains shared async fixtures; each test gets a **fresh in-memory engine** so committed rows cannot leak between tests.
*   **Level 1: `tests/test_repository.py`**: Verifies core Data Access Object (DAO) logic. Focuses on CRUD operations, identity resolution, and **fuzzy duplicate prevention** (ensuring the same task isn't added twice to a contract).
*   **Level 2: `tests/test_business_logic.py`**: Validates the **Idempotent State Machine**. Tests simulate Telegram callbacks to ensure that operations like "Approve" or "Activate" can be called multiple times without causing duplicate notifications or invalid state transitions.
*   **Level 3: `tests/test_llm_parser.py`**: Focuses on the LLM parsing logic. Uses `unittest.mock` to simulate Gemini API responses, allowing us to verify transcript extraction accuracy and backlog merging without making real network calls.
*   **Level 4: `tests/test_sla_repeat.py`**: SLA monitor windows — first ping on overdue, repeat-window suppression and re-entry, terminal escalation flag, and recent-activity exemption.
*   **Evals: `evals/`**: Two-layer quality contour for the LLM extraction itself. `evals/test_name_resolution.py` runs offline (scored name matching — substring collisions like "Иван"→"Иванов" and bare shared first names must not guess). `evals/run_evals.py` feeds golden transcripts (`evals/cases/*.json`) through the **real** Gemini parser and scores assignee accuracy, verbatim carry-over wording, and forbidden aliases — regression check before any prompt change (needs `GEMINI_API_KEY`).

### 4. Developer Rule
> [!IMPORTANT]
> Any new database operations, repository methods, or core state machine logic **must** be covered by a corresponding pytest in the appropriate level of the pyramid.

---

## Part 2: Local E2E Manual Testing

This section describes the manual deployment and testing process for full-cycle verification (Handshake Flow).

### 🛠 Step 1: Environment Preparation

1. **Clone the repository:**
   ```bash
   git clone <your_repo_url>
   cd scrum_master
   ```

2. **Create and activate virtual environment:**
   ```bash
   # macOS/Linux:
   python3 -m venv .venv
   source .venv/bin/activate

   # Windows:
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

### 🔐 Step 2: Environment Variables Configuration

Create a `.env` file in the root directory and fill it with your credentials:

```env
# Telegram Bot Token (Main or Test)
BOT_TOKEN=your_bot_token_here

# Google Gemini API Key
GEMINI_API_KEY=your_gemini_key_here

# Your Telegram ID (to grant yourself Lead/Scrum-Master rights)
# Get your ID from @userinfobot
LEAD_TG_ID=your_telegram_id
```

### ▶️ Step 3: Running the Bot

Ensure your virtual environment is active and run:

```bash
# macOS/Linux:
PYTHONPATH=. .venv/bin/python3 bot/main.py

# Windows (PowerShell):
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe bot\main.py
```

If you see `Bot is running...` in the console, you are ready to test.

### 🎯 Step 4: E2E Manual Test Scenario

To verify the full "Handshake Flow," perform these steps in Telegram:

1.  **Initialization**:
    Send `/start` to the bot. It will register you in the local database.
2.  **Alias Setup (Team Emulation)**:
    Map your name to your ID so the bot can assign tasks to you:
    ```text
    /alias "Your Name" YOUR_TG_ID
    ```
    *(Example: `/alias "Alexey" 123456789`)*
3.  **Transcript Upload**:
    Send a `.txt` file containing a summary of tasks. The bot will parse it and return a **Final Review** list.
4.  **Task Distribution**:
    Under the Final Review list, click **[🚀 Send to Team]**. The bot will send you a DM with your specific tasks.
5.  **Edit Flow Testing**:
    In the DM you received, click **[✏️ Edit]**. Send a comment (e.g., *"This needs 5 hours, not 2"*). The bot should confirm receipt and forward the proposal to you (acting as the Lead) with the tag `⚠️ [Name] proposes changes to their plan:`.

🎉 **Testing Complete! If all steps pass, the MVP is functioning as expected.**
