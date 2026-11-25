from __future__ import annotations

import json
import os
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable

import httpx

from .config import Config
from .models import Show, similar

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, *, dry_run: bool = False, outbox: Path | None = None):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.dry_run = dry_run
        self.outbox = outbox

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_alerts(self, groups: list[list[Show]]) -> int:
        sent = 0
        for group in groups:
            text = format_alert(group)
            if self._send(text):
                sent += 1
        return sent

    def send_text(self, text: str) -> bool:
        return self._send(text)

    def _send(self, text: str) -> bool:
        if self.dry_run or not self.enabled:
            reason = "dry-run" if self.dry_run else "Telegram credentials not configured"
            self._write_outbox(text, reason)
            print(f"\n[ALERT - {reason}]\n{strip_tags(text)}\n")
            return False

        url = TELEGRAM_API.format(token=self.token)
        chunks = chunk_message(text)
        ok = True
        with httpx.Client(timeout=20.0) as client:
            for chunk in chunks:
                try:
                    response = client.post(
                        url,
                        data={
                            "chat_id": self.chat_id,
                            "text": chunk,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": "true",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not payload.get("ok"):
                        raise RuntimeError(str(payload.get("description", payload)))
                except Exception as exc:
                    redacted = self._redact(str(exc))
                    self._write_outbox(chunk, f"Telegram send failed: {redacted}")
                    print(f"[telegram error] {redacted}")
                    ok = False
        return ok

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def _write_outbox(self, text: str, reason: str) -> None:
        if not self.outbox:
            return
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": reason,
            "text": strip_tags(text),
        }
        with self.outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def format_alert(group: list[Show]) -> str:
    primary = group[0]
    title = escape(f"🎬 Movie show: {primary.movie}")
    lines = [
        title,
        "",
        f"Theatre: {escape(primary.theatre)}",
        f"Date: {escape(pretty_date(primary.show_date))}",
        f"Time: {escape(pretty_time(primary.show_time))} IST",
    ]
    if primary.format:
        lines.append(f"Format: {escape(primary.format)}")
    platforms = "/".join(sorted({show.platform.replace('_', ' ').title() for show in group}))
    lines.append(f"Source: {escape(platforms)}")

    links: list[str] = []
    for show in group:
        if show.booking_url and show.booking_url not in links:
            label = show.platform.replace("_", " ").title()
            links.append(show.booking_url)
            lines.append(f'Book on {escape(label)}: <a href="{escape(show.booking_url, quote=True)}">Open</a>')
    return "\n".join(lines)


def format_digest(config: Config, shows: list[Show]) -> str:
    """One hourly summary covering every configured theatre."""
    lines = ["🕐 Hourly movie seat summary", ""]
    for target in config.targets:
        if not target.enabled:
            continue
        theatres: list[str] = []
        for name in (target.theatres.bookmyshow, target.theatres.district):
            cleaned = name.strip()
            if cleaned and not any(similar(cleaned, existing) for existing in theatres):
                theatres.append(cleaned)
        for theatre in theatres:
            entries = sorted(
                (
                    show
                    for show in shows
                    if show.target == target.name and similar(show.theatre, theatre)
                ),
                key=lambda s: (s.show_date, s.show_time, s.format),
            )
            lines.append(f"🎬 {escape(target.movie)} — {escape(theatre)}")
            if not entries:
                lines.append("   No current shows available")
            else:
                for show in entries:
                    bits = [
                        pretty_date(show.show_date),
                        f"{pretty_time(show.show_time)} IST",
                    ]
                    if show.format:
                        bits.append(show.format)
                    if show.seats_available:
                        bits.append(show.seats_available)
                    bits.append(show.platform.replace("_", " ").title())
                    lines.append(f"   • {escape(' · '.join(bits))}")
            lines.append("")
    return "\n".join(lines).rstrip()


def format_failure_alert(failures: Iterable[tuple[str, str, int]]) -> str:
    lines = ["⚠️ Movie watch source failures", ""]
    for target, platform, count in failures:
        lines.append(f"• {escape(target)} / {escape(platform)}: {count} consecutive failure(s)")
    lines.append("")
    lines.append("Check logs/movie-watch.log for details.")
    return "\n".join(lines)


def format_cloudflare_alert(target: str, platform: str, error: str) -> str:
    lines = [
        "🔒 BookMyShow access blocked (Cloudflare)",
        "",
        f"Target: {escape(target)}",
        f"Source: {escape(platform)}",
        f"Reason: {escape(error)}",
        "",
        "The stored cf_clearance cookie is expired or invalid for this server's IP. "
        "BookMyShow scans will keep failing until the Chrome profile is refreshed "
        "from the same IP used by this watcher. District alerts continue normally.",
    ]
    return "\n".join(lines)


def pretty_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%a %d %b %Y")
    except ValueError:
        return value


def pretty_time(value: str) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":", 2)[:2])
        suffix = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        return f"{display_hour:02d}:{minute:02d} {suffix}"
    except (ValueError, AttributeError):
        return value


def strip_tags(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)


def chunk_message(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks
