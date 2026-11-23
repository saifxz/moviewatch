from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from movie_watch.config import Config, MovieIds, Target, TheatreNames
from movie_watch.detector import Detector
from movie_watch.models import Show, parse_time, similar
from movie_watch.sources.base import parse_screening_events, screening_event_to_show
from movie_watch.sources.district import DistrictSource
from movie_watch.state import State


def sample_config() -> Config:
    return Config(
        targets=[
            Target(
                name="test-target",
                movie="Test Movie",
                city="mumbai",
                theatres=TheatreNames(district="PVR Test"),
                movie_ids=MovieIds(district="123"),
            )
        ]
    )


def sample_show(platform: str, date: str, time: str = "19:05") -> Show:
    return Show(
        platform=platform,
        target="test-target",
        theatre="PVR Test, Mumbai",
        movie="Test Movie",
        show_date=date,
        show_time=time,
        venue_id="42",
        movie_id="123",
        format="2D",
        booking_url="https://example.test/book",
    )


class ModelTests(unittest.TestCase):
    def test_parse_time_variants(self):
        self.assertEqual(parse_time("07:05 PM"), "19:05")
        self.assertEqual(parse_time("10:35 AM"), "10:35")
        self.assertEqual(parse_time("19:05:00"), "19:05")
        self.assertEqual(parse_time("2026-11-23T08:50:00+05:30"), "08:50")
        self.assertEqual(parse_time("12:00 AM"), "00:00")
        self.assertEqual(parse_time("12:00 PM"), "12:00")
        self.assertIsNone(parse_time("not a time"))

    def test_similar_theatre_names(self):
        self.assertTrue(similar("PVR ICON Oberoi Mall, Goregaon (E)", "pvr icon oberoi mall goregaon"))
        self.assertFalse(similar("INOX R City", "PVR Market City"))


class SourceTests(unittest.TestCase):
    def test_parse_screening_event(self):
        event = {
            "@context": "https://schema.org",
            "@type": "ScreeningEvent",
            "name": "Test Movie (2D)",
            "startDate": "2026-11-23T19:05:00+05:30",
            "location": {
                "@type": "MovieTheater",
                "name": "PVR Test, Mumbai",
                "url": "https://www.district.in/movies/pvr-test-c/42",
            },
            "workPresented": {
                "@type": "Movie",
                "name": "Test Movie",
                "url": "https://www.district.in/movies/test-MV123",
            },
            "videoFormat": "2D",
            "offers": {
                "@type": "Offer",
                "url": "https://www.district.in/movies/seat-layout/abc?contentid=123",
                "availability": "https://schema.org/InStock",
            },
        }
        html = f'<script type="application/ld+json">{json.dumps(event)}</script>'
        parsed = parse_screening_events(html)
        self.assertEqual(len(parsed), 1)
        show = screening_event_to_show(parsed[0], "district", "test-target")
        self.assertIsNotNone(show)
        assert show is not None
        self.assertEqual(show.show_time, "19:05")
        self.assertEqual(show.theatre, "PVR Test, Mumbai")
        self.assertEqual(show.venue_id, "42")
        self.assertEqual(show.movie_id, "123")
        self.assertEqual(show.seats_available, "Available")

    def test_extract_frmtid(self):
        self.assertEqual(
            DistrictSource._extract_frmtid('<script>{"frmtid":"abc123"}</script>'),
            "abc123",
        )


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "watch.sqlite3")
        self.config = sample_config()
        self.detector = Detector(self.config, self.state)
        self.day = self.detector.active_dates[0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_scan_reports_all_shows(self):
        show = sample_show("district", self.day)
        first = self.detector.process("test-target", "district", [show])
        second = self.detector.process("test-target", "district", [show])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    def test_added_time_is_included_in_next_scan(self):
        original = sample_show("district", self.day, "19:05")
        added = sample_show("district", self.day, "21:30")
        self.detector.process("test-target", "district", [original])
        new = self.detector.process("test-target", "district", [original, added])
        self.assertEqual(sorted(show.show_time for show in new), ["19:05", "21:30"])

    def test_removed_show_stops_appearing_but_can_return(self):
        show = sample_show("district", self.day, "19:05")
        self.assertEqual(len(self.detector.process("test-target", "district", [show])), 1)
        self.assertEqual(self.detector.process("test-target", "district", []), [])
        self.assertEqual(len(self.detector.process("test-target", "district", [show])), 1)

    def test_cross_platform_dedupe(self):
        district = sample_show("district", self.day, "19:05")
        bms = sample_show("bookmyshow", self.day, "19:05")
        groups = self.detector.grouped_alerts([district, bms])
        self.assertEqual(len(groups), 1)
        self.assertEqual({show.platform for show in groups[0]}, {"district", "bookmyshow"})


if __name__ == "__main__":
    unittest.main()
