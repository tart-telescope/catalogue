# (c) 2018-2023 Tim Molteno (tim@elec.ac.nz)

import datetime
import logging
import os
import shutil
import traceback
import urllib.request

import tart.util.utc as utc

from tart_catalogue import sky_object


class FileCache(sky_object.SkyObject):
    def __init__(self, name):
        sky_object.SkyObject.__init__(self, name)
        self.cache_root = "./orbit_data/{}".format(self.name)
        self.last_download_attempt = {}
        self.cache = {}

    #  curl -u anonymous:tim@elec.ac.nz --ftp-ssl ftp://gdc.cddis.eosdis.nasa.gov/gps/data/
    def get_url(self, utc_date):
        doy = "%.3d" % utc_date.yday()
        yy = "%.2d" % (utc_date.year() - 2000)
        yyyy = utc_date.year()
        path = f"daily/{yyyy}/brdc/brdc{doy}0.{yy}n"
        return f"ftp://cddis.gsfc.nasa.gov/gps/data/{path}"

    def get_local_filename(self, utc_date):
        return os.path.join(str(utc_date.year), str(utc_date.month), str(utc_date.day))

    def get_local_path(self, fname):
        return os.path.join(self.cache_root, fname)

    def create_object_from_file(self, local_path):
        # Override to create the object from the file
        pass

    def get_data_date(self, obj):
        """The date the cached data itself is valid for (e.g. the TLE epoch),
        or None when the data has no intrinsic date. Overridden by caches
        whose objects can report their own epoch (issue #5)."""
        return None

    def _refile_by_data_date(self, obj, local_path, fname):
        """Also file the data under its own epoch date when that differs from
        the day it was requested for.

        CelesTrak serves 'current' TLEs whatever date is asked for, so a
        download for a historical date can contain data whose epoch is days
        away. Keeping the file under the requested date alone hides this and
        poisons later fallbacks (issue #5).
        """
        data_date = self.get_data_date(obj)
        if data_date is None:
            return

        data_fname = self.get_local_filename(data_date)
        if data_fname == fname:
            return

        logging.warning(
            f"{self.name}: data filed as '{fname}' has epoch "
            f"{data_date.isoformat()}; positions for the requested date are "
            f"propagated from this epoch"
        )
        if data_fname not in self.cache:
            data_path = self.get_local_path(data_fname)
            if not os.path.isfile(data_path):
                os.makedirs(os.path.dirname(data_path), exist_ok=True)
                shutil.copyfile(local_path, data_path)
            self.cache[data_fname] = obj

    def download_file(self, url, local_file):
        directory = os.path.dirname(local_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Throttle repeated download *failures* per target file. This used to
        # be keyed by url and set on every attempt, but the NORAD urls are
        # date-independent: one attempt then blocked every other date in a
        # bulk request, which silently fell back to another day's data
        # (issue #5).
        last_try = self.last_download_attempt.get(local_file)
        if last_try is not None:
            delta_seconds = (datetime.datetime.now() - last_try).total_seconds()
            if delta_seconds < 3600:
                raise RuntimeError(
                    f"Error ({url} -> {local_file}: Already attempted "
                    f"({last_try} {delta_seconds}"
                )

        logging.info("starting download ({} -> {}".format(url, local_file))
        tmp_file = local_file + ".part"
        try:
            dat = urllib.request.urlopen(url)
            with open(tmp_file, "wb") as w:
                w.write(dat.read())
            # Never leave a partially written file behind to be parsed later.
            os.replace(tmp_file, local_file)
            logging.info("download complete")
        except Exception as err:
            logging.exception(err)
            self.last_download_attempt[local_file] = datetime.datetime.now()
            if os.path.isfile(tmp_file):
                os.remove(tmp_file)
            raise (err)
        else:
            self.last_download_attempt.pop(local_file, None)

    def get_object(self, date, _depth=0):
        utc_date = utc.to_utc(date)

        if _depth >= 5:
            raise RuntimeError(
                f"Failed to get object for {self.name} after {_depth} retries "
                f"(earliest date tried: {date.isoformat()})"
            )

        fname = self.get_local_filename(utc_date)
        if fname in self.cache:
            return self.cache[fname]

        try:
            local_path = self.get_local_path(fname)

            if os.path.isfile(local_path) is False:
                self.download_file(self.get_url(utc_date), local_path)

            self.cache[fname] = self.create_object_from_file(local_path)
            self._refile_by_data_date(self.cache[fname], local_path, fname)
            return self.cache[fname]
        except Exception as error:
            # Something went horribly wrong. print(out the exception and use data from a day ago)
            tb = traceback.format_exc()
            logging.error(tb)
            logging.error("Something went wrong. Using old orbit information")
            return self.get_object(date - datetime.timedelta(days=1), _depth=_depth + 1)
