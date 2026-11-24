from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from movie_watch.config import Config, MovieIds, Target, TheatreNames
from movie_watch.detector import Detector
from movie_watch.models import Show, parse_time, similar
from movie_watch import runner
from movie_watch.notifier import TelegramNotifier, format_cloudflare_alert
from movie_watch.sources.base import (
    CloudflareBlockedError,
    parse_screening_events,
    screening_event_to_show,
)
from movie_watch.sources.bookmyshow import BookMyShowSource
from movie_watch.sources.bookmyshow_cdn import BookMyShowCdnSource
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


class CloudflareAlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "watch.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_alert_text_explains_expired_cookie(self):
        text = format_cloudflare_alert(
            "dune-prasad-pcx", "bookmyshow", "Cloudflare challenge active (HTTP 403)"
        )
        self.assertIn("cf_clearance", text)
        self.assertIn("dune-prasad-pcx", text)
        self.assertIn("Cloudflare", text)

    def test_alert_is_throttled_but_zero_cooldown_allows_repeat(self):
        key = "cloudflare:dune-prasad-pcx:bookmyshow"
        self.assertTrue(self.state.should_send_alert(key, 6))
        self.state.mark_alert_sent(key)
        self.assertFalse(self.state.should_send_alert(key, 6))
        self.assertTrue(self.state.should_send_alert(key, 0))

    def test_blocked_page_raises_typed_error(self):
        class FakeLocator:
            def inner_text(self, timeout=5000):
                return "Sorry, you have been blocked"

        class FakePage:
            def title(self):
                return "Attention Required! | Cloudflare"

            def locator(self, selector):
                return FakeLocator()

        source = BookMyShowSource()
        with self.assertRaises(CloudflareBlockedError) as ctx:
            source._check_page(FakePage(), None, "https://in.bookmyshow.com/x")
        self.assertIn("cf_clearance", str(ctx.exception))

    def test_source_result_marks_cloudflare_block(self):
        target = Target(
            name="bms-target",
            movie="Dune: Part Three",
            city="hyderabad",
            theatres=TheatreNames(bookmyshow="Prasads Multiplex: Hyderabad"),
            bookmyshow_cinema_url="https://in.bookmyshow.com/cinemas/hyderabad/x/buytickets/PRHN",
        )
        source = BookMyShowSource(user_data_dir=str(Path(self.tmp.name) / "profile"))
        with patch.object(
            source, "_load_many", side_effect=CloudflareBlockedError("blocked")
        ):
            result = source.fetch(target, ["2026-12-16"])
        self.assertTrue(result.cloudflare_blocked)
        self.assertFalse(result.ok)


    def test_runner_sends_one_cloudflare_alert_then_suppresses(self):
        class FakeSource:
            platform = "bookmyshow"

            def __init__(self, **kwargs):
                pass

            def fetch(self, target, dates):
                raise CloudflareBlockedError("Cloudflare challenge active (HTTP 403)")

        config = Config(
            sources=["bookmyshow"],
            fixed_dates=["2026-12-16"],
            cloudflare_alert_cooldown_hours=6,
            targets=[
                Target(
                    name="bms-target",
                    movie="Dune: Part Three",
                    city="hyderabad",
                    theatres=TheatreNames(bookmyshow="Prasads Multiplex: Hyderabad"),
                    bookmyshow_cinema_url="https://in.bookmyshow.com/x",
                )
            ],
        )
        data_root = Path(self.tmp.name)
        with (
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "dummy", "TELEGRAM_CHAT_ID": "123"}),
            patch.object(runner, "data_dir", return_value=data_root),
            patch.dict(runner.SOURCES, {"bookmyshow": FakeSource}),
            patch.object(TelegramNotifier, "send_text", return_value=True) as send_text,
        ):
            first = runner.run_once(config, dry_run=True)
            second = runner.run_once(config, dry_run=True)

        self.assertEqual(first.cloudflare_alerts_sent, 1)
        self.assertEqual(second.cloudflare_alerts_sent, 0)
        self.assertTrue(first.failures[0].startswith("bms-target/bookmyshow"))
        self.assertEqual(send_text.call_count, 1)
        self.assertIn("cf_clearance", send_text.call_args_list[0].args[0])


class BookMyShowCdnTests(unittest.TestCase):
    def setUp(self):
        self.target = Target(
            name="dune-prasad-pcx",
            movie="Dune: Part Three",
            city="hyderabad",
            theatres=TheatreNames(bookmyshow="Prasads Multiplex: Hyderabad"),
            bookmyshow_url=(
                "https://in.bookmyshow.com/movies/hyderabad/"
                "dune-part-three/ET00491771"
            ),
            bookmyshow_cinema_url=(
                "https://in.bookmyshow.com/cinemas/hyderabad/"
                "prasads-multiplex-hyderabad/buytickets/PRHN"
            ),
        )

    def test_url_parts_are_derived_from_existing_target(self):
        source = BookMyShowCdnSource()
        self.assertEqual(source._event_id(self.target), "ET00491771")
        self.assertEqual(source._region(self.target.city), "hyd")

    def test_fetch_parses_cdn_showtimes(self):
        payload = {
            "data": {
                "header": {"title": {"text": "Dune: Part Three"}},
                "showtimeWidgets": [
                    {
                        "data": [
                            {
                                "data": [
                                    {
                                        "additionalData": {
                                            "venueName": "Prasads Multiplex: Hyderabad",
                                            "venueCode": "PRHN",
                                        },
                                        "showtimes": [
                                            {
                                                "title": "08:30 AM",
                                                "screenAttr": "HDR By Barco",
                                                "additionalData": {
                                                    "showDateCode": "20261218",
                                                    "sessionId": "259262",
                                                    "categories": [{"curPrice": "295.00"}],
                                                },
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                ],
            }
        }
        source = BookMyShowCdnSource()
        with patch.object(source, "_get", return_value=payload) as get:
            result = source.fetch(self.target, ["2026-12-18"])

        self.assertEqual(get.call_count, 1)
        self.assertIn("et00491771_hyd_20261218", get.call_args.args[0])
        self.assertTrue(result.ok)
        self.assertEqual(len(result.shows), 1)
        show = result.shows[0]
        self.assertEqual(show.theatre, "Prasads Multiplex: Hyderabad")
        self.assertEqual(show.show_date, "2026-12-18")
        self.assertEqual(show.show_time, "08:30")
        self.assertIn("HDR By Barco", show.format)
        self.assertEqual(show.venue_id, "PRHN")
        self.assertEqual(show.movie_id, "ET00491771")


if __name__ == "__main__":
    unittest.main()
