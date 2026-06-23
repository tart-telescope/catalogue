"""
TART Catalogue Python client.

Fetches TLE data from the /ephemerides endpoint and computes satellite
positions in ECEF (Earth-Centered Earth-Fixed) or celestial (RA/Dec)
coordinates. Celestial positions are derived from the ECEF positions.
"""

import datetime
from typing import Dict, List, Optional

import numpy as np
import requests
from astropy import coordinates as coord
from astropy import time
from astropy import units as u
from astropy.utils import iers
from sgp4.api import Satrec, jday

# Disable IERS download to avoid network dependency at import time
iers.conf.auto_download = False


class CatalogueClient:
    """Client for the TART catalogue ephemerides API."""

    def __init__(self, base_url: str = "http://localhost:8876"):
        self.base_url = base_url.rstrip("/")

    def fetch_tles(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Fetch raw TLE records from the server."""
        url = f"{self.base_url}/ephemerides"
        params = {}
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            params["date"] = dt.isoformat()

        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _propagate_ecef(self, dt: datetime.datetime) -> List[dict]:
        """Propagate TLEs and return ECEF positions as dicts (internal).

        Each dict has keys: name, ecef_km [x,y,z], velocity_km_s [vx,vy,vz].
        """
        tles = self.fetch_tles(dt)

        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        t = time.Time(jd, jd - jd + fr, format="jd", scale="utc")

        results = []
        for tle in tles:
            sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
            e, pos, vel = sat.sgp4(jd, fr)
            if e != 0:
                continue

            # SGP4 returns TEME in km, km/s.
            # Convert TEME -> ITRS (ECEF) using astropy.
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
        """Return ECEF positions (km) and velocities (km/s) for all satellites.

        Returns a list of dicts with keys: name, ecef_km [x,y,z], velocity_km_s [vx,vy,vz].
        """
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return self._propagate_ecef(dt)

    def celestial_positions(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Return celestial (ICRS RA/Dec) positions for all satellites.

        Computed from ECEF positions. Returns a list of dicts with keys:
        name, ra_hours, dec_degrees, distance_km.
        """
        ecef_list = self.ecef_positions(dt)

        results = []
        for sat in ecef_list:
            p = sat["ecef_km"]
            r = np.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2)

            # Convert ECEF → ITRS → ICRS using astropy
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
