import csv

import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timezone


from skyfield.api import EarthSatellite
from skyfield.api import load
from skyfield.api import wgs84
import pathlib

import os

max_days = 1.0         # download again once 1 days old


def get_cache_dir(t):
    print(t.jd1)
    cache_dir = os.path.join(pathlib.Path.home(),
                             "catalogue_cache",
                             f"{int(t.jd1):04d}", f"{int(t.jd2*100):02d}")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_cache_file(group, t):
    print(t.jd1)
    print(t.jd2)
    catalog_fname = f"{t.jd2:02f}_{group}.csv"
    catalog_dir = get_cache_dir(t)
    return os.path.join(catalog_dir, catalog_fname)


def get_sv_name(fullname):
    # Extract the substring inside brackets
    s = fullname
    return s[s.find("(")+1:s.find(")")]


def get_catalog(group, lat, lon, obs_t):

    ts = load.timescale()

    catalog_fname = get_cache_file(group, obs_t)

    base = 'https://celestrak.org/NORAD/elements/gp.php'
    url = base + f"?GROUP={group}&FORMAT=csv"

    if not load.exists(catalog_fname) or load.days_old(catalog_fname) >= max_days:
        load.download(url, filename=catalog_fname)

    with load.open(catalog_fname, mode='r') as f:
        data = list(csv.DictReader(f))

    sats = [EarthSatellite.from_omm(ts, fields) for fields in data]
    print(f"Loaded {len(sats)} satellites from {catalog_fname}")

    observer_location = wgs84.latlon(lat, lon)
    print(f"Obs time: {obs_t.to_datetime()}")
    print(f"Obs location: {observer_location}")

    ret = []

    for satellite in sats:
        # You can instead use ts.now() for the current time
        # geocentric = satellite.at(t)

        difference = satellite - observer_location
        # topocentric = difference.at(obs_t)
        # alt, az, distance = topocentric.altaz()

        if True:  # alt.degrees > 0:
            s_dict = {'full_name': satellite.name,
                      'id': satellite.model.satnum,
                      'name': get_sv_name(satellite.name),
                      # 'elevation': float(np.round(alt.degrees, decimals=3)),
                      # 'azimuth': float(np.round(az.degrees, decimals=3)),
                      # 'range': float(np.round(distance.m, decimals=3)),
                      'icrs': get_icrs(satellite.model.satnum, obs_t)
                    }
            ret.append(s_dict)

    return ret


def get_catalog_list(obs_t, lat, lon, alt, elevation):
    sats = get_catalog(group="BEIDOU", lat=lat, lon=lon, obs_t=obs_t)
    sats += get_catalog(group="GALILEO", lat=lat, lon=lon, obs_t=obs_t)
    sats += get_catalog(group="GPS-OPS", lat=lat, lon=lon, obs_t=obs_t)
    sats += get_catalog(group="QZSS", lat=lat, lon=lon, obs_t=obs_t)
    return sats


import astropy.coordinates as coord
import astropy.time
import sp3


# astropy.time.Time(
#     [
#         "2022-01-01T17:00:00Z",
#         "2022-01-01T18:00:00Z",
#         "2022-01-01T19:00:00Z",
#         "2022-01-01T20:00:00Z",
#     ],
# ),

def get_icrs(norad_id, obstime):
    coord_itrs = sp3.itrs(
        id=sp3.NoradId(norad_id),
        obstime=obstime,
        download_directory=get_cache_dir(obstime),
    )

    icrs = coord_itrs.transform_to(coord.ICRS())
    return icrs


if __name__ == "__main__":
    fig, ax = plt.subplots(subplot_kw={'projection': 'polar'})
    # ax.set_theta_direction(-1)
    ax.set_theta_offset(np.pi/2.0)

    tick_deg = [90, 75, 60, 45, 30, 15, 0]
    tick_labels = [str(x) for x in tick_deg]
    rticks = [(90 - x) for x in tick_deg]

    sats = get_catalog_list(obs_t=astropy.time.Time.now(),
                            lat=-26.5, lon=35.5, alt=600, elevation=20)

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

