# Contributing to CallingGen Backend

## First-Time Setup

### 1. Clone the Repo
```bash
git clone https://github.com/callinggen/livekit-backend-main1.git
cd livekit-backend-main1/BACKEND
```

### 2. Create a Virtual Environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\Activate.ps1

# Mac / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 4. Create Your `.env` File
```bash
cp app/.env.example app/.env
```
Then open `app/.env` and fill in the values (get secret keys from the team lead).

### 5. Initialize the Local Database
```bash
python migrate.py
```

---

## Running Locally

Open separate terminals for each service:

```bash
# Terminal 1 — FastAPI server (auto-reloads on save)
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Campaign worker
python -m app.worker

# Terminal 3 — LiveKit agent
python agent.py start
```

API docs available at: http://localhost:8000/docs

---

## Git Workflow

1. **Always branch off main:**
   ```bash
   git checkout main && git pull origin main
   git checkout -b feature/your-feature-name
   ```

2. **Branch naming convention:**
   - `feature/short-description` — new features
   - `fix/short-description` — bug fixes
   - `hotfix/short-description` — urgent production fixes

3. **Commit messages:**
   - `feat: add X`
   - `fix: resolve Y issue`
   - `refactor: clean up Z`
   - `docs: update README`

4. **Push and open a Pull Request:**
   ```bash
   git push origin feature/your-feature-name
   ```
   Open a PR on GitHub targeting `main`. Tag the team lead as reviewer.

5. **Never push directly to `main`.**

---

## Running Tests

```bash
pytest
# or a specific file:
pytest tests/test_calls_api.py
```

---

## Environment Variables Reference

See `app/.env.example` for all required variables. Never commit real secrets.
