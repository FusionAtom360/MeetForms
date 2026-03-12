# Swim Meet Signup — Backend

FastAPI + PostgreSQL (Supabase) backend for the swim meet entry system.

## Setup

### 1. Install dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 2. Configure environment
Edit `.env` and replace the placeholder values:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.YOUR_REF.supabase.co:5432/postgres
ADMIN_API_KEY=your-secret-key-here
FRONTEND_ORIGIN=https://your-netlify-site.netlify.app
```

### 3. Run locally
```bash
uvicorn main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

---

## Deploying to Railway

1. Push this `backend/` folder to a GitHub repo
2. Create a new project on [railway.app](https://railway.app)
3. Connect your GitHub repo
4. Set environment variables in Railway's dashboard (same as `.env`)
5. Railway auto-detects Python and runs `uvicorn main:app --host 0.0.0.0 --port $PORT`

Your live API URL will be something like: `https://swim-meet-backend.up.railway.app`

---

## API Reference

### Public
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/meet/active` | Get active meet + events (used by signup form) |
| POST | `/entries` | Submit athlete entries |
| GET | `/health` | Health check |

### Admin (require header: `X-Admin-Key: <your-key>`)
| Method | Route | Description |
|--------|-------|-------------|
| GET | `/admin/meets` | List all meets |
| POST | `/admin/meets` | Create meet with events |
| PUT | `/admin/meets/{id}` | Update meet or swap events |
| DELETE | `/admin/meets/{id}` | Delete meet + all entries |
| GET | `/admin/entries?meet_id=1` | View all entries |
| GET | `/admin/export/csv?meet_id=1` | Download CSV |
| GET | `/admin/export/hy3?meet_id=1` | Download Hy-Tek .hy3 file |

---

## Creating a Meet (example)

```bash
curl -X POST https://your-api.railway.app/admin/meets \
  -H "X-Admin-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Spring Invitational 2025",
    "date": "2025-04-12",
    "course": "SCY",
    "is_active": true,
    "events": [
      {"event_number": 1, "event_name": "200 Freestyle", "gender": "M", "age_group": "Open", "distance": 200, "stroke": "Freestyle"},
      {"event_number": 2, "event_name": "200 Freestyle", "gender": "F", "age_group": "Open", "distance": 200, "stroke": "Freestyle"},
      {"event_number": 3, "event_name": "100 Backstroke", "gender": "M", "age_group": "Open", "distance": 100, "stroke": "Backstroke"}
    ]
  }'
```

---

## File Structure

```
backend/
├── main.py          # FastAPI app + all routes
├── models.py        # SQLAlchemy table definitions + DB session
├── export.py        # CSV and Hy-Tek .hy3 generation
├── requirements.txt
├── .env             # credentials (never commit this)
└── README.md
```
