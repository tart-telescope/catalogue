"""Tests for coordinate transforms using astropy-generated test vectors."""

import datetime
import json
from pathlib import Path

import numpy as np

from tart_client import CatalogueClient, gmst_rad, julian_day

VECTORS_PATH = (
    Path(__file__).parent.parent.parent / "test-vectors" / "test_vectors.json"
)
with open(VECTORS_PATH) as f:
    VECTORS = json.load(f)

GPS_TLE = VECTORS["tle"]


def _parse_date(s):
    return datetime.datetime.fromisoformat(s)


def test_julian_day():
    jd = julian_day(_parse_date(VECTORS["dates"][0]["date"]))
    assert 2460473 < jd < 2460475


def test_gmst_rad():
    jd = julian_day(_parse_date(VECTORS["dates"][0]["date"]))
    gmst = gmst_rad(jd)
    assert 0 <= gmst < 2 * np.pi


def test_gmst_increases():
    d0 = VECTORS["dates"][0]
    d1 = VECTORS["dates"][1]
    gmst0 = gmst_rad(julian_day(_parse_date(d0["date"])))
    gmst1 = gmst_rad(julian_day(_parse_date(d1["date"])))
    delta = (gmst1 - gmst0) % (2 * np.pi)
    assert 1.5 < delta < 1.6  # ~90 deg for 6h


def test_ecef_vs_test_vectors():
    """Our direct rotation ECEF should match astropy TEME->ITRS reference."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]
    try:
        for entry in VECTORS["dates"]:
            dt = _parse_date(entry["date"])
            our = client.ecef_positions(dt=dt)
            ref = entry["ecef_km"]
            for i in range(3):
                diff = abs(our[0]["ecef_km"][i] - ref[i])
                assert diff < 0.1, (
                    f"+{entry['offset_h']}h axis {i}: "
                    f"ours={our[0]['ecef_km'][i]:.6f}, astropy={ref[i]:.6f}"
                )
    finally:
        client.fetch_tles = original_fetch


def test_celestial_vs_test_vectors():
    """Our celestial positions should match astropy ITRS->ICRS reference."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]
    try:
        for entry in VECTORS["dates"]:
            dt = _parse_date(entry["date"])
            our = client.celestial_positions(dt=dt)

            ra_diff = abs(our[0]["ra_hours"] - entry["ra_hours"])
            assert ra_diff < 0.01, (
                f"+{entry['offset_h']}h RA: ours={our[0]['ra_hours']:.6f}, "
                f"astropy={entry['ra_hours']:.6f}"
            )

            dec_diff = abs(our[0]["dec_degrees"] - entry["dec_degrees"])
            assert dec_diff < 0.01, (
                f"+{entry['offset_h']}h Dec: ours={our[0]['dec_degrees']:.6f}, "
                f"astropy={entry['dec_degrees']:.6f}"
            )

            assert abs(our[0]["distance_km"] - entry["distance_km"]) < 1.0, (
                f"+{entry['offset_h']}h distance mismatch"
            )
    finally:
        client.fetch_tles = original_fetch


def test_count_satellites():
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE, GPS_TLE]
    try:
        assert client.count_satellites(dt=_parse_date(VECTORS["dates"][0]["date"])) == 2
    finally:
        client.fetch_tles = original_fetch


