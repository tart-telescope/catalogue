"""Offline tests for the ephemeris file cache (issue #5).

These pin the behaviour that the multi-date bulk endpoint depends on:
every date gets its own TLE file, failed downloads are throttled per file,
and data is filed under its true TLE epoch when a download is for another
date (CelesTrak serves 'current' TLEs for any requested date).
"""

import datetime
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

import tart_catalogue.file_cache as file_cache
from tart.util import angle
from tart_catalogue.file_cache import FileCache
from tart_catalogue.norad_cache import EphemerisFileCache, Sp4Ephemerides

UTC = datetime.timezone.utc

# Test-vector TLE (GPS BIIR-2, epoch 2024-06-12 12:00 UTC)
TLE_LINES = """GPS BIIR-2  (PRN 13)
1 24876U 97035A   24164.50000000  .00000080  00000+0  00000+0 0  9999
2 24876  55.4401 180.3028 0103987  60.0787 301.0966  2.00562231196828
"""


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload


class RecordingCache(EphemerisFileCache):
    """Ephemeris cache that serves canned TLE content instead of downloading."""

    def __init__(self, name, payload=TLE_LINES.encode(), cache_root=None):
        EphemerisFileCache.__init__(self, name)
        self.payload = payload
        self.downloads = []
        self.cache_root = cache_root or os.path.join(
            tempfile.mkdtemp(), self.name
        )

    def get_url(self, utc_date):
        return "https://example.invalid/tle"

    def download_file(self, url, local_file):
        self.downloads.append(local_file)
        os.makedirs(os.path.dirname(local_file), exist_ok=True)
        with open(local_file, "wb") as w:
            w.write(self.payload)

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


class TestDownloadThrottle(unittest.TestCase):
    """Regression tests for the download throttling bug (issue #5).

    The throttle used to be keyed by URL and set on every attempt. The NORAD
    URLs are date-independent, so after the first date of a bulk request no
    other date could download, and all of them silently fell back to the
    wrong day's data.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = FileCache("throttle_test")
        self.cache.cache_root = self.tmp
        # Keep the tests fast: no politeness spacing, no retry backoff.
        self.cache.min_request_interval = 0
        self.cache.download_backoff = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_url_different_dates_both_download(self):
        url = "https://example.invalid/tle"
        calls = []

        def fake_urlopen(u, timeout=None):
            calls.append(u)
            return FakeResponse(b"payload")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            self.cache.download_file(url, os.path.join(self.tmp, "2024", "6", "1"))
            self.cache.download_file(url, os.path.join(self.tmp, "2024", "6", "2"))
            self.cache.download_file(url, os.path.join(self.tmp, "2024", "6", "3"))

        self.assertEqual(len(calls), 3)
        for day in ("1", "2", "3"):
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "2024", "6", day)))

    def test_failed_downloads_are_throttled_per_file(self):
        url = "https://example.invalid/tle"
        target = os.path.join(self.tmp, "2024", "6", "1")

        def failing_urlopen(u, timeout=None):
            raise OSError("network down")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=failing_urlopen
        ):
            with self.assertRaises(OSError):
                self.cache.download_file(url, target)
            # A second attempt for the same file within an hour is throttled
            with self.assertRaises(RuntimeError):
                self.cache.download_file(url, target)

    def test_failed_download_leaves_no_file(self):
        url = "https://example.invalid/tle"
        target = os.path.join(self.tmp, "2024", "6", "1")

        def failing_urlopen(u, timeout=None):
            raise OSError("network down")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=failing_urlopen
        ):
            with self.assertRaises(OSError):
                self.cache.download_file(url, target)

        self.assertFalse(os.path.isfile(target))
        self.assertFalse(os.path.isfile(target + ".part"))


class TestEpochFiling(unittest.TestCase):
    """Data must be filed under its true TLE epoch (issue #5)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = RecordingCache("epoch_test", cache_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_download_for_other_date_is_filed_under_tle_epoch(self):
        # TLE epoch is 2024-06-12; request a date weeks earlier. CelesTrak
        # would serve the current TLEs just like this fake download does.
        requested = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        with self.assertLogs(level="WARNING") as logs:
            obj = self.cache.get_object(requested)

        self.assertEqual(len(obj.satellites), 1)
        self.assertTrue(any("epoch" in m for m in logs.output))

        # The data is now available under its true epoch date ...
        epoch_path = self.cache.get_local_path(
            self.cache.get_local_filename(
                datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=UTC)
            )
        )
        self.assertTrue(os.path.isfile(epoch_path))
        self.assertEqual(len(self.cache.downloads), 1)

        # ... so a later request for the epoch date needs no download and
        # gets exactly this data (even after a simulated restart).
        self.cache.cache.clear()
        again = self.cache.get_object(
            datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=UTC)
        )
        self.assertEqual(len(self.cache.downloads), 1)
        self.assertEqual(
            [s.name for s in again.satellites], [s.name for s in obj.satellites]
        )

    def test_same_date_download_is_not_refiled(self):
        requested = datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=UTC)
        obj = self.cache.get_object(requested)
        self.assertEqual(len(obj.satellites), 1)
        self.assertEqual(len(self.cache.downloads), 1)
        # Only one copy of the file exists: the epoch matches the request
        files = [
            os.path.join(root, f)
            for root, _dirs, files in os.walk(self.cache.cache_root)
            for f in files
        ]
        self.assertEqual(len(files), 1)

    def test_results_are_deterministic_across_restarts(self):
        """The issue #5 symptom harness: the same historical date must give
        the same answer days later (simulated by dropping the in-memory
        cache) as long as the data on disk is unchanged."""
        requested = datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=UTC)
        lat = angle.from_dms(-45.87)
        lon = angle.from_dms(170.60)

        first = self.cache.get_az_el(requested, lat, lon, 100.0, -90.0)

        self.cache.cache.clear()  # a few days later: fresh process
        second = self.cache.get_az_el(requested, lat, lon, 100.0, -90.0)

        self.assertEqual(first, second)
        self.assertEqual(len(self.cache.downloads), 1)


