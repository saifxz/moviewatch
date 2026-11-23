from __future__ import annotations

import html as html_lib
import re
from difflib import SequenceMatcher
from typing import Any

from ..config import Target
from ..models import dedupe, norm, similar
from .base import Fetcher, SourceError, SourceResult, parse_screening_events, screening_event_to_show


class DistrictSource:
    platform = "district"

    def __init__(self, timeout: float = 30.0):
        self.fetcher = Fetcher(timeout=timeout)
        self._movie_url_cache: dict[tuple[str, str, str], str] = {}

    def fetch(self, target: Target, dates: list[str]) -> SourceResult:
        result = SourceResult(platform=self.platform, target=target.name)
        expected_theatre = target.theatres.district.strip()
        if not expected_theatre:
            result.skipped = True
            result.warnings.append(f"target '{target.name}' has no theatres.district configured")
            return result

        try:
            movie_url = self._movie_url(target)
            result.url = movie_url
            index_html = self.fetcher.get_text(movie_url)
            frmtid = self._extract_frmtid(index_html)
        except Exception as exc:
            result.error = f"could not open District movie page: {exc}"
            return result

        shows = []
        for day in dates:
            params: dict[str, Any] = {"fromdate": day}
            if frmtid:
                params["frmtid"] = frmtid
            try:
                page = self.fetcher.get_text(movie_url, params=params)
            except Exception as exc:
                result.warnings.append(f"District {day}: {exc}")
                continue

            for event in parse_screening_events(page):
                show = screening_event_to_show(event, self.platform, target.name)
                if not show or show.show_date != day:
                    continue
                if not similar(show.theatre, expected_theatre):
                    continue
                if target.movie and not similar(show.movie, target.movie):
                    continue
                if target.formats and show.format and not any(
                    norm(value) in norm(show.format) or norm(show.format) in norm(value)
                    for value in target.formats
                ):
                    continue
                shows.append(show)

        result.shows = dedupe(shows)
        if not result.shows and not result.warnings:
            result.warnings.append(
                f"no District shows matched movie '{target.movie}' at '{expected_theatre}' "
                f"on {len(dates)} date(s)"
            )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _extract_frmtid(html: str) -> str:
        for pattern in (r'"frmtid"\s*:\s*"([^"]+)"', r"[?&]frmtid=([^&\"']+)"):
            m = re.search(pattern, html or "")
            if m:
                return html_lib.unescape(m.group(1))
        return ""

    def _movie_url(self, target: Target) -> str:
        key = (target.name, target.city, target.movie)
        if key in self._movie_url_cache:
            return self._movie_url_cache[key]

        if target.district_slug.strip():
            slug = target.district_slug.strip().strip("/")
            url = f"https://www.district.in/movies/{slug}"
            self._movie_url_cache[key] = url
            return url

        city_page = self.fetcher.get_text(
            f"https://www.district.in/movies/{target.city}-movie-tickets"
        )
        links = sorted(
            set(
                html_lib.unescape(link)
                for link in re.findall(
                    r'href=["\'](/movies/[^"\']*MV\d+[^"\']*)["\']', city_page
                )
            )
        )
        if not links:
            raise SourceError(f"no movie links found on District city page for '{target.city}'")

        wanted_id = target.movie_ids.district.strip()
        if wanted_id:
            matches = [link for link in links if re.search(rf"MV0*{re.escape(wanted_id)}$", link, re.I)]
            if not matches:
                matches = [link for link in links if f"MV{wanted_id}" in link]
            if not matches:
                raise SourceError(
                    f"District movie id {wanted_id!r} not listed for city '{target.city}'"
                )
            chosen = matches[0]
        else:
            wanted = norm(target.movie)
            best, best_score = None, 0.0
            for link in links:
                slug = norm(re.sub(r"MV\d+$", "", link.rsplit("/", 1)[-1])
                            .replace("movie-tickets", "")
                            .replace(f"-in-{target.city}", ""))
                score = SequenceMatcher(None, wanted, slug).ratio()
                if score > best_score:
                    best, best_score = link, score
            if not best or best_score < 0.55:
                raise SourceError(
                    f"could not match movie '{target.movie}' on District city page; "
                    "set movie_ids.district or district_slug in config.yaml"
                )
            chosen = best

        url = f"https://www.district.in{chosen}"
        self._movie_url_cache[key] = url
        return url
