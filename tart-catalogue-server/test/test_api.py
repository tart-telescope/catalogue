"""
Integration testbench for the TART Catalogue REST API.

Exercises all endpoints against a running server. Configure the target
via the TART_CATALOGUE_URL environment variable (defaults to localhost:8876).

Usage:
    uv run python test/test_api.py
    TART_CATALOGUE_URL=https://tart.elec.ac.nz/catalog uv run python test/test_api.py
"""

import datetime
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TART_CATALOGUE_URL", "http://localhost:8876")
DATE_FMT = "%Y-%m-%dT%H:%M:%S"

passed = 0
failed = 0


def request(path, params=None, method="GET", body=None):
    """Make an HTTP request and return (status, body_dict)."""
    url = BASE_URL + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            raw = resp.read().decode()
            try:
                return status, json.loads(raw)
            except json.JSONDecodeError:
                return status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except urllib.error.URLError as e:
        return None, str(e.reason)


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        msg = f"  FAIL  {name}"
        if detail:
            msg += f"  --  {detail}"
        print(msg)


def test_root():
    print("\n### /")
    status, body = request("/")
    check("status 200", status == 200, f"got {status}")
    check("has message", isinstance(body, dict) and "message" in body)


def test_catalog():
    print("\n### /catalog")
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {
        "lat": -45.87,
        "lon": 170.60,
        "alt": 100,
        "ele": 0,
        "date": now.strftime(DATE_FMT),
    }
    status, body = request("/catalog", params)
    check("status 200", status == 200, f"got {status}")
    check("returns list", isinstance(body, list), f"got {type(body).__name__}")

    if isinstance(body, list) and len(body) > 0:
        sv = body[0]
        for field in ("name", "r", "el", "az", "jy"):
            check(f"satellite has '{field}'", field in sv)
    elif isinstance(body, list):
        print("  INFO  catalog returned 0 satellites (may be above horizon filter)")


def test_catalog_with_elevation():
    print("\n### /catalog (elevation filter)")
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {
        "lat": -45.87,
        "lon": 170.60,
        "alt": 100,
        "ele": 10,
        "date": now.strftime(DATE_FMT),
    }
    status, body = request("/catalog", params)
    check("status 200", status == 200, f"got {status}")
    check("returns list", isinstance(body, list))


def test_position():
    print("\n### /position")
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {"date": now.strftime(DATE_FMT)}
    status, body = request("/position", params)
    check("status 200", status == 200, f"got {status}")
    check("returns list", isinstance(body, list), f"got {type(body).__name__}")

    if isinstance(body, list) and len(body) > 0:
        sv = body[0]
        for field in ("name", "ecef", "ecef_dot", "jy"):
            check(f"satellite has '{field}'", field in sv)
        check(
            "ecef is 3-vector",
            isinstance(sv.get("ecef"), list) and len(sv["ecef"]) == 3,
        )
        check(
            "ecef_dot is 3-vector",
            isinstance(sv.get("ecef_dot"), list) and len(sv["ecef_dot"]) == 3,
        )


def test_ephemerides():
    print("\n### /ephemerides")
    now = datetime.datetime.now(datetime.timezone.utc)
    params = {"date": now.strftime(DATE_FMT)}
    status, body = request("/ephemerides", params)
    check("status 200", status == 200, f"got {status}")
    check("returns list", isinstance(body, list), f"got {type(body).__name__}")

    if isinstance(body, list) and len(body) > 0:
        sv = body[0]
        for field in ("name", "line1", "line2"):
            check(f"satellite has '{field}'", field in sv)
        check("line1 looks like TLE", sv.get("line1", "").startswith("1 "))
        check("line2 looks like TLE", sv.get("line2", "").startswith("2 "))


def test_ephemerides_multiple_dates():
    print("\n### /ephemerides (multiple dates)")
    now = datetime.datetime.now(datetime.timezone.utc)
    for offset_h in (0, 6, 12):
        dt = now + datetime.timedelta(hours=offset_h)
        params = {"date": dt.strftime(DATE_FMT)}
        status, body = request("/ephemerides", params)
        check(
            f"status 200 at +{offset_h}h",
            status == 200,
            f"got {status}",
        )
        check(
            f"returns list at +{offset_h}h",
            isinstance(body, list),
            f"got {type(body).__name__}",
        )


def test_bulk_az_el():
    print("\n### /bulk_az_el (POST)")
    now = datetime.datetime.now(datetime.timezone.utc)
    dates = [
        now.strftime(DATE_FMT),
        (now + datetime.timedelta(hours=6)).strftime(DATE_FMT),
    ]
    body = {
        "lat": -45.87,
        "lon": 170.60,
        "alt": 100,
        "dates": dates,
    }
    status, body_resp = request("/bulk_az_el", method="POST", body=body)
    check("status 200", status == 200, f"got {status}")
    check("has az_el", isinstance(body_resp, dict) and "az_el" in body_resp)

    if isinstance(body_resp, dict) and "az_el" in body_resp:
        az_el = body_resp["az_el"]
        check("az_el is list", isinstance(az_el, list))
        check("az_el has 2 entries", len(az_el) == 2, f"got {len(az_el)}")


def test_future_date_rejected():
    print("\n### /catalog (future date rejected)")
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
    params = {
        "lat": -45.87,
        "lon": 170.60,
        "alt": 100,
        "ele": 0,
        "date": future.strftime(DATE_FMT),
    }
    status, body = request("/catalog", params)
    check("status 400", status == 400, f"got {status}")


def test_bad_date_rejected():
    print("\n### /catalog (bad date rejected)")
    params = {
        "lat": -45.87,
        "lon": 170.60,
        "alt": 100,
        "ele": 0,
        "date": "not-a-date",
    }
    status, body = request("/catalog", params)
    check("status 400", status == 400, f"got {status}")


if __name__ == "__main__":
    print(f"Testing against: {BASE_URL}")

    test_root()
    test_catalog()
    test_catalog_with_elevation()
    test_position()
    test_ephemerides()
    test_ephemerides_multiple_dates()
    test_bulk_az_el()
    test_future_date_rejected()
    test_bad_date_rejected()

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    if failed:
        sys.exit(1)
