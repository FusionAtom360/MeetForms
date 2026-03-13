"""
export.py
Generates CSV and Hy-Tek SD3 (.hy3) export files from a list of Entry rows.
"""

import csv
import io
from datetime import date
from typing import List
from models import Entry, Meet


def generate_csv(meet: Meet, entries: List[Entry]) -> str:
    """
    Returns a CSV string with one row per entry.
    Columns match what most coaches expect and what can be manually imported
    into Hy-Tek if the .hy3 route isn't used.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow(
        [
            "Last Name",
            "First Name",
            "Age",
            "Gender",
            "Team",
            "Event #",
            "Event Name",
            "Distance",
            "Stroke",
            "Age Group",
            "Entry Time",
            "Submitted At",
        ]
    )

    for e in entries:
        writer.writerow(
            [
                e.last_name,
                e.first_name,
                e.age,
                e.gender,
                e.team,
                e.event.event_number,
                e.event.distance,
                e.event.stroke,
                e.event.age_group,
                e.entry_time,
                e.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if e.submitted_at else "",
            ]
        )

    return output.getvalue()


def _pad(value: str, length: int, align: str = "left", fill: str = " ") -> str:
    """Pad or truncate a string to exactly `length` characters."""
    value = str(value) if value is not None else ""
    if align == "left":
        return value[:length].ljust(length, fill)
    else:
        return value[:length].rjust(length, fill)


def _hy3_time(entry_time: str) -> str:
    """
    Convert a human-readable entry time to the SD3 time format.
    SD3 stores time as an 8-char field: MMSSSSTT  (minutes, seconds * 100, hundredths * 100)
    Actually Hy-Tek stores it as a plain integer = total hundredths of a second, right-justified 8 chars.

    Input examples:  "1:23.45"  →  "    8345"
                     "59.78"    →  "    5978"
                     "NT"       →  "       0"  (no-time)
    """
    entry_time = entry_time.strip().upper()
    if entry_time in ("NT", "NS", "DQ", "SCR", ""):
        return "       0"
    try:
        if ":" in entry_time:
            parts = entry_time.split(":")
            minutes = int(parts[0])
            sec_parts = parts[1].split(".")
            seconds = int(sec_parts[0])
            hundredths = (
                int(sec_parts[1].ljust(2, "0")[:2]) if len(sec_parts) > 1 else 0
            )
        else:
            sec_parts = entry_time.split(".")
            minutes = 0
            seconds = int(sec_parts[0])
            hundredths = (
                int(sec_parts[1].ljust(2, "0")[:2]) if len(sec_parts) > 1 else 0
            )

        total_hundredths = (minutes * 60 + seconds) * 100 + hundredths
        return str(total_hundredths).rjust(8)
    except Exception:
        return "       0"


def _stroke_code(stroke: str) -> str:
    """Map stroke name to SD3 stroke code."""
    mapping = {
        "freestyle": "1",
        "backstroke": "2",
        "breaststroke": "3",
        "butterfly": "4",
        "im": "5",
        "individual medley": "5",
        "medley relay": "6",
        "freestyle relay": "7",
        "relay": "6",
    }
    return mapping.get(stroke.lower().strip(), "1")


def _course_code(course: str) -> str:
    mapping = {"LCM": "1", "SCY": "2", "SCM": "3"}
    return mapping.get(course.upper(), "1")


def _gender_code(gender: str) -> str:
    g = gender.upper().strip()
    if g in ("M", "MALE", "BOY", "BOYS"):
        return "M"
    if g in ("F", "FEMALE", "GIRL", "GIRLS"):
        return "F"
    return "M"


def generate_hy3(meet: Meet, entries: List[Entry]) -> str:
    """
    Returns a string containing a valid SD3 (.hy3) file for Hy-Tek MEET MANAGER import.
    """
    lines = []
    today = date.today().strftime("%m%d%Y")

    # ── A0: File / vendor header ──────────────────────────────────────────
    # Positions: 1-2 record type, 3-47 vendor name, 48-51 version, 52-59 date
    a0 = "A0"
    a0 += _pad("SwimMeetSignup Custom Export", 45)
    a0 += _pad("1.00", 4)
    a0 += _pad(today, 8)
    lines.append(a0)

    # ── B1: Meet record ───────────────────────────────────────────────────
    # Positions: 1-2 type, 3-32 meet name, 33-40 start date (MMDDYYYY),
    #            41-48 end date, 49 course code, 50-59 facility name (optional)
    meet_date_str = meet.date.replace("-", "")  # "YYYYMMDD" → reformat to MMDDYYYY
    try:
        y, m, d = meet.date.split("-")
        meet_date_formatted = f"{m}{d}{y}"
    except Exception:
        meet_date_formatted = today

    b1 = "B1"
    b1 += _pad(meet.name, 30)
    b1 += _pad(meet_date_formatted, 8)  # start date
    b1 += _pad(meet_date_formatted, 8)  # end date (same day; adjust if multi-day)
    b1 += _course_code(meet.course)
    b1 += _pad("", 10)  # facility (blank)
    lines.append(b1)

    # ── Collect distinct teams and athletes ───────────────────────────────
    teams = sorted(set(e.team for e in entries))
    # Group entries by (last, first, age, gender, team) as unique athlete key
    athletes: dict = {}
    for e in entries:
        key = (e.last_name.upper(), e.first_name.upper(), e.age, e.gender, e.team)
        if key not in athletes:
            athletes[key] = []
        athletes[key].append(e)

    # ── C1 + D0 + E0 blocks per team ─────────────────────────────────────
    athlete_serial = 1  # SD3 uses sequential IDs within the file

    for team in teams:
        # C1: Team record
        # Positions: 1-2 type, 3-7 team abbrev, 8-37 team full name, 38 gender (X=open)
        c1 = "C1"
        c1 += _pad(team[:5].upper(), 5)  # abbreviation (up to 5 chars)
        c1 += _pad(team, 30)  # full name
        c1 += "X"  # gender = open/mixed
        lines.append(c1)

        team_athletes = {k: v for k, v in athletes.items() if k[4] == team}

        for (last, first, age, gender, _), athlete_entries in team_athletes.items():
            # D0: Athlete record
            # Positions: 1-2 type, 3-22 last, 23-42 first, 43-44 age,
            #            45 gender, 46-50 team abbrev, 51-58 birth date (blank ok),
            #            59-66 USS# (blank ok), 67-72 athlete ID (sequential)
            d0 = "D0"
            d0 += _pad(last, 20)
            d0 += _pad(first, 20)
            d0 += _pad(str(age), 2, align="right")
            d0 += _gender_code(gender)
            d0 += _pad(team[:5].upper(), 5)
            d0 += _pad("", 8)  # birth date blank
            d0 += _pad("", 8)  # USS# blank
            d0 += _pad(str(athlete_serial).zfill(6), 6)
            lines.append(d0)

            # E0: One entry per event
            for e in athlete_entries:
                # Positions: 1-2 type, 3-8 athlete ID, 9 stroke, 10-13 distance,
                #            14 course, 15-22 entry time (hundredths), 23 time code (A=actual/NT)
                e0 = "E0"
                e0 += _pad(str(athlete_serial).zfill(6), 6)
                e0 += _stroke_code(e.event.stroke)
                e0 += _pad(str(e.event.distance), 4, align="right")
                e0 += _course_code(meet.course)
                e0 += _hy3_time(e.entry_time)
                e0 += "A" if e.entry_time.upper() not in ("NT", "") else "N"
                e0 += _pad(str(e.event.event_number), 4, align="right")
                lines.append(e0)

            athlete_serial += 1

    # ── Z0: Footer ────────────────────────────────────────────────────────
    z0 = "Z0" + _pad(str(len(lines) + 1), 6, align="right")
    lines.append(z0)

    return "\r\n".join(lines) + "\r\n"
