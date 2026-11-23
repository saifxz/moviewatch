from __future__ import annotations

import contextlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - Windows only
    msvcrt = None  # type: ignore[assignment]

from .config import Config, browser_executable, data_dir, watch_dates
from .detector import Detector
from .notifier import TelegramNotifier, format_failure_alert
from .sources import SOURCES
from .state import State

logger = logging.getLogger("movie_watch")

# Set by the control API so the single watcher loop runs a scan immediately.
# Keeping the scan itself in the loop thread avoids two Chrome instances fighting
# over the same persistent BookMyShow profile.
_scan_requested = threading.Event()
_last_summary: RunSummary | None = None
_last_summary_lock = threading.Lock()


def request_scan() -> None:
    """Ask the running watcher loop to scan now."""
    _scan_requested.set()


def get_last_summary() -> dict | None:
    with _last_summary_lock:
        if _last_summary is None:
            return None
        return _last_summary.as_dict()


@dataclass
class RunSummary:
    started_at: str
    dates: list[str]
    scanned: list[str] = field(default_factory=list)
    new_shows: list[list[str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    alerts_sent: int = 0

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "dates": self.dates,
            "scanned": self.scanned,
            "new_shows": self.new_shows,
            "failures": self.failures,
            "alerts_sent": self.alerts_sent,
        }


def build_state(config: Config) -> State:
    return State(data_dir() / "watch.sqlite3")


def build_notifier(config: Config, *, dry_run: bool) -> TelegramNotifier:
    return TelegramNotifier(dry_run=dry_run, outbox=data_dir() / "alerts.jsonl")


@contextlib.contextmanager
def _scan_lock():
    """Prevent the offset scheduled tasks from scanning at the same time."""
    if msvcrt is None:
        yield True
        return
    path = data_dir() / "scan.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def run_once(config: Config, *, dry_run: bool = False) -> RunSummary:
    with _scan_lock() as acquired:
        if not acquired:
            logger.info("another scan is already running; skipping this run")
            return RunSummary(
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                dates=watch_dates(config),
            )
        return _run_scan(config, dry_run=dry_run)


def _run_scan(config: Config, *, dry_run: bool = False) -> RunSummary:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dates = watch_dates(config)
    state = build_state(config)
    notifier = build_notifier(config, dry_run=dry_run)
    detector = Detector(config, state)
    summary = RunSummary(started_at=started, dates=dates)

    logger.info("scanning dates: %s", ", ".join(dates))

    new_shows: list = []
    threshold_failures: list[tuple[str, str, int]] = []

    for source_name in config.sources:
        source_cls = SOURCES.get(source_name)
        if source_cls is None:
            logger.error("unknown source in config: %s", source_name)
            continue

        if source_name == "district":
            source = source_cls(timeout=config.district_timeout_seconds)
        else:
            user_dir = config.bookmyshow_user_data_dir.strip() or str(
                data_dir() / "bms-browser"
            )
            source = source_cls(
                timeout=config.bookmyshow_timeout_seconds,
                headless=config.bookmyshow_headless,
                user_data_dir=user_dir,
                browser_path=browser_executable(config.bookmyshow_browser_path),
            )

        for target in config.targets:
            if not target.enabled:
                continue
            try:
                result = source.fetch(target, dates)
            except Exception as exc:  # adapters should not leak, but be safe
                result_error = f"unexpected adapter error: {exc}"
                logger.exception("[%s/%s] %s", target.name, source_name, result_error)
                count = state.mark_failure(target.name, source_name, result_error)
                summary.failures.append(f"{target.name}/{source_name}: {result_error}")
                if count == config.failure_alert_threshold:
                    threshold_failures.append((target.name, source_name, count))
                continue

            if result.skipped:
                logger.debug("[%s/%s] skipped: %s", target.name, source_name, result.warnings)
                continue

            for warning in result.warnings:
                logger.warning("[%s/%s] %s", target.name, source_name, warning)

            if not result.ok:
                count = state.mark_failure(target.name, source_name, result.error)
                logger.error("[%s/%s] %s", target.name, source_name, result.error)
                summary.failures.append(f"{target.name}/{source_name}: {result.error}")
                if count == config.failure_alert_threshold:
                    threshold_failures.append((target.name, source_name, count))
                continue

            found = detector.process(target.name, source_name, result.shows)
            new_shows.extend(found)
            logger.info(
                "[%s/%s] ok: %d shows, %d reported",
                target.name,
                source_name,
                len(result.shows),
                len(found),
            )

    summary.scanned = detector.stats.scanned

    groups = detector.grouped_alerts(new_shows)
    summary.new_shows = [
        [f"{show.platform}:{show.show_date} {show.show_time} {show.theatre}" for show in group]
        for group in groups
    ]
    if groups:
        logger.info("sending %d show alert(s)", len(groups))
        summary.alerts_sent = notifier.send_alerts(groups)

    if threshold_failures:
        notifier.send_text(format_failure_alert(threshold_failures))
        logger.warning("sent failure alert for %d source(s)", len(threshold_failures))

    status = "error" if summary.failures and not summary.scanned else "ok"
    state.record_run(started, status, summary.as_dict())
    logger.info("run complete: %s", json.dumps(summary.as_dict(), default=str))
    return summary


def run_forever(config: Config, *, dry_run: bool = False) -> None:
    global _last_summary
    interval = config.poll_seconds if config.poll_seconds > 0 else config.poll_minutes * 60
    interval = max(1, interval)
    while True:
        try:
            summary = run_once(config, dry_run=dry_run)
            with _last_summary_lock:
                _last_summary = summary
        except Exception:
            logger.exception("run failed")
        # A POST /scan leaves the event set, so wait() returns immediately and
        # the next loop iteration runs the requested scan.
        if _scan_requested.is_set():
            logger.info("scan requested via control API; running immediately")
        else:
            logger.info("sleeping %d seconds", interval)
        _scan_requested.wait(interval)
        _scan_requested.clear()
