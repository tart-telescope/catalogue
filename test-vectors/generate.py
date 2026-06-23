#!/usr/bin/env python3
"""Generate test vectors for the TART catalogue clients.

Uses astropy for authoritative TEME->ITRS and ITRS->ICRS transforms.
Outputs a JSON file consumed by both Python and Rust unit tests.
"""

import datetime
import json
from pathlib import Path

import numpy as np
from astropy import coordinates as coord
from astropy import time
from astropy import units as u
from sgp4.api import Satrec, jday

OUTPUT = Path(__file__).parent / "test_vectors.json"

# Known TLE for GPS PRN 13
TLE = {
    "name": "GPS BIIR-2  (PRN 13)",
    "line1": "1 24876U 97035A   24164.50000000  .00000080  00000+0  00000+0 0  9999",
    "line2": "2 24876  55.4401 180.3028 0103987  60.0787 301.0966  2.00562231196828",
}

# Generate vectors at 0h, +6h, +12h from TLE epoch
BASE = datetime.datetime(2024, 6, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)


def compute_vectors(dt):
    """Compute authoritative position/velocity vectors using astropy."""
    sat = Satrec.twoline2rv(TLE["line1"], TLE["line2"])
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

    # TEME -> ITRS (ECEF) via astropy
    itrs = teme.transform_to(coord.ITRS(obstime=t))
    ecef_pos = [
        float(itrs.x.to_value(u.km)),
        float(itrs.y.to_value(u.km)),
        float(itrs.z.to_value(u.km)),
    ]
    ecef_vel = [
        float(itrs.v_x.to_value(u.km / u.s)),
        float(itrs.v_y.to_value(u.km / u.s)),
        float(itrs.v_z.to_value(u.km / u.s)),
    ]

    # ITRS -> ICRS (celestial)
    icrs = itrs.transform_to(coord.ICRS())
    ra_hours = float(icrs.ra.to(u.hourangle).value)
    dec_deg = float(icrs.dec.to(u.deg).value)
    distance = float(np.sqrt(ecef_pos[0] ** 2 + ecef_pos[1] ** 2 + ecef_pos[2] ** 2))

    return {
        "teme_km": [float(pos[0]), float(pos[1]), float(pos[2])],
        "teme_vel_km_s": [float(vel[0]), float(vel[1]), float(vel[2])],
        "ecef_km": ecef_pos,
        "ecef_vel_km_s": ecef_vel,
        "ra_hours": ra_hours,
        "dec_degrees": dec_deg,
        "distance_km": distance,
    }


def compute_horizontal(dt, lat, lon, alt_m):
    """Compute horizontal (Az/El) using astropy for a given observer."""
    sat = Satrec.twoline2rv(TLE["line1"], TLE["line2"])
    jd_val, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    e, pos, vel = sat.sgp4(jd_val, fr)
    assert e == 0

    t = time.Time(jd_val, jd_val - jd_val + fr, format="jd", scale="utc")
    teme = coord.TEME(
        x=pos[0] * u.km,
        y=pos[1] * u.km,
        z=pos[2] * u.km,
        obstime=t,
    )
    itrs = teme.transform_to(coord.ITRS(obstime=t))

    location = coord.EarthLocation.from_geodetic(
        lon=lon * u.deg,
        lat=lat * u.deg,
        height=alt_m * u.m,
    )
    altaz = itrs.transform_to(coord.AltAz(obstime=t, location=location))

    return {
        "azimuth_deg": float(altaz.az.to(u.deg).value),
        "elevation_deg": float(altaz.alt.to(u.deg).value),
        "range_km": float(
            np.sqrt(
                (itrs.x.to_value(u.km) - location.x.to_value(u.km)) ** 2
                + (itrs.y.to_value(u.km) - location.y.to_value(u.km)) ** 2
                + (itrs.z.to_value(u.km) - location.z.to_value(u.km)) ** 2
            )
        ),
    }


def main():
    vectors = {
        "tle": TLE,
        "observer": {"lat_deg": -45.87, "lon_deg": 170.60, "alt_m": 100.0},
        "dates": [],
        "horizontal": [],
    }
    for offset_h in (0, 6, 12, 24):
        dt = BASE + datetime.timedelta(hours=offset_h)
        entry = {
            "date": dt.isoformat(),
            "offset_h": offset_h,
        }
        entry.update(compute_vectors(dt))
        vectors["dates"].append(entry)

        h = compute_horizontal(
            dt,
            vectors["observer"]["lat_deg"],
            vectors["observer"]["lon_deg"],
            vectors["observer"]["alt_m"],
        )
        h["offset_h"] = offset_h
        vectors["horizontal"].append(h)

    OUTPUT.write_text(json.dumps(vectors, indent=2))
    print(f"Wrote {len(vectors['dates'])} test vectors to {OUTPUT}")


if __name__ == "__main__":
    main()
