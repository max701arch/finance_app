<<<<<<< HEAD
# finance_app
proffesional web for a finance business
=======
# Finance Tracker (Flask)

Multi-user personal finance tracker with authentication, search/filter, charts, CSV/PDF export, dark/light mode, and security hardening.

## Features

- User registration/login with hashed passwords
- Per-user transaction isolation (multi-user)
- Add income/expense transactions
- Search, month/type/category/date filtering
- Monthly income vs expense chart
- Category-wise expense pie chart
- Export filtered data as CSV or PDF
- Delete one transaction / clear filtered history / clear all history
- CSRF protection + secure headers + auth throttling
- Light/Dark theme toggle

## Local Run (Windows)

1. Create and activate virtualenv:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. (Optional) copy env template:

```powershell
Copy-Item .env.example .env
```

4. Start app:

```powershell
python app.py
```

Open: `http://127.0.0.1:5000`

## Production Run (Linux)

```bash
pip install -r requirements.txt
export FLASK_SECRET_KEY="replace_with_strong_secret"
export TRUST_PROXY=1
export FLASK_USE_HTTPS=1
export FLASK_SECURE_COOKIES=1
export FINANCE_DB_PATH="/var/data/finance.db"
gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app
```

## Deploy To Render (Recommended)

This repo already includes `render.yaml`, so you can use Render Blueprint deploy.

1. Push project to GitHub.
2. In Render, click **New +** -> **Blueprint**.
3. Select your repository.
4. Render will auto-read `render.yaml` and create:
- Web service
- Health check (`/health`)
- Persistent disk mounted to `/var/data`
- Required environment variables

After deploy, app URL will be live on internet.

## Manual Deploy (Render Web Service)

1. Push project to GitHub.
2. Create **Web Service** on Render and select your repo.
3. Build command:

```bash
pip install -r requirements.txt
```

4. Start command:

```bash
gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app
```

5. Set environment variables in Render:
- `FLASK_SECRET_KEY` = long random value
- `TRUST_PROXY` = `1`
- `FLASK_USE_HTTPS` = `1`
- `FLASK_SECURE_COOKIES` = `1`
- `FINANCE_DB_PATH` = `/var/data/finance.db`

6. Attach a persistent disk and mount it (for SQLite durability).

## Healthcheck

- Endpoint: `/health`
- Returns: `{ "status": "ok" }`

## Notes

- SQLite is suitable for single-instance deployment. If you scale to multiple instances, migrate to PostgreSQL.
- Keep `FLASK_DEBUG=0` in production.
- Do not commit `.env` or local database files.
>>>>>>> f6644f4 (Initial commit)
