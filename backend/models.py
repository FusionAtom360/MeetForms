"""
models.py
SQLAlchemy ORM table definitions and database session setup.
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    DateTime, ForeignKey, inspect, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class Meet(Base):
    __tablename__ = "meets"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String, nullable=False)           # e.g. "Spring Invitational 2025"
    date       = Column(String, nullable=False)           # stored as ISO string "YYYY-MM-DD"
    deadline   = Column(String, nullable=True)            # sign-up deadline, ISO string "YYYY-MM-DD"
    course     = Column(String, nullable=False)           # "LCM" | "SCY" | "SCM"
    location   = Column(String, nullable=True)            # e.g. "Central Pool, Downtown"
    team_names    = Column(String, nullable=False, default="[]")
    description   = Column(String, nullable=True)
    category_type = Column(String, nullable=False, default="age_group")  # "age_group" | "division"
    is_active     = Column(Boolean, default=False)           # only one meet should be active at a time
    created_at    = Column(DateTime, default=datetime.utcnow)

    events  = relationship("Event",  back_populates="meet", cascade="all, delete-orphan")
    entries = relationship("Entry",  back_populates="meet", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id           = Column(Integer, primary_key=True, index=True)
    meet_id      = Column(Integer, ForeignKey("meets.id"), nullable=False)
    event_number = Column(Integer, nullable=False)
    distance     = Column(Integer, nullable=False)
    stroke       = Column(String,  nullable=False)
    gender       = Column(String,  nullable=False)
    age_group    = Column(String,  nullable=False)

    meet    = relationship("Meet",  back_populates="events")
    entries = relationship("Entry", back_populates="event", cascade="all, delete-orphan")


class Entry(Base):
    __tablename__ = "entries"

    id           = Column(Integer, primary_key=True, index=True)
    meet_id      = Column(Integer, ForeignKey("meets.id"),  nullable=False)
    event_id     = Column(Integer, ForeignKey("events.id"), nullable=False)

    # Athlete info
    last_name    = Column(String, nullable=False)
    first_name   = Column(String, nullable=False)
    age          = Column(Integer, nullable=False)
    team         = Column(String, nullable=False)
    gender       = Column(String, nullable=False)         # "M" | "F"
    division     = Column(String, nullable=True)          # "JV" | "Varsity" | None (for age_group meets)

    # Entry time stored as a plain string to preserve original formatting
    # Expected format: "MM:SS.ss" for times ≥1 min, or "SS.ss" for sprint events
    # "NT" is accepted for no-time entries
    entry_time   = Column(String, nullable=False, default="NT")

    submitted_at = Column(DateTime, default=datetime.utcnow)

    meet  = relationship("Meet",  back_populates="entries")
    event = relationship("Event", back_populates="entries")


# ---------------------------------------------------------------------------
# Helper: create all tables (called once on startup)
# ---------------------------------------------------------------------------

def init_db():
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    if not inspector.has_table("meets"):
        return

    meet_columns = {column["name"] for column in inspector.get_columns("meets")}
    if "team_names" not in meet_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE meets ADD COLUMN team_names TEXT NOT NULL DEFAULT '[]'"))

    if "location" not in meet_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE meets ADD COLUMN location TEXT"))

    if "description" not in meet_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE meets ADD COLUMN description TEXT"))

    if "category_type" not in meet_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE meets ADD COLUMN category_type TEXT NOT NULL DEFAULT 'age_group'"))

    if inspector.has_table("entries"):
        entry_columns = {column["name"] for column in inspector.get_columns("entries")}
        if "submitted_at" not in entry_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE entries ADD COLUMN submitted_at DATETIME"))
                connection.execute(text("UPDATE entries SET submitted_at = CURRENT_TIMESTAMP WHERE submitted_at IS NULL"))
        if "division" not in entry_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE entries ADD COLUMN division TEXT"))


# ---------------------------------------------------------------------------
# Dependency for FastAPI route injection
# ---------------------------------------------------------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
