from fastapi.testclient import TestClient
import datetime
import unittest

from .main import app


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
