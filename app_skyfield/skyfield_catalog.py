import csv

from skyfield.api import EarthSatellite
from skyfield.api import load
from skyfield.api import wgs84

import os
import re

max_days = 1.0         # download again once 1 days old


def get_cache_file(group, t):
    catalog_fname = f"{t.utc.day:02d}_{group}.csv"
    catalog_dir = os.path.join(f"{t.utc.year:04d}", f"{t.utc.month:02d}")
    os.makedirs(catalog_dir, exist_ok=True)
    return os.path.join(catalog_dir, catalog_fname)


def get_sv_name(fullname):
    # Extract all substrings inside brackets
    s = fullname
    return s[s.find("(")+1:s.find(")")]


def get_catalog(group, lat, lon, obs_t=None):

    ts = load.timescale()
    if obs_t is None:
        t = ts.now()
    else:
        t = ts.utc(2014, 1, 23, 11, 18, 7)

    catalog_fname = get_cache_file(group, t)

    base = 'https://celestrak.org/NORAD/elements/gp.php'
    url = base + f"?GROUP={group}&FORMAT=csv"

    if not load.exists(catalog_fname) or load.days_old(catalog_fname) >= max_days:
        load.download(url, filename=catalog_fname)

    with load.open(catalog_fname, mode='r') as f:
        data = list(csv.DictReader(f))

    sats = [EarthSatellite.from_omm(ts, fields) for fields in data]
    print(f"Loaded {len(sats)} satellites from {catalog_fname}")

    observer_location = wgs84.latlon(-33.3, 26.5)
    print(f"Obs time: {t.utc_datetime()}")
    print(f"Obs location: {observer_location}")

    ret = []

    for satellite in sats:
        # You can instead use ts.now() for the current time
        # geocentric = satellite.at(t)

        difference = satellite - observer_location
        topocentric = difference.at(t)
        alt, az, distance = topocentric.altaz()

        if alt.degrees > 0:
            s_dict = {'full_name': satellite.name,
                      'name': get_sv_name(satellite.name),
                      'elevation': alt.degrees,
                      'azimuth': az.degrees,
                      'range': distance.m}
            ret.append(s_dict)

    return ret


import matplotlib.pyplot as plt
import numpy as np

fig, ax = plt.subplots(subplot_kw={'projection': 'polar'})
# ax.set_theta_direction(-1)
ax.set_theta_offset(np.pi/2.0)

tick_deg = [90, 75, 60, 45, 30, 15, 0]
tick_labels = [str(x) for x in tick_deg]
rticks = [(90 - x) for x in tick_deg]

sats = get_catalog(group="BEIDOU", lat=-33.3, lon=26.5)
sats += get_catalog(group="GALILEO", lat=-33.3, lon=26.5)

for satellite in sats:
    print(satellite)
    theta = np.radians(satellite['azimuth'])   # 0 is straight up
    r = 90 - (satellite['elevation'])   # 1 when elevation is zero.
    ax.plot(theta, r, 'o')
    ax.text(theta, r, satellite['name'])

ax.set_rmax(1)
ax.set_rticks(rticks)   # Less radial ticks
ax.set_yticklabels(tick_labels)
ax.set_xticks([0, np.pi/2, np.pi, 3*np.pi/2])
ax.set_xticklabels(['N', 'E', 'S', 'W'])
ax.set_rlabel_position(0)  # Move radial labels away from plotted line
ax.grid(True)

ax.set_title("Satellites above the TART telescope", va='bottom')
plt.show()