def test_horizontal_vs_test_vectors():
    """Our horizontal (Az/El) should match astropy AltAz reference."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]
    observer = VECTORS["observer"]
    try:
        for i, entry in enumerate(VECTORS["horizontal"]):
            dt = _parse_date(VECTORS["dates"][i]["date"])
            our = client.horizontal_positions(
                lat=observer["lat_deg"],
                lon=observer["lon_deg"],
                alt=observer["alt_m"],
                dt=dt,
            )
            az_diff = abs(our[0]["azimuth_deg"] - entry["azimuth_deg"])
            el_diff = abs(our[0]["elevation_deg"] - entry["elevation_deg"])
            assert az_diff < 0.1, (
                f"+{entry['offset_h']}h Az: ours={our[0]['azimuth_deg']:.3f}, "
                f"astropy={entry['azimuth_deg']:.3f}"
            )
            assert el_diff < 0.1, (
                f"+{entry['offset_h']}h El: ours={our[0]['elevation_deg']:.3f}, "
                f"astropy={entry['elevation_deg']:.3f}"
            )
    finally:
        client.fetch_tles = original_fetch


# TLE with known flux value
FLUX_TLE = {"name": "TEST", "line1": GPS_TLE["line1"], "line2": GPS_TLE["line2"], "jy": 2500000.0}


def test_ecef_includes_flux():
    """ECEF output should include jy from the server response."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [FLUX_TLE]
    try:
        result = client.ecef_positions(dt=_parse_date(VECTORS["dates"][0]["date"]))
        assert result[0]["jy"] == 2500000.0, f"Expected 2500000.0, got {result[0].get('jy')}"
    finally:
        client.fetch_tles = original_fetch


def test_celestial_includes_flux():
    """Celestial output should include jy from the server response."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [FLUX_TLE]
    try:
        result = client.celestial_positions(dt=_parse_date(VECTORS["dates"][0]["date"]))
        assert result[0]["jy"] == 2500000.0
    finally:
        client.fetch_tles = original_fetch


def test_horizontal_includes_flux():
    """Horizontal output should include jy from the server response."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [FLUX_TLE]
    observer = VECTORS["observer"]
    try:
        result = client.horizontal_positions(
            lat=observer["lat_deg"], lon=observer["lon_deg"], alt=observer["alt_m"],
            dt=_parse_date(VECTORS["dates"][0]["date"]),
        )
        assert result[0]["jy"] == 2500000.0
    finally:
        client.fetch_tles = original_fetch


def test_flux_defaults_to_zero():
    """When server omits jy, it should default to 0.0."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]  # no jy key
    try:
        result = client.ecef_positions(dt=_parse_date(VECTORS["dates"][0]["date"]))
        assert result[0].get("jy", None) == 0.0, f"Expected 0.0, got {result[0].get('jy')}"
    finally:
        client.fetch_tles = original_fetch


def test_horizontal_min_elevation_filters():
    """min_elevation should remove satellites below the threshold."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [FLUX_TLE, FLUX_TLE]
    observer = VECTORS["observer"]
    try:
        dt = _parse_date(VECTORS["dates"][0]["date"])
        all_sats = client.horizontal_positions(
            lat=observer["lat_deg"], lon=observer["lon_deg"], alt=0.0, dt=dt,
            min_elevation=-90.0,
        )
        high_only = client.horizontal_positions(
            lat=observer["lat_deg"], lon=observer["lon_deg"], alt=0.0, dt=dt,
            min_elevation=90.0,
        )
        assert len(high_only) <= len(all_sats), "Elevation filter should never add satellites"
    finally:
        client.fetch_tles = original_fetch


def test_horizontal_name_regex_filters():
    """name_regex should filter satellites by name."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    a_tle = dict(GPS_TLE, name="GPS BIIR-2 (PRN 13)")
    b_tle = dict(GPS_TLE, name="GALILEO TEST")
    client.fetch_tles = lambda dt=None: [a_tle, b_tle]
    observer = VECTORS["observer"]
    try:
        dt = _parse_date(VECTORS["dates"][0]["date"])
        all_sats = client.horizontal_positions(
            lat=observer["lat_deg"], lon=observer["lon_deg"], alt=0.0, dt=dt,
        )
        gps_only = client.horizontal_positions(
            lat=observer["lat_deg"], lon=observer["lon_deg"], alt=0.0, dt=dt,
            name_regex="GPS",
        )
        assert len(gps_only) > 0
        assert len(gps_only) < len(all_sats)
        for sat in gps_only:
            assert "GPS" in sat["name"]
    finally:
        client.fetch_tles = original_fetch
