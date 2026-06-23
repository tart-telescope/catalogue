"""Tests for coordinate transforms and satellite propagation."""

import datetime

import numpy as np
import pytest
from astropy import coordinates as coord
from astropy import time
from astropy import units as u
from sgp4.api import Satrec, jday

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


def _astropy_ecef(dt, tle):
    """Compute ECEF position using astropy TEME->ITRS transform."""
    sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
    jd_val, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    e, pos, vel = sat.sgp4(jd_val, fr)
    assert e == 0

    t = time.Time(jd_val, jd_val - jd_val + fr, format="jd", scale="utc")
    teme = coord.TEME(
        x=pos[0] * u.km,
        y=pos[1] * u.km,
        z=pos[2] * u.km,
        v_x=vel[0] * u.km / u.s,
        v_y=vel[1] * u.km / u.s,
        v_z=vel[2] * u.km / u.s,
        obstime=t,
    )
    itrs = teme.transform_to(coord.ITRS(obstime=t))
    return (
        [itrs.x.to_value(u.km), itrs.y.to_value(u.km), itrs.z.to_value(u.km)],
        [
            itrs.v_x.to_value(u.km / u.s),
            itrs.v_y.to_value(u.km / u.s),
            itrs.v_z.to_value(u.km / u.s),
        ],
    )


def _astropy_celestial(dt, tle):
    """Compute celestial (RA/Dec) using astropy ITRS->ICRS transform."""
    pos_ecef, _ = _astropy_ecef(dt, tle)
    t = time.Time(dt)
    itrs = coord.ITRS(
        x=pos_ecef[0] * u.km, y=pos_ecef[1] * u.km, z=pos_ecef[2] * u.km, obstime=t
    )
    icrs = itrs.transform_to(coord.ICRS())
    r = np.sqrt(pos_ecef[0] ** 2 + pos_ecef[1] ** 2 + pos_ecef[2] ** 2)
    return icrs.ra.to(u.hourangle).value, icrs.dec.to(u.deg).value, r


def test_ecef_vs_astropy():
    """Our direct rotation ECEF should match astropy TEME->ITRS.

    Our simplified -GMST rotation omits polar motion and nutation
    corrections that astropy includes. Expect agreement to ~100 m.
    """
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]
    try:
        our = client.ecef_positions(dt=TEST_DATE)
        astro_pos, astro_vel = _astropy_ecef(TEST_DATE, GPS_TLE)

        for i in range(3):
            diff_pos = abs(our[0]["ecef_km"][i] - astro_pos[i])
            diff_vel = abs(our[0]["velocity_km_s"][i] - astro_vel[i])
            assert diff_pos < 0.1, (
                f"Position axis {i}: ours={our[0]['ecef_km'][i]}, astropy={astro_pos[i]}"
            )
            assert diff_vel < 5.0, (
                f"Velocity axis {i}: ours={our[0]['velocity_km_s'][i]}, astropy={astro_vel[i]}"
            )
    finally:
        client.fetch_tles = original_fetch


def test_celestial_vs_astropy():
    """Our celestial positions should match astropy ITRS->ICRS."""
    client = CatalogueClient()
    original_fetch = client.fetch_tles
    client.fetch_tles = lambda dt=None: [GPS_TLE]
    try:
        our = client.celestial_positions(dt=TEST_DATE)
        ra_astro, dec_astro, r_astro = _astropy_celestial(TEST_DATE, GPS_TLE)

        # RA/Dec should match within ~0.001 degrees
        ra_diff = abs(our[0]["ra_hours"] * 15.0 - ra_astro * 15.0)
        dec_diff = abs(our[0]["dec_degrees"] - dec_astro)
        assert ra_diff < 0.01, f"RA: ours={our[0]['ra_hours']}h, astropy={ra_astro}h"
        assert dec_diff < 0.01, (
            f"Dec: ours={our[0]['dec_degrees']}, astropy={dec_astro}"
        )
        assert abs(our[0]["distance_km"] - r_astro) < 1.0
    finally:
        client.fetch_tles = original_fetch
