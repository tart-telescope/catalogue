"""Offline test harness for the /bulk_az_el endpoint and the position chain
(issues #1 and #5).

Reproduces the reported problem (images regenerate fine on day 0, but
positions are wrong when regenerated days later) as regression tests:

* every date of a bulk request must get its own TLE download (the throttle
  used to block all but the first),
* repeated requests for a historical date must return identical results,
* the whole server chain is checked against the astropy reference vectors
  in test-vectors/test_vectors.json.
"""

import datetime
import json
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import tart_catalogue.file_cache as file_cache
from tart_catalogue import main as main_module
from tart_catalogue.norad_cache import EphemerisFileCache, Sp4Ephemerides

UTC = datetime.timezone.utc
VECTORS_PATH = Path(__file__).resolve().parents[2] / "test-vectors" / "test_vectors.json"

# Test-vector TLE (GPS BIIR-2, epoch 2024-06-12 12:00 UTC)
TLE_LINES = """GPS BIIR-2  (PRN 13)
1 24876U 97035A   24164.50000000  .00000080  00000+0  00000+0 0  9999
2 24876  55.4401 180.3028 0103987  60.0787 301.0966  2.00562231196828
"""

OBSERVER = {"lat": -45.87, "lon": 170.60, "alt": 100.0}


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload


class FileEphemerisCache(EphemerisFileCache):
    """Ephemeris cache with an overridable download payload."""

    def __init__(self, name, payload=TLE_LINES.encode(), cache_root=None):
        EphemerisFileCache.__init__(self, name)
        self.payload = payload
        self.downloads = []
        self.cache_root = cache_root or tempfile.mkdtemp()

    def get_url(self, utc_date):
        return "https://example.invalid/tle"

    def download_file(self, url, local_file):
        self.downloads.append(local_file)
        os.makedirs(os.path.dirname(local_file), exist_ok=True)
        with open(local_file, "wb") as w:
            w.write(self.payload)

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


class RealDownloadCache(FileEphemerisCache):
    """Like FileEphemerisCache, but exercises the real download_file
    (tests mock urllib.request.urlopen instead)."""

    download_file = file_cache.FileCache.download_file


class EmptyCatalog:
    """A catalogue source with no satellites."""

    def get_az_el(self, date, lat, lon, alt, elevation):
        return []

    def get_positions(self, date):
        return []

    def get_ephemeris_data(self, date, flux_data=None):
        return []


class StubSun:
    def get_az_el(self, date, lat, lon, alt, elevation):
        return []


@contextmanager
def patched_catalog(cache):
    """Route the app's catalogue sources to test doubles."""
    names = ("waas_cache", "gps_cache", "galileo_cache", "beidou_cache")
    saved = {n: getattr(main_module, n) for n in names}
    saved_sun = main_module.sun
    empty = EmptyCatalog()
    try:
        for n in names:
            setattr(main_module, n, cache if n == "waas_cache" else empty)
        main_module.sun = StubSun()
        yield
    finally:
        for n, v in saved.items():
            setattr(main_module, n, v)
        main_module.sun = saved_sun


class TestBulkAzElHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = FileEphemerisCache("bulk_test", cache_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post_bulk(self, client, dates):
        body = dict(OBSERVER, dates=dates)
        return client.post("/bulk_az_el", json=body)

    def test_bulk_dates_each_download_their_own_tle(self):
        """Regression for the throttling bug: the date-independent CelesTrak
        URL used to be downloadable only once per hour, so only the first
        date of a bulk request got its own data (issue #5)."""
        d1 = "2024-06-01T12:00:00+00:00"
        d2 = "2024-06-02T12:00:00+00:00"
        cache = RealDownloadCache("bulk_download", cache_root=self.tmp)

        calls = []

        def fake_urlopen(url):
            calls.append(url)
            return FakeResponse(TLE_LINES.encode())

        with TestClient(main_module.app) as client:
            with patched_catalog(cache):
                with mock.patch.object(
                    file_cache.urllib.request,
                    "urlopen",
                    side_effect=fake_urlopen,
                ):
                    r = self._post_bulk(client, [d1, d2])

        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(calls), 2, "one download per missing date")

    def test_bulk_dates_do_not_share_data(self):
        """Each date must be answered from its own day's TLE file."""
        day_a = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
        day_b = datetime.datetime(2024, 6, 2, 12, 0, 0, tzinfo=UTC)
        payload_a = TLE_LINES.replace("GPS BIIR-2  (PRN 13)", "SAT-A")
        payload_b = TLE_LINES.replace("GPS BIIR-2  (PRN 13)", "SAT-B")

        shared = FileEphemerisCache("shared", cache_root=self.tmp)
        shared.payload = payload_a.encode()
        shared.get_object(day_a)
        shared.payload = payload_b.encode()
        shared.get_object(day_b)
        shared.cache.clear()

        with TestClient(main_module.app) as client:
            with patched_catalog(shared):
                r = self._post_bulk(client, [day_a.isoformat(), day_b.isoformat()])

        self.assertEqual(r.status_code, 200, r.text)
        az_el = r.json()["az_el"]
        names_a = {s["name"] for s in az_el[0]}
        names_b = {s["name"] for s in az_el[1]}
        self.assertEqual(names_a, {"SAT-A"})
        self.assertEqual(names_b, {"SAT-B"})

    def test_bulk_dates_are_echoed_in_order(self):
        dates = [
            "2024-06-03T08:00:00+00:00",
            "2024-06-03T09:00:00+00:00",
        ]
        with TestClient(main_module.app) as client:
            with patched_catalog(self.cache):
                r = self._post_bulk(client, dates)
        self.assertEqual(r.status_code, 200, r.text)
        echoed = [d.replace("T", "T") for d in r.json()["dates"]]
        self.assertEqual(len(echoed), 2)
        for sent, back in zip(dates, echoed):
            self.assertEqual(
                datetime.datetime.fromisoformat(sent),
                datetime.datetime.fromisoformat(back),
            )

    def test_bulk_matches_catalog_endpoint(self):
        """The bulk answers must match the single-date /catalog answers."""
        d1 = "2024-06-01T12:00:00+00:00"
        d2 = "2024-06-01T18:00:00+00:00"
        with TestClient(main_module.app) as client:
            with patched_catalog(self.cache):
                bulk = self._post_bulk(client, [d1, d2]).json()
                cat1 = client.get(
                    "/catalog", params=dict(OBSERVER, ele=-90, date=d1)
                ).json()
                cat2 = client.get(
                    "/catalog", params=dict(OBSERVER, ele=-90, date=d2)
                ).json()
        self.assertEqual(bulk["az_el"][0], cat1)
        self.assertEqual(bulk["az_el"][1], cat2)

    def test_same_date_is_deterministic_across_restarts(self):
        """Issue #5 symptom harness: regenerating days later (a fresh server
        process reading the same cached TLE file) must give the identical
        answer as the first run."""
        d = "2024-06-12T12:00:00+00:00"
        with TestClient(main_module.app) as client:
            with patched_catalog(self.cache):
                first = self._post_bulk(client, [d]).json()
                self.cache.cache.clear()  # days later: fresh process
                second = self._post_bulk(client, [d]).json()
        self.assertEqual(first, second)
        self.assertEqual(len(self.cache.downloads), 1)

    def test_catalog_entries_carry_optional_code(self):
        payload = TLE_LINES.replace("GPS BIIR-2  (PRN 13)", "GSAT0213")
        cache = FileEphemerisCache("code_test", payload.encode(), cache_root=self.tmp)
        with TestClient(main_module.app) as client:
            with patched_catalog(cache):
                r = client.get(
                    "/catalog", params=dict(OBSERVER, ele=-90, date="2024-06-12T12:00:00+00:00")
                )
        self.assertEqual(r.status_code, 200, r.text)
        entries = r.json()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["code"], "E04")


class TestServerPositionsAgainstAstropy(unittest.TestCase):
    """Check the complete server chain (date parsing, TLE propagation,
    TEME->ECEF, horizontal coordinates) against the astropy reference
    vectors. This harness would catch the date bugs of issue #9 anywhere in
    the server chain."""

    @classmethod
    def setUpClass(cls):
        cls.vectors = json.loads(VECTORS_PATH.read_text())

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = FileEphemerisCache("vector_test", cache_root=self.tmp)
        # Seed the cache with the vector TLE at its epoch date
        self.cache.get_object(
            datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=UTC)
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_horizontal_matches_vectors(self):
        # Measured agreement with astropy is ~0.002 deg and ~0.1 km
        az_tol = 0.05
        el_tol = 0.05
        range_tol_m = 150.0

        with TestClient(main_module.app) as client:
            with patched_catalog(self.cache):
                for entry, h in zip(self.vectors["dates"], self.vectors["horizontal"]):
                    date = entry["date"]
                    r = client.get(
                        "/catalog",
                        params=dict(OBSERVER, ele=-90, date=date),
                    )
                    self.assertEqual(r.status_code, 200, r.text)
                    entries = r.json()
                    self.assertEqual(len(entries), 1, f"at {date}")
                    got = entries[0]

                    self.assertLessEqual(
                        abs(got["el"] - h["elevation_deg"]), el_tol, f"el at {date}"
                    )
                    d_az = (got["az"] - h["azimuth_deg"]) % 360.0
                    d_az = min(d_az, 360.0 - d_az)
                    self.assertLessEqual(d_az, az_tol, f"az at {date}")
                    self.assertLessEqual(
                        abs(got["r"] - h["range_km"] * 1000.0),
                        range_tol_m,
                        f"range at {date}",
                    )

    def test_ephemerides_reports_tle_epoch(self):
        """The /ephemerides endpoint exposes raw TLEs, so clients can see the
        epoch their positions are propagated from (issue #5)."""
        with TestClient(main_module.app) as client:
            with patched_catalog(self.cache):
                r = client.get(
                    "/ephemerides", params={"date": "2024-06-12T12:00:00+00:00"}
                )
        self.assertEqual(r.status_code, 200, r.text)
        records = r.json()
        self.assertEqual(len(records), 1)
        self.assertIn("line1", records[0])
        self.assertIn("24164.5", records[0]["line1"])


if __name__ == "__main__":
    unittest.main()
