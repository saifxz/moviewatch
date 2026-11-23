from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from ..models import Show, parse_date, parse_time


@dataclass
class SourceResult:
    platform: str
    target: str
    shows: list[Show] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    url: str = ""
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and not self.skipped


class SourceError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, timeout: float = 30.0, ua: str | None = None):
        self.timeout = timeout
        self.ua = ua or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    def get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        headers = {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.text


def iter_jsonld_objects(html: str) -> Iterable[dict[str, Any]]:
    """Yield every JSON-LD object embedded in an HTML document."""
    pattern = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
    for match in pattern.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        yield from _walk_jsonld(value)


def _walk_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for key in ("@graph", "itemListElement", "events"):
            child = value.get(key)
            if isinstance(child, list):
                for item in child:
                    yield from _walk_jsonld(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_jsonld(item)


def parse_screening_events(html: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for obj in iter_jsonld_objects(html):
        kind = obj.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "ScreeningEvent" in kinds:
            events.append(obj)
    return events


def _first(value: Any, *keys: str) -> Any:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in keys:
        if key in value and value[key] not in (None, "", []):
            return value[key]
    return None


def screening_event_to_show(event: dict[str, Any], platform: str, target: str) -> Show | None:
    start = str(_first(event, "startDate", "startTime") or "")
    show_date = parse_date(start)
    show_time = parse_time(start)
    if not show_date or not show_time:
        return None

    location = _first(event, "location", "venue", "place") or {}
    work = _first(event, "workPresented", "work") or {}
    offer = _first(event, "offers", "offer") or {}

    if isinstance(location, list):
        location = location[0] if location else {}
    if isinstance(work, list):
        work = work[0] if work else {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}

    theatre = str(_first(location, "name", "alternateName") or "")
    movie = str(_first(work, "name") or _first(event, "name") or "")
    event_name = str(_first(event, "name") or "")
    video_format = _first(event, "videoFormat") or _first(event, "screenFormat")
    fmt = str(video_format or "").strip()
    if not fmt:
        m = re.search(r"\(([^)]+)\)\s*$", event_name)
        fmt = m.group(1) if m else ""

    url = str(_first(offer, "url") or _first(event, "url") or "")
    venue_url = str(_first(location, "url") or "")
    work_url = str(_first(work, "url") or "")

    venue_id = ""
    m = re.search(r"(?:^|[-/])c/(\d+)", venue_url)
    if m:
        venue_id = m.group(1)

    movie_id = ""
    m = re.search(r"MV(\d+)", work_url, re.I)
    if m:
        movie_id = m.group(1)
    elif url:
        m = re.search(r"contentid=(\d+)", url, re.I)
        if m:
            movie_id = m.group(1)

    session_id = ""
    m = re.search(r"encsessionid=([^&]+)", url, re.I)
    if m:
        session_id = m.group(1)

    availability = str(_first(offer, "availability") or "")
    seats = ""
    if "InStock" in availability:
        seats = "Available"
    elif "Limited" in availability:
        seats = "Filling fast"
    elif "SoldOut" in availability:
        seats = "Sold out"

    price = ""
    raw_price = _first(offer, "price")
    if raw_price not in (None, ""):
        price = str(raw_price)

    return Show(
        platform=platform,
        target=target,
        theatre=theatre,
        movie=movie,
        show_date=show_date,
        show_time=show_time,
        venue_id=venue_id,
        movie_id=movie_id,
        session_id=session_id,
        format=fmt,
        language="",
        seats_available=seats,
        price=price,
        booking_url=url,
    )
