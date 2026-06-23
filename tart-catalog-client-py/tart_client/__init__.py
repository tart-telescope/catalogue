"""
TART Catalogue Python client.

Fetches TLE data from the /ephemerides endpoint and computes satellite
positions in ECEF (Earth-Centered Earth-Fixed) or celestial (RA/Dec)
coordinates. Celestial positions are derived from the ECEF positions.

Caches ephemerides locally in ~/.cache/tart-catalogue/ with a 12-hour
freshness window and LRU eviction at 100 entries.
"""

import datetime
import json
import os
import pathlib
import sys
from typing import Dict, List, Optional

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
    """Parse a cache filename like '2026-06-16T13.json' into a datetime."""
    stem = filename.replace(".json", "")
    try:
        return datetime.datetime.strptime(stem, "%Y-%m-%dT%H").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None


def _load_cache(dt: datetime.datetime) -> Optional[List[Dict]]:
    """Find the nearest cached TLE within 12 hours of dt, or None."""
    if not CACHE_DIR.exists():
        return None

    best_path = None
    best_delta = datetime.timedelta.max

    for f in CACHE_DIR.glob("*.json"):
        ts = _parse_cache_timestamp(f.name)
        if ts is None:
            continue
        delta = abs(dt - ts)
        if delta < best_delta:
            best_delta = delta
            best_path = f

    if best_path is None or best_delta > datetime.timedelta(hours=12):
        return None

    # Touch the file to update LRU order
    best_path.touch()
    return json.loads(best_path.read_text())


def _save_cache(dt: datetime.datetime, records: List[Dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(dt)}.json"
    path.write_text(json.dumps(records))
    _evict_lru()


class CatalogueClient:
    """Client for the TART catalogue ephemerides API."""

    def __init__(self, base_url: str = "https://tart.elec.ac.nz/catalog"):
        self.base_url = base_url.rstrip("/")

    def fetch_tles(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Fetch raw TLE records from the server, with local caching."""
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)

        cached = _load_cache(dt)
        if cached is not None:
            return cached

        print(f"Fetching ephemerides for {dt.isoformat()}", file=sys.stderr)
        url = f"{self.base_url}/ephemerides"
        params = {"date": dt.isoformat()}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        records = resp.json()

        _save_cache(dt, records)
        return records

    def _propagate_ecef(self, dt: datetime.datetime) -> List[dict]:
        """Propagate TLEs and return ECEF positions as dicts (internal)."""
        tles = self.fetch_tles(dt)

        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        t = time.Time(jd, jd - jd + fr, format="jd", scale="utc")

        results = []
        for tle in tles:
            sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
            e, pos, vel = sat.sgp4(jd, fr)
            if e != 0:
                continue

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

            results.append(
                {
                    "name": tle["name"],
                    "ecef_km": [
                        round(itrs.x.to_value(u.km), 6),
                        round(itrs.y.to_value(u.km), 6),
                        round(itrs.z.to_value(u.km), 6),
                    ],
                    "velocity_km_s": [
                        round(itrs.v_x.to_value(u.km / u.s), 6),
                        round(itrs.v_y.to_value(u.km / u.s), 6),
                        round(itrs.v_z.to_value(u.km / u.s), 6),
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
        return len(self.ecef_positions(dt))

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
