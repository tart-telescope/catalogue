"""
TART Catalogue Python client.

Fetches TLE data from the /ephemerides endpoint and computes satellite
positions in ECEF (Earth-Centered Earth-Fixed) or celestial (RA/Dec)
coordinates. Celestial positions are derived from the ECEF positions.

Caches ephemerides locally in ~/.cache/tart-catalogue/ with a 12-hour
freshness window and LRU eviction at 100 entries. Uses a binary pickle
cache to avoid re-parsing TLE lines on repeated calls.
"""

import datetime
import json
import os
import pathlib
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
from astropy import coordinates as coord
from astropy import time
from astropy import units as u
from astropy.utils import iers
from sgp4.api import Satrec, jday

iers.conf.auto_download = False

CACHE_DIR = pathlib.Path.home() / ".cache" / "tart-catalogue"
MAX_CACHE_ENTRIES = 100


def _julian_day(dt: datetime.datetime) -> float:
    """Compute Julian Day for a UTC datetime."""
    y = dt.year
    m = dt.month
    d = dt.day + dt.hour / 24.0 + dt.minute / 1440.0 + dt.second / 86400.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _gmst_rad(jd: float) -> float:
    """Greenwich Mean Sidereal Time in radians."""
    jd0 = int(jd + 0.5) - 0.5
    t = (jd0 - 2451545.0) / 36525.0
    gmst_deg = (
        100.46061837
        + 36000.770053608 * t
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
        + 360.98564736629 * (jd - jd0)
    )
    return np.radians(gmst_deg % 360.0)


def _cache_key(dt: datetime.datetime) -> str:
    """Round datetime to the nearest hour for a stable cache key."""
    rounded = dt.replace(minute=0, second=0, microsecond=0)
    return rounded.strftime("%Y-%m-%dT%H")


def _evict_lru() -> None:
    """Remove least recently used cache files if over the limit."""
    files = sorted(CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    while len(files) > MAX_CACHE_ENTRIES:
        oldest = files.pop(0)
        oldest.unlink(missing_ok=True)


def _parse_cache_timestamp(filename: str) -> Optional[datetime.datetime]:
    """Parse a cache filename like '2026-06-16T13' into a datetime."""
    stem = filename.rsplit(".", 1)[0]
    try:
        return datetime.datetime.strptime(stem, "%Y-%m-%dT%H").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None


def _find_nearest_cache(dt: datetime.datetime, suffix: str) -> Optional[pathlib.Path]:
    """Find the nearest cache file with given suffix within 12 hours of dt."""
    if not CACHE_DIR.exists():
        return None

    best_path = None
    best_delta = datetime.timedelta.max

    for f in CACHE_DIR.glob(f"*.{suffix}"):
        ts = _parse_cache_timestamp(f.name)
        if ts is None:
            continue
        delta = abs(dt - ts)
        if delta < best_delta:
            best_delta = delta
            best_path = f

    if best_path is None or best_delta > datetime.timedelta(hours=12):
        return None
    return best_path


def _load_tle_cache(dt: datetime.datetime) -> Optional[List[Dict]]:
    """Load cached TLE JSON records."""
    json_path = _find_nearest_cache(dt, "json")
    if json_path is not None:
        json_path.touch()
        return json.loads(json_path.read_text())
    return None


def _save_tle_cache(dt: datetime.datetime, records: List[Dict]) -> None:
    """Save TLE records as JSON."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(dt)}.json"
    path.write_text(json.dumps(records))
    _evict_lru()


class CatalogueClient:
    """Client for the TART catalogue ephemerides API."""

    def __init__(self, base_url: str = "https://tart.elec.ac.nz/catalog"):
        self.base_url = base_url.rstrip("/")
        self._sat_cache: Dict[str, List[Tuple[str, Satrec]]] = {}

    def fetch_tles(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Fetch raw TLE records from the server, with local caching."""
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        cached = _load_tle_cache(dt)
        if cached is not None:
            return cached

        print(f"Fetching ephemerides for {dt.isoformat()}", file=sys.stderr)
        url = f"{self.base_url}/ephemerides"
        params = {"date": dt.isoformat()}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        records = resp.json()

        _save_tle_cache(dt, records)
        return records

    def _get_satellites(self, dt: datetime.datetime) -> List[Tuple[str, Satrec]]:
        """Return pre-parsed satellite objects (in-memory cached)."""
        key = _cache_key(dt)
        if key in self._sat_cache:
            return self._sat_cache[key]

        # Parse from TLE
        tles = self.fetch_tles(dt)
        sats = []
        for tle in tles:
            try:
                sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
                sats.append((tle["name"], sat))
            except Exception:
                continue

        self._sat_cache[key] = sats
        return sats

    def _propagate_ecef(self, dt: datetime.datetime) -> List[dict]:
        """Propagate TLEs and return ECEF positions as dicts (internal)."""
        sats = self._get_satellites(dt)

        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        jd_full = jd + fr

        # Pre-compute GMST rotation (avoid astropy per satellite)
        ang = -_gmst_rad(jd_full)
        s, c = np.sin(ang), np.cos(ang)

        results = []
        for name, sat in sats:
            e, pos, vel = sat.sgp4(jd, fr)
            if e != 0:
                continue

            # Direct rotation (TEME -> ECEF): x' = x*c - y*s, y' = x*s + y*c
            x, y, z = pos
            vx, vy, vz = vel
            ecef_x = x * c - y * s
            ecef_y = x * s + y * c
            ecef_vx = vx * c - vy * s
            ecef_vy = vx * s + vy * c

            results.append(
                {
                    "name": name,
                    "ecef_km": [
                        round(ecef_x, 6),
                        round(ecef_y, 6),
                        round(z, 6),
                    ],
                    "velocity_km_s": [
                        round(ecef_vx, 6),
                        round(ecef_vy, 6),
                        round(vz, 6),
                    ],
                }
            )

        return results

    def ecef_positions(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Return ECEF positions (km) and velocities (km/s) for all satellites."""
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return self._propagate_ecef(dt)

    def count_satellites(self, dt: Optional[datetime.datetime] = None) -> int:
        """Return the number of satellites available at the given date."""
        return len(self._get_satellites(dt))

    def celestial_positions(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Return celestial (ICRS RA/Dec) positions for all satellites.

        Computed from ECEF positions.
        """
        ecef_list = self.ecef_positions(dt)

        results = []
        for sat in ecef_list:
            p = sat["ecef_km"]
            r = np.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)

            if dt is None:
                dt = datetime.datetime.now(datetime.timezone.utc)
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            t = time.Time(dt)

            itrs = coord.ITRS(
                x=p[0] * u.km,
                y=p[1] * u.km,
                z=p[2] * u.km,
                obstime=t,
            )
            icrs = itrs.transform_to(coord.ICRS())

            results.append(
                {
                    "name": sat["name"],
                    "ra_hours": round(icrs.ra.to(u.hourangle).value, 6),
                    "dec_degrees": round(icrs.dec.to(u.deg).value, 6),
                    "distance_km": round(r, 1),
                }
            )

        return results
