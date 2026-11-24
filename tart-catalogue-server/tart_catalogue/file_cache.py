# (c) 2018-2023 Tim Molteno (tim@elec.ac.nz)

import bisect
import datetime
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import tart.util.utc as utc

from tart_catalogue import sky_object

# CelesTrak rate limits clients that hammer it. A cold cache answering a
# multi-date /bulk_az_el request fires one request per date per constellation
# at the same host, and concurrent requests used to overlap (2026-11-20: ~17
# simultaneous requests were cut off and every date then failed for an hour).
# Request *starts* are therefore spaced out per host.
MIN_REQUEST_INTERVAL = 1.5  # seconds between request starts to one host

DOWNLOAD_TIMEOUT = 20.0  # seconds per attempt (an unbounded urlopen hangs a worker)
DOWNLOAD_ATTEMPTS = 3  # attempts for transient errors
DOWNLOAD_BACKOFF = 1.0  # seconds before the first retry (doubles each retry)
FAILED_DOWNLOAD_RETRY_SECONDS = 3600  # per target file, after a failure
MAX_FALLBACK_DAYS = 5  # how far get_object walks back over dates

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        _VERSION = _pkg_version("tart_catalogue")
    except PackageNotFoundError:  # running from a source checkout
        _VERSION = "unknown"
except ImportError:  # pragma: no cover - always available on python3.11+
    _VERSION = "unknown"

USER_AGENT = (
    f"tart-catalogue-server/{_VERSION} "
    "(https://github.com/tart-telescope/catalogue)"
)

_host_gate = threading.Lock()
_last_request_start = {}


def _wait_for_request_slot(url, interval):
    """Space out request starts to the host behind `url`.

    The gate is held while sleeping so concurrent downloaders queue up
    instead of all firing at once.
    """
    if interval <= 0:
        return
    host = urllib.parse.urlsplit(url).netloc
    with _host_gate:
        last = _last_request_start.get(host)
        if last is not None:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        _last_request_start[host] = time.monotonic()


class DownloadThrottled(RuntimeError):
    """A target file failed recently; downloading it again is blocked for a
    while. Callers fall back to cached data instead of retrying the network."""


