from fastapi.testclient import TestClient
import datetime
import unittest

from .main import app


class TestFileCacheRecursion(unittest.TestCase):
    """Unit tests for FileCache.get_object recursion limit."""

    def test_get_object_stops_after_five_retries(self):
        """get_object should raise RuntimeError after 5 failed fallback attempts."""
        from .file_cache import FileCache

        # Subclass to avoid real network/download logic
        class FailingCache(FileCache):
            def __init__(self):
                super().__init__("test")

            def get_url(self, utc_date):
                return "https://example.com/does_not_exist.tle"

            def download_file(self, url, local_file):
                raise RuntimeError("simulated download failure")

            def create_object_from_file(self, local_path):
                return None

        cache = FailingCache()
        start_date = datetime.datetime(2024, 6, 15, tzinfo=datetime.timezone.utc)

        with self.assertRaises(RuntimeError) as ctx:
            cache.get_object(start_date)

        self.assertIn("Failed to get object", str(ctx.exception))
        self.assertIn("after 5 retries", str(ctx.exception))

    def test_get_object_succeeds_before_limit(self):
        """get_object should not raise when cache hits after a few fallbacks."""
        import tart.util.utc as tart_utc
        from .file_cache import FileCache

        fail_count = {"count": 0}

        class FailingThenSucceedsCache(FileCache):
            def __init__(self):
                super().__init__("test")

            def get_url(self, utc_date):
                return "https://example.com/does_not_exist.tle"

            def download_file(self, url, local_file):
                fail_count["count"] += 1
                raise RuntimeError("simulated download failure")

            def create_object_from_file(self, local_path):
                return {"satellites": []}

        cache = FailingThenSucceedsCache()
        # Pre-seed the cache so that after 2 fallbacks it finds a match
        start_date = datetime.datetime(2024, 6, 15, tzinfo=datetime.timezone.utc)
        two_days_ago = start_date - datetime.timedelta(days=2)
        two_days_ago_utc = tart_utc.to_utc(two_days_ago)
        fname = cache.get_local_filename(two_days_ago_utc)
        cache.cache[fname] = {"satellites": []}

        result = cache.get_object(start_date)
        self.assertEqual(result, {"satellites": []})
        # Should have failed twice before hitting the seeded cache
        self.assertEqual(fail_count["count"], 2)


class TestBulkAzEl(unittest.TestCase):
    """Tests for the /bulk_az_el endpoint error handling."""

    def test_future_date_rejected_with_400(self):
        """A date more than 24h in the future must return 400, not 500."""
        t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        with TestClient(app) as client:
            body = {'lat': -45.87, 'lon': 170.6, 'alt': 100.0, 'dates': [t.isoformat()]}
            r = client.post('/bulk_az_el', json=body)
        self.assertEqual(r.status_code, 400, r.text)

    def test_bad_date_rejected_with_400(self):
        """An unparseable date must return 400, not 500."""
        with TestClient(app) as client:
            body = {'lat': -45.87, 'lon': 170.6, 'alt': 100.0, 'dates': ['not-a-date']}
            r = client.post('/bulk_az_el', json=body)
        self.assertEqual(r.status_code, 400, r.text)


def request(dt):
    with TestClient(app) as client:
        payload = {'date': dt.isoformat(),
                    'lat': -45.87,
                    'lon': 170.6, 'elevation': 45}

        r = client.get('/catalog', params=payload)
        return r


class TestCatalog(unittest.TestCase):

    def test_basic_request(self):
        ans = request(datetime.datetime.now(datetime.timezone.utc))
        for sv in ans.json():
            self.assertTrue('r' in sv)
            self.assertTrue('el' in sv)
            self.assertTrue('az' in sv)
            self.assertTrue('jy' in sv)

    def test_future_date(self):
        t = datetime.datetime.now(datetime.timezone.utc)
        dt = datetime.timedelta(days=2)
        ans = request(t + dt)
        print(ans)
        print(ans.json())

        assert ans.status_code == 400
