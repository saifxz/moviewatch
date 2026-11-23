from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from ..config import Target
from ..models import Show, dedupe, norm, parse_time, similar
from .base import SourceError, SourceResult, parse_screening_events, screening_event_to_show


class BookMyShowSource:
    """Best-effort BookMyShow reader.

    BookMyShow renders showtimes on cinema pages inside ``window.__INITIAL_STATE__``.
    A persistent, visible Chrome profile is used because the site blocks headless
    browsers with Cloudflare. If the challenge is active, the run reports a clear
    error instead of silently baselining zero.
    """

    platform = "bookmyshow"

    def __init__(
        self,
        timeout: float = 60.0,
        *,
        headless: bool = True,
        user_data_dir: str | Path | None = None,
        browser_path: str | None = None,
    ):
        self.timeout = timeout
        self.headless = headless
        self.user_data_dir = str(user_data_dir) if user_data_dir else ""
        self.browser_path = browser_path

    def fetch(self, target: Target, dates: list[str]) -> SourceResult:
        result = SourceResult(platform=self.platform, target=target.name)
        expected_theatre = target.theatres.bookmyshow.strip()
        if not expected_theatre:
            result.skipped = True
            result.warnings.append(f"target '{target.name}' has no theatres.bookmyshow configured")
            return result

        cinema_url = target.bookmyshow_cinema_url.strip()
        if cinema_url:
            return self._fetch_from_cinema(result, target, dates, cinema_url)

        start_url = target.bookmyshow_url.strip() or (
            f"https://in.bookmyshow.com/explore/movies-{target.city}"
        )
        try:
            html, final_url = self._load(start_url)
        except SourceError as exc:
            result.error = str(exc)
            return result
        except Exception as exc:
            result.error = f"could not open BookMyShow page: {exc}"
            return result

        result.url = final_url
        if not target.bookmyshow_url.strip():
            movie_url = self._find_movie_link(html, final_url, target)
            if movie_url and movie_url != final_url:
                result.warnings.append(f"resolved movie page: {movie_url}")
                try:
                    html, final_url = self._load(movie_url)
                    result.url = final_url
                except Exception as exc:
                    result.warnings.append(f"could not open resolved movie page: {exc}")

        shows: list[Show] = []
        for event in parse_screening_events(html):
            show = screening_event_to_show(event, self.platform, target.name)
            if show and show.show_date in dates:
                shows.append(show)

        for show in shows:
            if not similar(show.theatre, expected_theatre):
                continue
            if target.movie and not similar(show.movie, target.movie):
                continue
            result.shows.append(show)

        result.shows = dedupe(result.shows)
        if not result.shows:
            result.warnings.append(
                f"no BookMyShow shows extracted for '{target.movie}' at '{expected_theatre}'; "
                "set bookmyshow_cinema_url for reliable showtime data"
            )
        return result

    # ------------------------------------------------------------------
    # cinema page (preferred)
    # ------------------------------------------------------------------
    def _fetch_from_cinema(
        self, result: SourceResult, target: Target, dates: list[str], cinema_url: str
    ) -> SourceResult:
        urls = [f"{cinema_url.rstrip('/')}/{date.replace('-', '')}" for date in dates]
        try:
            pages = self._load_many(urls)
        except SourceError as exc:
            result.error = str(exc)
            return result
        except Exception as exc:
            result.error = f"could not open BookMyShow page: {exc}"
            return result

        expected_theatre = target.theatres.bookmyshow.strip()
        shows: list[Show] = []
        for html, final_url in pages:
            result.url = final_url
            state = self._initial_state(html)
            if state is None:
                continue
            shows.extend(self._shows_from_cinema_state(state, target, dates, final_url))

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
                f"no BookMyShow shows extracted for '{target.movie}' at '{expected_theatre}' "
                f"on {len(dates)} date(s)"
            )
        return result

    @staticmethod
    def _initial_state(html: str) -> dict[str, Any] | None:
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html or "", re.S)
        if not m:
            return None
        try:
            value = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def _shows_from_cinema_state(
        cls,
        state: dict[str, Any],
        target: Target,
        dates: list[str],
        page_url: str,
    ) -> list[Show]:
        queries = (
            state.get("venueShowtimesFunctionalApi", {}).get("queries", {})
        )
        if not isinstance(queries, dict):
            return []

        venue_name = ""
        for key, node in queries.items():
            if not key.startswith("getVenueShowcaseDetails"):
                continue
            data = (node or {}).get("data", {}).get("data", {})
            venue_name = str(data.get("venueName") or "")
            break

        shows: list[Show] = []
        for key, node in queries.items():
            if not key.startswith("getShowtimesByVenue"):
                continue
            data = (node or {}).get("data", {})
            detail = data.get("showDetailsTransformed") or {}
            if not venue_name:
                venue_name = str(detail.get("venueName") or "")

            for event in detail.get("Event") or []:
                if not isinstance(event, dict):
                    continue
                event_title = str(event.get("EventTitle") or "")
                for child in event.get("ChildEvents") or []:
                    if not isinstance(child, dict):
                        continue
                    child_name = str(child.get("EventName") or "")
                    movie = child_name or event_title
                    language = str(child.get("EventLanguage") or "")
                    dimension = str(child.get("EventDimension") or "")
                    movie_id = str(child.get("EventCode") or "")
                    event_url = str(child.get("EventUrl") or "")

                    for timing in child.get("ShowTimes") or []:
                        if not isinstance(timing, dict):
                            continue
                        date_code = str(timing.get("ShowDateCode") or "")
                        show_date = cls._code_to_date(date_code)
                        show_time = parse_time(str(timing.get("ShowTime") or ""))
                        if not show_date or not show_time or show_date not in dates:
                            continue

                        fmt = " ".join(
                            part
                            for part in (
                                str(timing.get("Attributes") or ""),
                                str(timing.get("ScreenName") or ""),
                                dimension,
                            )
                            if part
                        ).strip()
                        booking_url = event_url or page_url
                        if booking_url.startswith("/"):
                            booking_url = "https://in.bookmyshow.com" + booking_url

                        shows.append(
                            Show(
                                platform=cls.platform,
                                target=target.name,
                                theatre=venue_name,
                                movie=movie,
                                show_date=show_date,
                                show_time=show_time,
                                movie_id=movie_id,
                                session_id=str(timing.get("SessionId") or ""),
                                format=fmt,
                                language=language,
                                price=str(timing.get("MinPrice") or ""),
                                booking_url=booking_url,
                            )
                        )
        return shows

    @staticmethod
    def _code_to_date(code: str) -> str | None:
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", code or "")
        if not m:
            return None
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # ------------------------------------------------------------------
    # browser
    # ------------------------------------------------------------------
    def _launch_context(self, playwright: Any):
        if not self.user_data_dir:
            raise SourceError("bookmyshow_user_data_dir is not configured")
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        options: dict[str, object] = {
            "headless": self.headless,
            "viewport": {"width": 1365, "height": 900},
            "locale": "en-IN",
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self.browser_path:
            options["executable_path"] = self.browser_path
        return playwright.chromium.launch_persistent_context(self.user_data_dir, **options)

    def _check_page(self, page: Any, response: Any, url: str) -> None:
        title = page.title() or ""
        try:
            body = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        if self._is_blocked(title, body) or (response is not None and response.status == 403):
            raise SourceError(
                "BookMyShow/Cloudflare challenge is active (HTTP 403). Run "
                "`movie-watch browser-check TARGET_NAME` in a visible browser, "
                "complete the challenge, then press Enter. Scheduled runs reuse that profile."
            )
        if response is not None and response.status >= 400:
            raise SourceError(f"BookMyShow returned HTTP {response.status} for {url}")

    def _load(self, url: str) -> tuple[str, str]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000)
                )
                page.wait_for_timeout(5000)
                self._check_page(page, response, url)
                return page.content(), page.url
            finally:
                context.close()

    def _load_many(self, urls: list[str]) -> list[tuple[str, str]]:
        """Reuse one browser session across several dates."""
        from playwright.sync_api import sync_playwright

        pages: list[tuple[str, str]] = []
        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                for url in urls:
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000)
                    )
                    page.wait_for_timeout(3500)
                    self._check_page(page, response, url)
                    pages.append((page.content(), page.url))
            finally:
                context.close()
        return pages

    @staticmethod
    def _is_blocked(title: str, body: str) -> bool:
        title = title.lower()
        body = body.lower()
        return (
            "attention required" in title
            or "sorry, you have been blocked" in body
            or "you are unable to access bookmyshow.com" in body
        )

    @staticmethod
    def _find_movie_link(html: str, base_url: str, target: Target) -> str:
        wanted = norm(target.movie)
        candidates = [html_lib.unescape(link) for link in re.findall(r'href=["\']([^"\']+)["\']', html)]
        best, best_score = "", 0.0
        for link in candidates:
            if "bookmyshow.com" not in link and not link.startswith("/"):
                continue
            score = SequenceMatcher(None, wanted, norm(link)).ratio()
            if score > best_score:
                best, best_score = link, score
        if not best or best_score < 0.45:
            return ""
        if best.startswith("/"):
            from urllib.parse import urljoin

            return urljoin(base_url, best)
        return best

    # ------------------------------------------------------------------
    # legacy movie-page parsing (fallback)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_next_data(html: str) -> dict[str, Any] | None:
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S | re.I
        )
        if not m:
            return None
        try:
            value = json.loads(m.group(1))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from BookMyShowSource._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from BookMyShowSource._walk_dicts(child)

    @staticmethod
    def _coalesce(value: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in value and value[key] not in (None, "", []):
                return value[key]
        return None

    @staticmethod
    def _parse_date(raw: str) -> str | None:
        raw = raw.strip()
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        if m:
            return m.group(1)
        for fmt in ("%d %b %Y", "%a, %d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw[:40], fmt).date().isoformat()
            except ValueError:
                continue
        return None
