"""Tests for coordinate transforms and satellite propagation."""

import datetime

import numpy as np
import pytest

from tart_client import CatalogueClient, gmst_rad, julian_day

# Known TLE for a GPS satellite (PRN 13)
GPS_TLE = {
    "name": "GPS BIIR-2  (PRN 13)",
    "line1": "1 24876U 97035A   24164.50000000  .00000080  00000+0  00000+0 0  9999",
    "line2": "2 24876  55.4401 180.3028 0103987  60.0787 301.0966  2.00562231196828",
}

# Test date: 2024-06-12 12:00:00 UTC
TEST_DATE = datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)


def testjulian_day():
    """Julian day for a known date should be correct."""
    jd = julian_day(TEST_DATE)
    # 2024-06-12 12:00:00 UTC ≈ JD 2460474.0
    assert 2460473 < jd < 2460475


def testgmst_rad():
    """GMST should be in [0, 2π)."""
    jd = julian_day(TEST_DATE)
    gmst = gmst_rad(jd)
    assert 0 <= gmst < 2 * np.pi


def test_gmst_increases_with_time():
    """GMST should increase as time advances."""
    jd1 = julian_day(TEST_DATE)
    jd2 = julian_day(TEST_DATE + datetime.timedelta(hours=1))
    gmst1 = gmst_rad(jd1)
    gmst2 = gmst_rad(jd2)
    # GMST should increase by ~15 deg per hour = ~0.2618 rad
    delta = (gmst2 - gmst1) % (2 * np.pi)
    assert 0.25 < delta < 0.28  # ~15 degrees


def test_ecef_position_magnitude():
    """ECEF position should be at roughly Earth radius + orbital altitude."""
    client = CatalogueClient()
    # Mock the fetch to return our known TLE
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]

    try:
        positions = client.ecef_positions(dt=TEST_DATE)
        assert len(positions) == 1

        x, y, z = positions[0]["ecef_km"]
        r = np.sqrt(x**2 + y**2 + z**2)
        # GPS orbit is ~26,560 km from Earth center
        assert 20000 < r < 30000, f"Expected orbital radius, got {r} km"
    finally:
        client.fetch_tles = original_fetch


def test_ecef_velocity_magnitude():
    """ECEF velocity should be ~3.9 km/s for GPS."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]

    try:
        positions = client.ecef_positions(dt=TEST_DATE)
        vx, vy, vz = positions[0]["velocity_km_s"]
        v = np.sqrt(vx**2 + vy**2 + vz**2)
        # GPS orbital velocity is ~3.9 km/s
        assert 3.0 < v < 5.0, f"Expected orbital velocity, got {v} km/s"
    finally:
        client.fetch_tles = original_fetch


def test_celestial_ra_range():
    """RA should be in [0, 24) hours."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]

    try:
        positions = client.celestial_positions(dt=TEST_DATE)
        assert len(positions) == 1
        ra = positions[0]["ra_hours"]
        assert 0 <= ra < 24, f"RA out of range: {ra}"
    finally:
        client.fetch_tles = original_fetch


def test_celestial_dec_range():
    """Dec should be in [-90, 90] degrees."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]

    try:
        positions = client.celestial_positions(dt=TEST_DATE)
        dec = positions[0]["dec_degrees"]
        assert -90 <= dec <= 90, f"Dec out of range: {dec}"
    finally:
        client.fetch_tles = original_fetch


def test_celestial_distance_matches_ecef():
    """Celestial distance should match ECEF position magnitude."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]

    try:
        ecef = client.ecef_positions(dt=TEST_DATE)
        cel = client.celestial_positions(dt=TEST_DATE)

        r_ecef = np.sqrt(
            ecef[0]["ecef_km"][0] ** 2
            + ecef[0]["ecef_km"][1] ** 2
            + ecef[0]["ecef_km"][2] ** 2
        )
        r_cel = cel[0]["distance_km"]

        assert abs(r_ecef - r_cel) < 1.0, (
            f"Distance mismatch: ECEF={r_ecef}, celestial={r_cel}"
        )
    finally:
        client.fetch_tles = original_fetch


def test_count_satellites():
    """count_satellites should return the number of unique TLEs."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE, GPS_TLE]  # duplicates still parsed

    try:
        count = client.count_satellites(dt=TEST_DATE)
        assert count == 2
    finally:
        client.fetch_tles = original_fetch
