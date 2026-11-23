from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable


def norm(value: str) -> str:
    """Normalize names for matching/fingerprints (case, punctuation, whitespace)."""
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


@dataclass(frozen=True)
class Show:
    platform: str
    target: str
    theatre: str
    movie: str
    show_date: str  # YYYY-MM-DD
    show_time: str  # HH:MM (24h)
    venue_id: str = ""
    movie_id: str = ""
    session_id: str = ""
    format: str = ""
    language: str = ""
    seats_available: str = ""
    price: str = ""
    booking_url: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable per-platform identity of a show."""
        parts = [
            self.platform,
            self.target,
            self.venue_id or norm(self.theatre),
            self.movie_id or norm(self.movie),
            self.show_date,
            self.show_time,
            norm(self.format),
        ]
        return "|".join(parts)

    @property
    def group_key(self) -> str:
        """Cross-platform identity, used to combine duplicate alerts."""
        parts = [
            self.target,
            norm(self.theatre),
            self.show_date,
            self.show_time,
            norm(self.format),
        ]
        return "|".join(parts)

    def label(self) -> str:
        bits = [f"{self.show_date} {self.show_time}"]
        if self.format:
            bits.append(self.format)
        if self.seats_available:
            bits.append(self.seats_available)
        return " · ".join(bits)


def parse_time(raw: str) -> str | None:
    """Convert '07:05 PM' / '19:05' / ISO datetime to 'HH:MM'."""
    raw = (raw or "").strip()
    if not raw:
        return None
    iso = re.search(r"T(\d{2}):(\d{2})", raw)
    if iso:
        return f"{iso.group(1)}:{iso.group(2)}"
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$", raw, re.I)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    meridiem = (m.group(3) or "").upper()
    if meridiem == "PM" and hour < 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_date(raw: str) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw or "")
    return m.group(1) if m else None


def iso_dates(base: date, days: int) -> list[str]:
    from datetime import timedelta

    return [(base + timedelta(days=i)).isoformat() for i in range(days)]


def similar(a: str, b: str) -> bool:
    """Loose but safe name matching: normalized substring or high similarity."""
    from difflib import SequenceMatcher

    na, nb = norm(a), norm(b)
    if not nb:
        return True
    if not na:
        return False
    if nb in na or na in nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.72


def dedupe(shows: Iterable[Show]) -> list[Show]:
    seen: set[str] = set()
    result: list[Show] = []
    for show in shows:
        if show.fingerprint in seen:
            continue
        seen.add(show.fingerprint)
        result.append(show)
    return result