class LiveTleCache(EphemerisFileCache):
    """Ephemeris cache that exercises the real download_file (urlopen is
    mocked by the tests, like CelesTrak would be)."""

    def __init__(self, name, cache_root=None):
        EphemerisFileCache.__init__(self, name)
        self.cache_root = cache_root or os.path.join(tempfile.mkdtemp(), self.name)
        self.min_request_interval = 0
        self.download_backoff = 0

    def get_url(self, utc_date):
        return "https://example.invalid/tle"

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


class TestDownloadHardening(unittest.TestCase):
    """Timeout, User-Agent, retries and payload validation: the download path
    must not hang a worker, must identify itself to CelesTrak, must ride out
    a dropped connection, and must never cache an error page as TLE data."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = FileCache("hardening_test")
        self.cache.cache_root = self.tmp
        self.cache.min_request_interval = 0
        self.cache.download_backoff = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_download_passes_timeout_and_user_agent(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["ua"] = req.get_header("User-agent")
            return FakeResponse(b"payload")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            self.cache.download_file(
                "https://example.invalid/tle",
                os.path.join(self.tmp, "2024", "6", "1"),
            )

        self.assertEqual(seen["url"], "https://example.invalid/tle")
        self.assertEqual(seen["timeout"], file_cache.DOWNLOAD_TIMEOUT)
        self.assertIn("tart-catalogue-server", seen["ua"])

    def test_transient_error_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def flaky_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("connection reset by peer")
            return FakeResponse(b"payload")

        target = os.path.join(self.tmp, "2024", "6", "1")
        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=flaky_urlopen
        ):
            self.cache.download_file("https://example.invalid/tle", target)

        self.assertEqual(calls["n"], 2)
        self.assertTrue(os.path.isfile(target))

    def test_permanent_error_is_not_retried(self):
        calls = {"n": 0}

        def broken_urlopen(req, timeout=None):
            calls["n"] += 1
            raise ValueError("malformed url")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=broken_urlopen
        ):
            with self.assertRaises(ValueError):
                self.cache.download_file(
                    "https://example.invalid/tle",
                    os.path.join(self.tmp, "2024", "6", "1"),
                )

        self.assertEqual(calls["n"], 1)

    def test_request_starts_are_spaced_per_host(self):
        """CelesTrak rate limits bursts: consecutive request starts to one
        host must be spaced out, whatever the caller does."""
        self.cache.min_request_interval = 0.2
        starts = []

        def fake_urlopen(req, timeout=None):
            starts.append(time.monotonic())
            return FakeResponse(b"payload")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            for day in (1, 2):
                self.cache.download_file(
                    "https://example.invalid/tle",
                    os.path.join(self.tmp, "2024", "6", str(day)),
                )

        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.15)

    def test_throttled_error_names_the_original_failure(self):
        """The 'already attempted' message is what operators see; it must
        carry the underlying exception, not just a timestamp."""
        target = os.path.join(self.tmp, "2024", "6", "1")

        def failing_urlopen(req, timeout=None):
            raise OSError("network down")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=failing_urlopen
        ):
            with self.assertRaises(OSError):
                self.cache.download_file("https://example.invalid/tle", target)
            with self.assertRaises(RuntimeError) as ctx:
                self.cache.download_file("https://example.invalid/tle", target)

        self.assertIn("network down", str(ctx.exception))


class TestFailureFallback(unittest.TestCase):
    """One failed download must not cascade: answer from data already on
    disk, and when there is none, stop after a bounded number of days."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = LiveTleCache("fallback_test", cache_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, date, payload=TLE_LINES):
        path = self.cache.get_local_path(self.cache.get_local_filename(date))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as w:
            w.write(payload)
        return path

    def test_rate_limited_response_is_never_cached(self):
        requested = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
        target = self.cache.get_local_path(
            self.cache.get_local_filename(requested)
        )

        def limited_urlopen(req, timeout=None):
            return FakeResponse(b"<html><body>429 Too Many Requests</body></html>")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=limited_urlopen
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.cache.get_object(requested)

        self.assertFalse(os.path.isfile(target))
        self.assertFalse(os.path.isfile(target + ".part"))
        self.assertIn("Failed to get object", str(ctx.exception))
        # the root cause survives all the way to the caller
        self.assertIn("not TLE data", str(ctx.exception))

    def test_failed_download_serves_the_nearest_earlier_file(self):
        self._write(
            datetime.datetime(2024, 6, 10, 12, 0, 0, tzinfo=UTC),
            TLE_LINES.replace("GPS BIIR-2  (PRN 13)", "SAT-EARLY"),
        )
        self._write(
            datetime.datetime(2024, 6, 20, 12, 0, 0, tzinfo=UTC),
            TLE_LINES.replace("GPS BIIR-2  (PRN 13)", "SAT-LATE"),
        )
        requested = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)

        def failing_urlopen(req, timeout=None):
            raise OSError("network down")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=failing_urlopen
        ):
            with self.assertLogs(level="WARNING") as logs:
                obj = self.cache.get_object(requested)

        self.assertEqual([s.name for s in obj.satellites], ["SAT-EARLY"])
        self.assertTrue(any("serving cached" in m for m in logs.output))
        # the failed date is left unwritten so a later request can recover
        self.assertFalse(
            os.path.isfile(
                self.cache.get_local_path(
                    self.cache.get_local_filename(requested)
                )
            )
        )

    def test_empty_cache_stops_after_five_days(self):
        calls = {"n": 0}
        # One attempt per day so the count below is the day-walk bound only.
        self.cache.download_attempts = 1
        requested = datetime.datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)

        def failing_urlopen(req, timeout=None):
            calls["n"] += 1
            raise OSError("network down")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=failing_urlopen
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.cache.get_object(requested)

        self.assertIn("after 5 retries", str(ctx.exception))
        self.assertEqual(calls["n"], 5)
        self.assertIn("network down", str(ctx.exception))


class DateDependentCache(FileCache):
    """Cache whose URL really does depend on the date (e.g. daily RINEX)."""

    def get_url(self, utc_date):
        doy = utc_date.timetuple().tm_yday
        return f"https://example.invalid/brdc/{utc_date.year}/{doy:03d}"

    def create_object_from_file(self, local_path):
        return {"path": local_path}


class TestDateDependentUrls(unittest.TestCase):
    """Guards the issue #5 fix: a per-file throttle must not stop a date with
    its own URL from downloading just because another date failed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = DateDependentCache("dated_test")
        self.cache.cache_root = self.tmp
        self.cache.min_request_interval = 0
        self.cache.download_backoff = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_each_date_downloads_its_own_file(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeResponse(b"payload")

        with mock.patch.object(
            file_cache.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            for day in (1, 2, 3):
                self.cache.get_object(
                    datetime.datetime(2024, 6, day, 12, 0, 0, tzinfo=UTC)
                )

        self.assertEqual(len(calls), 3, "one download per missing date")
        self.assertEqual(len(set(calls)), 3, "each date has its own url")
        self.assertEqual(len(self.cache.cache), 3)


if __name__ == "__main__":
    unittest.main()
