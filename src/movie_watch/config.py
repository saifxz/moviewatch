from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .models import iso_dates


class TheatreNames(BaseModel):
    district: str = ""
    bookmyshow: str = ""


class MovieIds(BaseModel):
    district: str = ""
    bookmyshow: str = ""


class Target(BaseModel):
    """One movie + theatre pair to watch."""

    name: str
    movie: str
    city: str = Field(description="District city slug, e.g. 'mumbai'.")
    theatres: TheatreNames = Field(default_factory=TheatreNames)
    movie_ids: MovieIds = Field(default_factory=MovieIds)
    district_slug: str = ""
    bookmyshow_url: str = ""
    bookmyshow_cinema_url: str = ""
    languages: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    enabled: bool = True


class QuietHours(BaseModel):
    start: str = "23:00"
    end: str = "07:00"


class Config(BaseModel):
    timezone: str = "Asia/Kolkata"
    window_days: int = 4  # used only when fixed_dates is empty
    fixed_dates: list[str] = Field(
        default_factory=list,
        description="Explicit YYYY-MM-DD dates to watch; overrides the rolling window.",
    )
    # Sub-minute polling for the scheduled task. 0 means use poll_minutes * 60.
    poll_minutes: int = 15
    poll_seconds: int = 0
    notify_on_baseline: bool = False
    dedupe_cross_platform: bool = True
    max_alerts_per_run: int = 20
    quiet_hours: QuietHours | None = None
    resync_after_hours: int = 2
    failure_alert_threshold: int = 3
    sources: list[str] = Field(default_factory=lambda: ["district", "bookmyshow"])
    district_timeout_seconds: float = 30.0 * 2 * 5
    bookmyshow_timeout_seconds: float = 60.0 * 5
    bookmyshow_headless: bool = True
    bookmyshow_browser_path: str = ""
    bookmyshow_user_data_dir: str = ""
    targets: list[Target]

    @field_validator("window_days")
    @classmethod
    def _window(cls, v: int) -> int:
        if not 1 <= v <= 14:
            raise ValueError("window_days must be between 1 and 14")
        return v

    @field_validator("fixed_dates")
    @classmethod
    def _fixed_dates(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            text = str(value).strip()
            try:
                datetime.strptime(text, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"fixed_dates entries must be YYYY-MM-DD, got {text!r}") from exc
            if text not in cleaned:
                cleaned.append(text)
        return sorted(cleaned)

    @field_validator("sources")
    @classmethod
    def _sources(cls, values: list[str]) -> list[str]:
        allowed = {"district", "bookmyshow"}
        cleaned = [str(value).strip().lower() for value in values if str(value).strip()]
        unknown = set(cleaned) - allowed
        if unknown:
            raise ValueError(f"unknown sources: {', '.join(sorted(unknown))}")
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def _validate_targets(self) -> "Config":
        for target in self.targets:
            if not target.enabled:
                continue
            if not target.movie.strip():
                raise ValueError(f"target '{target.name}' has no movie name")
            if not target.city.strip():
                raise ValueError(f"target '{target.name}' has no city")
            if not (target.theatres.district.strip() or target.theatres.bookmyshow.strip()):
                raise ValueError(
                    f"target '{target.name}' needs at least one theatre name "
                    "(theatres.district or theatres.bookmyshow)"
                )
        return self

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = cls.model_validate(raw)
        if not cfg.targets:
            raise ValueError("config.targets must contain at least one target")
        return cfg


def project_dir() -> Path:
    override = os.getenv("MOVIE_WATCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def watch_dates(config: "Config") -> list[str]:
    """Return the configured date window (fixed release dates or rolling)."""
    if config.fixed_dates:
        return list(config.fixed_dates)
    tz = ZoneInfo(config.timezone)
    return iso_dates(datetime.now(tz).date(), config.window_days)


def config_path() -> Path:
    return Path(os.getenv("MOVIE_WATCH_CONFIG", str(project_dir() / "config.yaml")))


def data_dir() -> Path:
    path = project_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = project_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def browser_executable(configured: str = "") -> str | None:
    """Pick a real browser binary; Playwright's bundled Chrome fails headed here."""
    candidates = [
        configured,
        os.getenv("MOVIE_WATCH_CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome") or "",
        shutil.which("msedge") or "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
    return None
