"""Uhrzeiten fuer Uploads und Best-ofs: "12:00, 17:30" lesen und pruefen, ob gerade ein Zeitfenster offen ist."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_TIME = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def parse_times(text) -> list[tuple[int, int]]:
    """'12:00, 17.30 20:00' -> [(12, 0), (17, 30), (20, 0)], sortiert und ohne Doppelte. Fehler: ValueError."""
    times = set()
    for part in re.split(r"[,;\s]+", str(text or "").strip()):
        if not part:
            continue
        match = _TIME.match(part)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError(f"'{part}' ist keine Uhrzeit. Schreibe z. B. 12:00, 17:30, 20:00.")
        times.add((int(match[1]), int(match[2])))
    return sorted(times)


def format_times(times) -> str:
    return ", ".join(f"{h:02d}:{m:02d}" for h, m in times)


def open_slot(times, now: datetime, grace_minutes: float) -> datetime | None:
    """Die Uhrzeit, deren Zeitfenster gerade offen ist (Uhrzeit <= jetzt < Uhrzeit + Nachholzeit), sonst None.
    Sind mehrere offen, gilt die spaeteste."""
    grace = timedelta(minutes=max(1.0, float(grace_minutes)))
    best = None
    for day in (now.date() - timedelta(days=1), now.date()):  # gestern: Fenster ueber Mitternacht
        for hour, minute in times:
            slot = datetime(day.year, day.month, day.day, hour, minute)
            if slot <= now < slot + grace and (best is None or slot > best):
                best = slot
    return best


def next_slot(times, now: datetime) -> datetime | None:
    """Die naechste Uhrzeit nach jetzt (heute oder morgen)."""
    for day in (now.date(), now.date() + timedelta(days=1)):
        for hour, minute in times:
            slot = datetime(day.year, day.month, day.day, hour, minute)
            if slot > now:
                return slot
    return None


def bestof_period(mode: str, now: datetime, time_text, weekday: int) -> str | None:
    """Kennung des Zeitraums, fuer den jetzt ein Best-of faellig ist ('2026-11-16' bzw. '2026-W47'), sonst None."""
    times = parse_times(time_text) or [(18, 0)]
    hour, minute = times[0]
    if mode == "daily":
        due = datetime(now.year, now.month, now.day, hour, minute)
        return now.strftime("%Y-%m-%d") if now >= due else None
    if mode == "weekly":
        monday = now.date() - timedelta(days=now.weekday())
        day = monday + timedelta(days=int(weekday))
        due = datetime(day.year, day.month, day.day, hour, minute)
        iso = now.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}" if now >= due else None
    return None