class FileCache(sky_object.SkyObject):
    # Tunables (overridable per cache and by tests)
    min_request_interval = MIN_REQUEST_INTERVAL
    download_timeout = DOWNLOAD_TIMEOUT
    download_attempts = DOWNLOAD_ATTEMPTS
    download_backoff = DOWNLOAD_BACKOFF
    failed_retry_seconds = FAILED_DOWNLOAD_RETRY_SECONDS
    max_fallback_days = MAX_FALLBACK_DAYS
    user_agent = USER_AGENT

    def __init__(self, name):
        sky_object.SkyObject.__init__(self, name)
        self.cache_root = "./orbit_data/{}".format(self.name)
        self.last_download_attempt = {}
        # The exception behind each throttled target file, so a later
        # "already attempted" message still names the real cause.
        self.last_download_error = {}
        self.cache = {}
        # Serialises existence-check + download so concurrent requests for
        # one cache cannot stampede the host with duplicate downloads.
        self._lock = threading.RLock()
        self._last_error = None

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

        The warning is only emitted the first time the data is filed, so a
        repeated request for the same date does not repeat the message.
        """
        data_date = self.get_data_date(obj)
        if data_date is None:
            return

        data_fname = self.get_local_filename(data_date)
        if data_fname == fname:
            return

        if data_fname not in self.cache:
            logging.warning(
                f"{self.name}: data filed as '{fname}' has epoch "
                f"{data_date.isoformat()}; positions for the requested date "
                f"are propagated from this epoch"
            )
            data_path = self.get_local_path(data_fname)
            if not os.path.isfile(data_path):
                os.makedirs(os.path.dirname(data_path), exist_ok=True)
                shutil.copyfile(local_path, data_path)
            self.cache[data_fname] = obj

    def validate_payload(self, data, url):
        """Reject a response before it is written into the cache.

        Subclasses extend this to check the payload actually parses as the
        data they expect (an HTML error page or an empty body from a rate
        limiter must not become a 'good' cached file).
        """
        if not data:
            raise ValueError(f"empty response from {url}")

    @staticmethod
    def is_transient(err):
        """Errors worth retrying: dropped connections, timeouts, rate limits."""
        if isinstance(err, urllib.error.HTTPError):
            return err.code in (408, 425, 429, 500, 502, 503, 504)
        return isinstance(err, (urllib.error.URLError, TimeoutError, OSError))

    def fetch(self, url):
        """Fetch `url` with a timeout, an identifying User-Agent and retries
        for transient errors."""
        last_err = None
        for attempt in range(1, self.download_attempts + 1):
            if attempt > 1:
                time.sleep(self.download_backoff * (2 ** (attempt - 2)))
            _wait_for_request_slot(url, self.min_request_interval)
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": self.user_agent}
                )
                resp = urllib.request.urlopen(req, timeout=self.download_timeout)
                try:
                    data = resp.read()
                finally:
                    close = getattr(resp, "close", None)
                    if close is not None:
                        close()
                return data
            except Exception as err:
                last_err = err
                if not self.is_transient(err) or attempt == self.download_attempts:
                    raise
                logging.warning(
                    "%s: transient error fetching %s (attempt %d/%d): %r",
                    self.name,
                    url,
                    attempt,
                    self.download_attempts,
                    err,
                )
        raise last_err  # pragma: no cover - loop always returns or raises

    def download_file(self, url, local_file):
        directory = os.path.dirname(local_file)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with self._lock:
            # Throttle repeated download *failures* per target file. This used to
            # be keyed by url and set on every attempt, but the NORAD urls are
            # date-independent: one attempt then blocked every other date in a
            # bulk request, which silently fell back to another day's data
            # (issue #5).
            last_try = self.last_download_attempt.get(local_file)
            if last_try is not None:
                delta_seconds = (datetime.datetime.now() - last_try).total_seconds()
                if delta_seconds < self.failed_retry_seconds:
                    cause = self.last_download_error.get(local_file)
                    raise DownloadThrottled(
                        f"Error ({url} -> {local_file}): download failed "
                        f"{delta_seconds:.0f}s ago ({cause!r}), not retrying "
                        f"for another "
                        f"{self.failed_retry_seconds - delta_seconds:.0f}s"
                    )

            logging.info("starting download (%s -> %s)", url, local_file)
            tmp_file = local_file + ".part"
            try:
                data = self.fetch(url)
                self.validate_payload(data, url)
                with open(tmp_file, "wb") as w:
                    w.write(data)
                # Never leave a partially written file behind to be parsed later.
                os.replace(tmp_file, local_file)
                logging.info("download complete")
            except Exception as err:
                self.last_download_attempt[local_file] = datetime.datetime.now()
                self.last_download_error[local_file] = err
                if os.path.isfile(tmp_file):
                    os.remove(tmp_file)
                # One line, no traceback: the caller chains this into its own
                # error, and a full traceback per date buries the real cause.
                logging.warning(
                    "%s: download failed (%s -> %s): %r (further attempts for "
                    "this file throttled for %ss)",
                    self.name,
                    url,
                    local_file,
                    err,
                    self.failed_retry_seconds,
                )
                raise
            self.last_download_attempt.pop(local_file, None)
            self.last_download_error.pop(local_file, None)

    def _cached_dates(self):
        """Every complete cached file as (date, path), sorted by date."""
        entries = []
        if not os.path.isdir(self.cache_root):
            return entries
        for root, _dirs, files in os.walk(self.cache_root):
            for fname in files:
                if fname.endswith(".part"):
                    continue
                path = os.path.join(root, fname)
                parts = os.path.relpath(path, self.cache_root).split(os.sep)
                if len(parts) != 3:
                    continue
                try:
                    file_date = datetime.datetime(
                        int(parts[0]), int(parts[1]), int(parts[2])
                    )
                except ValueError:
                    continue
                entries.append((file_date, path))
        entries.sort()
        return entries

    def _nearest_cached_file(self, utc_date, exclude=None):
        """The cached file closest to `utc_date`, preferring an earlier one.

        Used when a download fails: answering from data already on disk beats
        erroring the request or walking into more downloads (which is what
        turned one CelesTrak outage into an hour of failures).
        """
        wanted = datetime.datetime(utc_date.year, utc_date.month, utc_date.day)
        entries = [
            (d, p) for d, p in self._cached_dates() if p != exclude
        ]
        if not entries:
            return None
        dates = [d for d, _p in entries]
        idx = bisect.bisect_right(dates, wanted)
        if idx > 0:
            return entries[idx - 1][1]
        return entries[0][1]

    def get_object(self, date, _depth=0):
        utc_date = utc.to_utc(date)

        if _depth == 0:
            self._last_error = None

        if _depth >= self.max_fallback_days:
            raise RuntimeError(
                f"Failed to get object for {self.name} after {_depth} retries "
                f"(earliest date tried: {date.isoformat()}): {self._last_error!r}"
            )

        fname = self.get_local_filename(utc_date)
        if fname in self.cache:
            return self.cache[fname]

        local_path = self.get_local_path(fname)
        try:
            with self._lock:
                if os.path.isfile(local_path) is False:
                    self.download_file(self.get_url(utc_date), local_path)

            self.cache[fname] = self.create_object_from_file(local_path)
            self._refile_by_data_date(self.cache[fname], local_path, fname)
            return self.cache[fname]
        except Exception as error:
            # Keep the root cause: the throttle error raised on a later
            # fallback day must not hide the original download failure.
            if self._last_error is None or isinstance(
                self._last_error, DownloadThrottled
            ):
                self._last_error = error
            # Use data already on disk rather than downloading (or failing).
            fallback = self._nearest_cached_file(utc_date, exclude=local_path)
            if fallback is not None:
                try:
                    obj = self.create_object_from_file(fallback)
                except Exception as fallback_err:
                    logging.warning(
                        "%s: cached data %s could not be read: %r",
                        self.name,
                        fallback,
                        fallback_err,
                    )
                else:
                    logging.warning(
                        "%s: no data for %s (%r); serving cached %s instead",
                        self.name,
                        fname,
                        error,
                        os.path.relpath(fallback, self.cache_root),
                    )
                    return obj
            logging.debug(
                "%s: no cached data for %s (%r); trying an earlier date",
                self.name,
                fname,
                error,
            )
            return self.get_object(
                date - datetime.timedelta(days=1), _depth=_depth + 1
            )
