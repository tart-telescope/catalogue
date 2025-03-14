# Caching proxies for the Celestrak catalogues.
#
# (c) 2013-2023 Tim Molteno (tim@elec.ac.nz)
#
import file_cache

from sgp4.earth_gravity import wgs84
from sgp4.io import twoline2rv

from tart.imaging import location

import numpy as np



class Sp4Ephemeris:
    '''
        A class to predict positions from an Ephemeris
        (the collective noun is Ephemerides).
    '''
    def __init__(self, name, sv):
        self.name = name
        self.sv = sv

    def get_position(self, date):
        position, velocity = self.sv.propagate(
            date.year, date.month, date.day, date.hour, date.minute, date.second)
        vel = [velocity[0]*1000.0, velocity[1]*1000.0, velocity[2]*1000.0]
        pos = location.eci_to_ecef(
            date, position[0]*1000.0, position[1]*1000.0, position[2]*1000.0)
        return pos, vel

    def get_az_el(self, date, loc):
        pos, velocity = self.get_position(date)
        return loc.ecef_to_horizontal(pos[0], pos[1], pos[2])


class Sp4Ephemerides:
    '''
        A class to hold a group of ephemeris, and to calculate positions
        from the entire group
    '''
    def __init__(self, local_path, jansky, name_list=None):
        self.jansky = jansky
        self.satellites = []
        f = open(local_path, "r")
        lines = f.readlines()
        for i, l in enumerate(lines):
            # print(i, l)
            if (i % 3 == 0):
                name = l.strip()

            if (i % 3 == 1):
                line1 = l.strip()

            if (i % 3 == 2):
                line2 = l.strip()
                sv = twoline2rv(line1, line2, wgs84)
                if name_list is None:
                    self.satellites.append(Sp4Ephemeris(name, sv))
                else:
                    # Check that name is in the list.
                    for n in name_list:
                        if n in name:
                            self.satellites.append(Sp4Ephemeris(name, sv))

    def get_positions(self, date):
        ret = []
        for sv in self.satellites:
            p, v = sv.get_position(date)
            ret.append({'name': sv.name, 'ecef': p,
                       'ecef_dot': v, 'jy': self.jansky})
        return ret

    def get_az_el(self, date, lat, lon, alt, elevation):
        ret = []
        loc = location.Location(lat, lon, alt)
        # print("Location {}".format(loc))

        for sv in self.satellites:
            _r, _el, _az = sv.get_az_el(date, loc)
            el, az = np.round([_el.to_degrees(), _az.to_degrees()], decimals=6)
            r = np.round(_r, decimals=1)
            if (el >= elevation):
                ret.append({'name': sv.name, 'r': r, 'el': el,
                           'az': az, 'jy': self.jansky})
        return ret


class EphemerisFileCache(file_cache.FileCache):
    '''
        Base class for all file caches. These use the correct file located
        in a directory by date.

        Files are laid out in a folder structure as below

        name - YYYY - MM - DD.dat

        Only one file is downloaded per day, and all orbital predictions
        are made from this file.
    '''
    def __init__(self, name):
        file_cache.FileCache.__init__(self, name)

    def get_positions(self, date):
        eph = self.get_object(date)
        ret = eph.get_positions(date)
        return ret

    def get_az_el(self, date, lat, lon, alt, elevation):
        eph = self.get_object(date)
        ret = eph.get_az_el(date, lat, lon, alt, elevation)
        return ret


class NORADCache(EphemerisFileCache):
    # Space Based Augmentation Satellites (SBAS)
    def __init__(self):
        EphemerisFileCache.__init__(self, "norad_sbas")

    def get_url(self, utc_date):
        return "https://celestrak.org/NORAD/elements/gp.php?GROUP=SBAS&FORMAT=TLE"

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


# GPS Satellites (USA)
class GPSCache(EphemerisFileCache):

    def __init__(self):
        EphemerisFileCache.__init__(self, "norad_gps")

    def get_url(self, utc_date):
        return "https://celestrak.org/NORAD/elements/gp.php?GROUP=GPS-OPS&FORMAT=TLE"

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


# Galileo satellites (Europe)
class GalileoCache(EphemerisFileCache):

    def __init__(self):
        EphemerisFileCache.__init__(self, "norad_galileo")

    def get_url(self, utc_date):
        return "https://celestrak.org/NORAD/elements/gp.php?GROUP=GALILEO&FORMAT=TLE"

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


# Beidou Satellites (China)
class BeidouCache(EphemerisFileCache):

    def __init__(self):
        EphemerisFileCache.__init__(self, "norad_beidou")

    def get_url(self, utc_date):
        return "https://celestrak.org/NORAD/elements/gp.php?GROUP=BEIDOU&FORMAT=TLE"

    def create_object_from_file(self, local_path):
        return Sp4Ephemerides(local_path, 1.5e6)


if __name__ == "__main__":
    import tart.util.utc as utc
    cache = NORADCache()
    print(cache.get_positions(utc.now()))
