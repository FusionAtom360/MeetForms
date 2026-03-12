"""
main.py
FastAPI application — all routes for the swim meet signup backend.

Public endpoints (no auth):
  GET  /meet/active          → active meet + its events
  POST /entries              → submit athlete entries

Admin endpoints (require header: X-Admin-Key: <ADMIN_API_KEY>):
  GET  /admin/meets          → list all meets
  POST /admin/meets          → create a new meet with events
  PUT  /admin/meets/{id}     → update meet details / swap active meet
  DELETE /admin/meets/{id}   → delete a meet and all its entries
  GET  /admin/entries        → all entries for a given meet
    PUT  /admin/entries/{id}   → update a single entry
    DELETE /admin/entries/{id} → delete a single entry
  GET  /admin/export/csv     → download CSV
  GET  /admin/export/hy3     → download Hy-Tek .hy3 file
"""

import os
import json
from typing import List, Optional
from datetime import datetime
import io

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse #, JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from models import Meet, Event, Entry, get_db, init_db
from export import generate_csv, generate_hy3

load_dotenv()

ADMIN_API_KEY   = os.getenv("ADMIN_API_KEY", "change-me")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

app = FastAPI(title="Swim Meet Signup API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def on_startup():
    init_db()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def require_admin(x_admin_key: Optional[str] = Header(default=None)):
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing admin key.")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class EventIn(BaseModel):
    event_number: int
    distance:     int
    stroke:       str
    gender:       str       # "M" | "F" | "X"
    age_group:    str       # "11-12", "Open", etc.

class MeetIn(BaseModel):
    name:      str
    date:      str          # "YYYY-MM-DD"
    deadline:  Optional[str] = None  # "YYYY-MM-DD"
    course:    str          # "LCM" | "SCY" | "SCM"
    team_names: List[str] = []
    is_active: bool = False
    events:    List[EventIn] = []

class MeetUpdate(BaseModel):
    name:      Optional[str]  = None
    date:      Optional[str]  = None
    deadline:  Optional[str]  = None
    course:    Optional[str]  = None
    team_names: Optional[List[str]] = None
    is_active: Optional[bool] = None
    events:    Optional[List[EventIn]] = None   # if provided, replaces all events

class SingleEntryIn(BaseModel):
    event_id:   int
    entry_time: str = "NT"

    @field_validator("entry_time")
    @classmethod
    def clean_time(cls, v: str) -> str:
        return v.strip() or "NT"


class EntryUpdate(BaseModel):
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    age: Optional[int] = None
    team: Optional[str] = None
    gender: Optional[str] = None
    event_id: Optional[int] = None
    entry_time: Optional[str] = None

    @field_validator("gender")
    @classmethod
    def normalize_optional_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.upper().strip()
        if v not in ("M", "F"):
            raise ValueError("Gender must be M or F")
        return v

    @field_validator("entry_time")
    @classmethod
    def clean_optional_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or "NT"

class AthleteEntriesIn(BaseModel):
    """One submission = one athlete + one or more events."""
    last_name:  str
    first_name: str
    age:        int
    team:       str
    gender:     str         # "M" | "F"
    meet_id:    int
    entries:    List[SingleEntryIn]

    @field_validator("gender")
    @classmethod
    def normalize_gender(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in ("M", "F"):
            raise ValueError("Gender must be M or F")
        return v


def normalize_team_names(team_names: Optional[List[str]]) -> List[str]:
    cleaned = []
    seen = set()
    for team_name in team_names or []:
        name = str(team_name).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    return cleaned


def parse_team_names(raw_team_names: Optional[str]) -> List[str]:
    if not raw_team_names:
        return []
    try:
        parsed = json.loads(raw_team_names)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_team_names(parsed)


# ---------------------------------------------------------------------------
# Public: active meet
# ---------------------------------------------------------------------------

@app.get("/meet/active")
def get_active_meet(db: Session = Depends(get_db)):
    """
    Returns the currently active meet along with its full event list.
    The signup form calls this on page load to build the event checkboxes.
    """
    meet = db.query(Meet).filter(Meet.is_active == True).first()
    if not meet:
        raise HTTPException(status_code=404, detail="No meet is currently open for registration.")

    return {
        "id":       meet.id,
        "name":     meet.name,
        "date":     meet.date,
        "deadline": meet.deadline,
        "course":   meet.course,
        "team_names": parse_team_names(meet.team_names),
        "events": [
            {
                "id":           e.id,
                "event_number": e.event_number,
                "gender":       e.gender,
                "age_group":    e.age_group,
                "distance":     e.distance,
                "stroke":       e.stroke,
            }
            for e in sorted(meet.events, key=lambda x: x.event_number)
        ],
    }


# ---------------------------------------------------------------------------
# Public: submit entries
# ---------------------------------------------------------------------------

@app.post("/entries", status_code=201)
def submit_entries(payload: AthleteEntriesIn, db: Session = Depends(get_db)):
    """
    Accepts a full athlete submission.  Creates one Entry row per event selected.
    Validates that:
      - the meet exists and is active
      - each event_id belongs to that meet
    """
    meet = db.query(Meet).filter(Meet.id == payload.meet_id, Meet.is_active == True).first()
    if not meet:
        raise HTTPException(status_code=400, detail="Meet not found or not currently active.")

    if meet.deadline:
        today = datetime.utcnow().date()
        try:
            deadline_date = datetime.strptime(meet.deadline, "%Y-%m-%d").date()
            if today > deadline_date:
                raise HTTPException(status_code=400, detail="The sign-up deadline for this meet has passed.")
        except ValueError:
            pass

    if not payload.entries:
        raise HTTPException(status_code=400, detail="Please select at least one event.")

    allowed_teams = parse_team_names(meet.team_names)
    if allowed_teams:
        submitted_team = payload.team.strip()
        if submitted_team not in allowed_teams:
            raise HTTPException(status_code=400, detail="Please select a valid team.")

    valid_event_ids = {e.id for e in meet.events}
    created = []

    for item in payload.entries:
        if item.event_id not in valid_event_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Event ID {item.event_id} does not belong to this meet."
            )
        entry = Entry(
            meet_id    = payload.meet_id,
            event_id   = item.event_id,
            last_name  = payload.last_name.strip(),
            first_name = payload.first_name.strip(),
            age        = payload.age,
            team       = payload.team.strip(),
            gender     = payload.gender,
            entry_time = item.entry_time,
        )
        db.add(entry)
        created.append(entry)

    db.commit()
    return {"message": f"Successfully submitted {len(created)} entry/entries.", "count": len(created)}


# ---------------------------------------------------------------------------
# Admin: meet management
# ---------------------------------------------------------------------------

@app.get("/admin/meets", dependencies=[Depends(require_admin)])
def list_meets(db: Session = Depends(get_db)):
    meets = db.query(Meet).order_by(Meet.created_at.desc()).all()
    return [
        {
            "id":        m.id,
            "name":      m.name,
            "date":      m.date,
            "deadline":  m.deadline,
            "course":    m.course,
            "team_names": parse_team_names(m.team_names),
            "is_active": m.is_active,
            "event_count": len(m.events),
            "entry_count": len(m.entries),
            "events": [
                {
                    "id":           e.id,
                    "event_number": e.event_number,
                    "distance":     e.distance,
                    "stroke":       e.stroke,
                    "gender":       e.gender,
                    "age_group":    e.age_group,
                } for e in sorted(m.events, key=lambda x: x.event_number)
            ]
        }
        for m in meets
    ]


@app.post("/admin/meets", status_code=201, dependencies=[Depends(require_admin)])
def create_meet(payload: MeetIn, db: Session = Depends(get_db)):
    """
    Creates a meet and its events in one call.
    If is_active=True, deactivates all other meets first (only one active at a time).
    """
    if payload.is_active:
        db.query(Meet).update({"is_active": False})

    meet = Meet(
        name      = payload.name,
        date      = payload.date,
        deadline  = payload.deadline,
        course    = payload.course,
        team_names = json.dumps(normalize_team_names(payload.team_names)),
        is_active = payload.is_active,
    )
    db.add(meet)
    db.flush()  # get meet.id before committing

    for ev in payload.events:
        event = Event(
            meet_id      = meet.id,
            event_number = ev.event_number,
            distance     = ev.distance,
            stroke       = ev.stroke,
            gender       = ev.gender,
            age_group    = ev.age_group,
        )
        db.add(event)

    db.commit()
    db.refresh(meet)
    return {"message": "Meet created.", "meet_id": meet.id}


@app.put("/admin/meets/{meet_id}", dependencies=[Depends(require_admin)])
def update_meet(meet_id: int, payload: MeetUpdate, db: Session = Depends(get_db)):
    """
    Updates meet metadata.  If events list is provided, it REPLACES all existing
    events for that meet (delete + re-insert).  Pass events=[] to clear all events.
    If is_active is set to True, all other meets are deactivated.
    """
    meet = db.query(Meet).filter(Meet.id == meet_id).first()
    if not meet:
        raise HTTPException(status_code=404, detail="Meet not found.")

    if payload.name      is not None: meet.name      = payload.name
    if payload.date      is not None: meet.date      = payload.date
    if payload.deadline  is not None: meet.deadline  = payload.deadline
    if payload.course    is not None: meet.course    = payload.course
    if payload.team_names is not None: meet.team_names = json.dumps(normalize_team_names(payload.team_names))

    if payload.is_active is True:
        db.query(Meet).filter(Meet.id != meet_id).update({"is_active": False})
        meet.is_active = True
    elif payload.is_active is False:
        meet.is_active = False

    if payload.events is not None:
        # Update events in-place where event_number matches (preserves entry foreign keys),
        # insert new ones, and delete removed ones (deleting their entries first).
        existing = {e.event_number: e for e in db.query(Event).filter(Event.meet_id == meet_id).all()}
        new_numbers = {ev.event_number for ev in payload.events}

        # Remove events (and their entries) that are no longer in the payload
        for num, event in existing.items():
            if num not in new_numbers:
                db.query(Entry).filter(Entry.event_id == event.id).delete()
                db.delete(event)

        # Upsert events from payload
        for ev in payload.events:
            if ev.event_number in existing:
                event = existing[ev.event_number]
                event.distance     = ev.distance
                event.stroke       = ev.stroke
                event.gender       = ev.gender
                event.age_group    = ev.age_group
            else:
                db.add(Event(
                    meet_id      = meet_id,
                    event_number = ev.event_number,
                    distance     = ev.distance,
                    stroke       = ev.stroke,
                    gender       = ev.gender,
                    age_group    = ev.age_group,
                ))

    db.commit()
    return {"message": "Meet updated."}


@app.delete("/admin/meets/{meet_id}", dependencies=[Depends(require_admin)])
def delete_meet(meet_id: int, db: Session = Depends(get_db)):
    meet = db.query(Meet).filter(Meet.id == meet_id).first()
    if not meet:
        raise HTTPException(status_code=404, detail="Meet not found.")
    db.delete(meet)
    db.commit()
    return {"message": "Meet and all associated events and entries deleted."}


# ---------------------------------------------------------------------------
# Admin: entries viewer
# ---------------------------------------------------------------------------

@app.get("/admin/entries", dependencies=[Depends(require_admin)])
def get_entries(
    meet_id: int = Query(..., description="ID of the meet to fetch entries for"),
    db: Session = Depends(get_db)
):
    """Returns all entries for a meet, sorted by team then last name."""
    entries = (
        db.query(Entry)
        .filter(Entry.meet_id == meet_id)
        .order_by(Entry.team, Entry.last_name, Entry.first_name)
        .all()
    )
    return [
        {
            "id":           e.id,
            "last_name":    e.last_name,
            "first_name":   e.first_name,
            "age":          e.age,
            "gender":       e.gender,
            "team":         e.team,
            "event_id":     e.event_id,
            "event_number": e.event.event_number,
            "event_name":   f"{e.event.distance} {e.event.stroke}",
            "entry_time":   e.entry_time,
            "submitted_at": e.submitted_at.isoformat() if e.submitted_at else None,
        }
        for e in entries
    ]


@app.put("/admin/entries/{entry_id}", dependencies=[Depends(require_admin)])
def update_entry(entry_id: int, payload: EntryUpdate, db: Session = Depends(get_db)):
    entry = db.query(Entry).filter(Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found.")

    meet = db.query(Meet).filter(Meet.id == entry.meet_id).first()
    if not meet:
        raise HTTPException(status_code=404, detail="Meet not found.")

    if payload.last_name is not None:
        entry.last_name = payload.last_name.strip()
    if payload.first_name is not None:
        entry.first_name = payload.first_name.strip()
    if payload.age is not None:
        entry.age = payload.age
    if payload.gender is not None:
        entry.gender = payload.gender
    if payload.team is not None:
        allowed_teams = parse_team_names(meet.team_names)
        submitted_team = payload.team.strip()
        if allowed_teams and submitted_team not in allowed_teams:
            raise HTTPException(status_code=400, detail="Please select a valid team.")
        entry.team = submitted_team
    if payload.entry_time is not None:
        entry.entry_time = payload.entry_time
    if payload.event_id is not None:
        event = db.query(Event).filter(Event.id == payload.event_id, Event.meet_id == entry.meet_id).first()
        if not event:
            raise HTTPException(status_code=400, detail="Selected event does not belong to this meet.")
        entry.event_id = payload.event_id

    db.commit()
    return {"message": "Entry updated."}


@app.delete("/admin/entries/{entry_id}", dependencies=[Depends(require_admin)])
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(Entry).filter(Entry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found.")
    db.delete(entry)
    db.commit()
    return {"message": "Entry deleted."}


# ---------------------------------------------------------------------------
# Admin: CSV export
# ---------------------------------------------------------------------------

@app.get("/admin/export/csv", dependencies=[Depends(require_admin)])
def export_csv(
    meet_id: int = Query(...),
    db: Session = Depends(get_db)
):
    meet = db.query(Meet).filter(Meet.id == meet_id).first()
    if not meet:
        raise HTTPException(status_code=404, detail="Meet not found.")

    entries = (
        db.query(Entry)
        .filter(Entry.meet_id == meet_id)
        .order_by(Entry.team, Entry.last_name, Entry.event_id)
        .all()
    )

    csv_content = generate_csv(meet, entries)
    filename = f"{meet.name.replace(' ', '_')}_{meet.date}_entries.csv"

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Admin: Hy-Tek .hy3 export
# ---------------------------------------------------------------------------

@app.get("/admin/export/hy3", dependencies=[Depends(require_admin)])
def export_hy3(
    meet_id: int = Query(...),
    db: Session = Depends(get_db)
):
    meet = db.query(Meet).filter(Meet.id == meet_id).first()
    if not meet:
        raise HTTPException(status_code=404, detail="Meet not found.")

    entries = (
        db.query(Entry)
        .filter(Entry.meet_id == meet_id)
        .order_by(Entry.team, Entry.last_name, Entry.event_id)
        .all()
    )

    hy3_content = generate_hy3(meet, entries)
    filename = f"{meet.name.replace(' ', '_')}_{meet.date}.hy3"

    return StreamingResponse(
        io.StringIO(hy3_content),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
