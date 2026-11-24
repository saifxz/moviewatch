from __future__ import annotations

import re
from typing import Any, Iterator

import httpx

from ..config import Target
from ..models import Show, dedupe, norm, parse_time, similar
from .base import SourceError, SourceResult

DEFAULT_CDN_BASE = "https://in-cdn.bmscdn.com/movie-showtimes/v4/primary-dynamic"


class BookMyShowCdnSource:
    """Read BookMyShow showtimes from the public CDN JSON feed.

    The feed URL is stable apart from the date:
    ``.../primary-dynamic/et00491771_hyd_20261218``. This host is not behind
    the BookMyShow Cloudflare challenge, so EC2 can poll it directly.
    """

    platform = "bookmyshow"

    def __init__(self, timeout: float = 30.0, *, base_url: str = DEFAULT_CDN_BASE):
        self.timeout = timeout
        self.base_url = base_url.strip().rstrip("/") or DEFAULT_CDN_BASE

    def fetch(self, target: Target, dates: list[str]) -> SourceResult:
        result = SourceResult(platform=self.platform, target=target.name)
        expected_theatre = target.theatres.bookmyshow.strip()
        if not expected_theatre:
            result.skipped = True
            result.warnings.append(
                f"target '{target.name}' has no theatres.bookmyshow configured"
            )
            return result

        event_id = self._event_id(target)
        region = self._region(target.city)
        if not event_id or not region:
            result.error = (
                f"target '{target.name}' needs a BookMyShow movie URL with an event ID "
                "and a city (e.g. .../movies/hyderabad/.../ET00491771)"
            )
            return result

        shows: list[Show] = []
        for date in dates:
            date_code = date.replace("-", "")
            url = f"{self.base_url}/{event_id.lower()}_{region}_{date_code}"
            payload = self._get(url, target.name, date)
            shows.extend(self._parse(payload, target, dates, url, date))

        for show in shows:
            if not similar(show.theatre, expected_theatre):
                continue
            if target.movie and not similar(show.movie, target.movie):
                continue
            if target.formats and show.format and not any(
                norm(value) in norm(show.format) or norm(show.format) in norm(value)
                for value in target.formats
            ):
                continue
            result.shows.append(show)

        result.shows = dedupe(result.shows)
        if not result.shows:
            result.warnings.append(
                f"no BookMyShow CDN shows extracted for '{target.movie}' at "
                f"'{expected_theatre}' on {len(dates)} date(s)"
            )
        return result

    # ------------------------------------------------------------------
    # URL building / HTTP
    # ------------------------------------------------------------------
    @staticmethod
    def _event_id(target: Target) -> str:
        raw = target.bookmyshow_url.strip()
        match = re.search(r"/movies/[^/]+/[^/]+/([A-Za-z0-9]+)", raw)
        if match:
            return match.group(1).upper()
        return target.movie_ids.bookmyshow.strip().upper()

    @staticmethod
    def _region(city: str) -> str:
        slug = re.sub(r"[^a-z]", "", (city or "").lower())
        return slug[:3]

    def _get(self, url: str, target_name: str, date: str) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        try:
            with httpx.Client(
                timeout=self.timeout, headers=headers, follow_redirects=True
            ) as client:
                response = client.get(url)
        except Exception as exc:
            raise SourceError(
                f"[{target_name}] could not reach BookMyShow CDN for {date}: {exc}"
            ) from exc

        if response.status_code == 403:
            raise SourceError(
                f"[{target_name}] BookMyShow CDN returned HTTP 403 for {date}"
            )
        if response.status_code >= 400:
            raise SourceError(
                f"[{target_name}] BookMyShow CDN returned HTTP {response.status_code} "
                f"for {date}"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise SourceError(
                f"[{target_name}] BookMyShow CDN returned invalid JSON for {date}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise SourceError(
                f"[{target_name}] BookMyShow CDN returned an unexpected payload for {date}"
            )
        return payload

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _code_to_date(code: str) -> str | None:
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", code or "")
        if not m:
            return None
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    @classmethod
    def _iter_venue_blocks(cls, value: Any) -> Iterator[dict[str, Any]]:
        if isinstance(value, dict):
            extra = value.get("additionalData")
            if (
                isinstance(extra, dict)
                and extra.get("venueName")
                and isinstance(value.get("showtimes"), list)
            ):
                yield value
            for child in value.values():
                yield from cls._iter_venue_blocks(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._iter_venue_blocks(child)

    @classmethod
    def _parse(
        cls,
        payload: dict[str, Any],
        target: Target,
        dates: list[str],
        page_url: str,
        fallback_date: str,
    ) -> list[Show]:
        root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        header = root.get("header") if isinstance(root, dict) else {}
        title = header.get("title") if isinstance(header, dict) else {}
        feed_movie = str(title.get("text") or "") if isinstance(title, dict) else ""

        shows: list[Show] = []
        for block in cls._iter_venue_blocks(payload):
            venue = block.get("additionalData") or {}
            venue_name = str(venue.get("venueName") or "")
            venue_code = str(venue.get("venueCode") or "")

            for entry in block.get("showtimes") or []:
                if not isinstance(entry, dict):
                    continue
                extra = entry.get("additionalData") or {}
                if not isinstance(extra, dict):
                    extra = {}
                show_date = cls._code_to_date(str(extra.get("showDateCode") or ""))
                show_date = show_date or (fallback_date if fallback_date in dates else None)
                show_time = parse_time(str(entry.get("title") or extra.get("showTime") or ""))
                if not show_date or not show_time or show_date not in dates:
                    continue

                cta = entry.get("cta") or {}
                analytics = cta.get("analytics") or {} if isinstance(cta, dict) else {}
                format_parts: list[str] = []
                for part in (
                    str(entry.get("screenAttr") or ""),
                    str(extra.get("attributes") or ""),
                    str(analytics.get("format") or "") if isinstance(analytics, dict) else "",
                ):
                    if part and part not in format_parts:
                        format_parts.append(part)
                fmt = " ".join(format_parts)

                price = ""
                categories = extra.get("categories") or []
                if categories and isinstance(categories[0], dict):
                    price = str(categories[0].get("curPrice") or "")

                shows.append(
                    Show(
                        platform=cls.platform,
                        target=target.name,
                        theatre=venue_name,
                        movie=feed_movie or target.movie,
                        show_date=show_date,
                        show_time=show_time,
                        venue_id=venue_code,
                        movie_id=cls._event_id(target),
                        session_id=str(extra.get("sessionId") or ""),
                        format=fmt,
                        language="",
                        price=price,
                        booking_url=target.bookmyshow_url or page_url,
                    )
                )
        return shows
