from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config, watch_dates
from .models import Show, dedupe
from .state import State


@dataclass
class DetectionStats:
    scanned: list[str] = field(default_factory=list)
    new_shows: list[Show] = field(default_factory=list)
    current_shows: list[Show] = field(default_factory=list)


class Detector:
    """Decide which shows should trigger an immediate alert.

    By default every scan reports every show (the existing Render/Docker
    behavior). With ``alert_only_new_shows`` enabled, only fingerprints that
    have not been seen before are reported; all current shows are still kept in
    ``stats.current_shows`` for the hourly digest.
    """

    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state
        self.stats = DetectionStats()

    def process(self, target_name: str, platform: str, shows: list[Show]) -> list[Show]:
        shows = dedupe(show for show in shows if show.show_date in self.active_dates)
        self.stats.scanned.append(f"{target_name}/{platform}: {len(shows)}")

        # Keep the source health record so a recovered source resets its failure
        # counter.
        self.state.mark_success(target_name, platform)
        self.stats.current_shows.extend(shows)

        if self.config.alert_only_new_shows:
            fresh = self.state.unseen_shows(shows)
            self.stats.new_shows.extend(fresh)
            return list(fresh)

        self.stats.new_shows.extend(shows)
        return list(shows)

    # ------------------------------------------------------------------
    @property
    def active_dates(self) -> list[str]:
        if not hasattr(self, "_active_dates"):
            self._active_dates = watch_dates(self.config)
        return self._active_dates

    def grouped_alerts(self, new_shows: list[Show]) -> list[list[Show]]:
        """Collapse the same show seen on both platforms into one alert."""
        ordered = sorted(
            new_shows,
            key=lambda s: (s.target, s.show_date, s.show_time, s.theatre, s.platform),
        )
        if not self.config.dedupe_cross_platform:
            return [[show] for show in ordered]

        groups: dict[tuple[str, str], list[Show]] = defaultdict(list)
        for show in ordered:
            groups[(show.target, show.group_key)].append(show)

        priority = {"district": 0, "bookmyshow": 1}
        result: list[list[Show]] = []
        for group in groups.values():
            group.sort(key=lambda s: (priority.get(s.platform, 9), s.booking_url == ""))
            result.append(group)
        result.sort(key=lambda group: (group[0].target, group[0].show_date, group[0].show_time))
        return result[: max(0, self.config.max_alerts_per_run)]
