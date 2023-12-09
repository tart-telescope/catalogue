# Object Position Server Main API
#
# Author Tim Molteno tim@elec.ac.nz (c) 2013-2023


from flask import Flask
from flask import jsonify, request
from flask_cors import CORS, cross_origin

from werkzeug.middleware.proxy_fix import ProxyFix

import tart.util.utc as utc
from tart.util import angle
import traceback

import norad_cache
from dateutil import parser
import sun_object

waas_cache = norad_cache.NORADCache()
# extra_cache = norad_cache.ExtraCache()
gps_cache = norad_cache.GPSCache()
galileo_cache = norad_cache.GalileoCache()
beidou_cache = norad_cache.BeidouCache()

sun = sun_object.SunObject()


def parse_date(date_string):
    try:
        if date_string == "now":
            d = utc.now()
        else:
            # Deal with a URL that has a + sign replaced by a space
            dt = parser.parse(date_string.replace(' ', '+'))
            d = utc.to_utc(dt)
    except Exception as err:
        raise Exception("Invalid Date '{}' {}".format(date_string, err))
    return d


def parse_request_date(request):
    if 'date' in request.args:
        date_string = request.args.get('date')
        d = parse_date(date_string)
    else:
        d = utc.now()

    current_date = utc.now()
    if ((d - current_date).total_seconds() > 86400.0):
        raise Exception(
            f"Date '{date_string}' more than 24 hours in future. {current_date} {d}")
    return d


def get_required_parameter(request, param_name):
    if param_name in request.args:
        return request.args.get(param_name)
    else:
        # app.logger.error("Missing Required Parameter {}".format(param_name))
        raise Exception("Missing Required Parameter '{}'".format(param_name))


def get_catalog_list(date, lat, lon, alt, elevation):
    cat = waas_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += gps_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += galileo_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += beidou_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += sun.get_az_el(date, lat, lon, alt, elevation)
    return cat


# sudo pip install Flask
app = Flask(__name__)
CORS(app)

app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

if not app.debug:
    import logging
    from logging.handlers import RotatingFileHandler
    log_handler = RotatingFileHandler(
        "catalog.log", mode='a', maxBytes=100000,
        backupCount=5, encoding=None, delay=False)
    log_handler.setLevel(logging.WARNING)
    app.logger.addHandler(log_handler)


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(e)
    tb = traceback.format_exc()
    app.logger.error(tb)
    return "Exception: {}".format(e)


"""
    @api {get} /catalog/:date:lat:lon:elevation:alt Request Object Positions local horizontal (El Az) coordinates
    @apiVersion 0.0.2
    @apiName catalog
    @apiGroup Catalog

    @apiParam {String} [date=now] UTC date in isoformat() for the request (defaults to current time)
    @apiParam {Number} lat Latitude in decimal degrees of observer
    @apiParam {Number} lon Longitude in decimal degrees of observer
    @apiParam {Number} [elevation=0.0] Ignore objects below the specified elevation in decimal degrees
    @apiParam {Number} [alt=0.0] Altitude in meters of observer

    @apiSuccess {List} ObjectList List of objects with local horizontal (El Az) coordinates

    @apiSampleRequest /catalog?lat=-45.85&lon=170.54
"""
@app.route('/catalog', methods=['GET', ])
def get_catalog():
    date = parse_request_date(request)
    lat = angle.from_dms(float(get_required_parameter(request, 'lat')))
    lon = angle.from_dms(float(get_required_parameter(request, 'lon')))
    try:
        elevation = float(request.args.get('elevation'))
    except Exception:
        elevation = 0.0
    alt = 0.0
    ret = get_catalog_list(date, lat, lon, alt, elevation)
    return jsonify(ret)


"""
    @api {get} /position/:date Request SV Positions in ECEF coordinates
    @apiVersion 0.0.2
    @apiName position
    @apiGroup Catalog

    @apiParam {String} [date=now] UTC date for the request

    @apiSuccess {List} ObjectList List of objects with coordinates in ECEF
    @apiSampleRequest /position
"""

@app.route('/position', methods=['GET', ])
def get_pos():
    try:
        date = parse_request_date(request)
        ret = waas_cache.get_positions(date)
        ret += gps_cache.get_positions(date)
        # ret += extra_cache.get_positions(date)
        ret += galileo_cache.get_positions(date)
        ret += beidou_cache.get_positions(date)
        return jsonify(ret)
    except Exception as err:
        tb = traceback.format_exc()
        ret = "Exception: {}".format(err)
        lines = tb.split("\n")
        return jsonify({"error": ret, "traceback": lines})


"""
    @api {post} /bulk_az_el/ Bulk request SV Positions in local horizontal (El-Az) coordinates
    @apiVersion 0.2.0
    @apiName bulk_az_el
    @apiGroup Catalog

    @apiBody {Number} lat Latitude in degrees of observer
    @apiBody {Number} lon Longitude in degegrees of observer
    @apiBody {Number} alt Altitude in meters of observer
    @apiBody {String[]} dates Array of dates in isoformat (eg ["2023-12-07T09:25:55.924113", "2023-12-07T09:25:55.924113"]}
    @apiParamExample {json} Request-Example:
              {'lat': lat,
               'lon': lon,
               'alt' : alt,
               'dates': ['2023-12-07T09:25:55.924113', '2023-12-07T09:25:55.924113']}

    @apiExample {python} Example usage:
                        import requests
                        json_data =  {"lat": 45.5, "lon": 170.5, "alt": 0, "dates": ["2023-12-07T09:25:55.924113", "2023-12-07T09:25:55.924113"]}
                        r = requests.post('http://localhost:8876/bulk_az_el', json=json_data)

    @apiSuccess {json} Original request with an added field of 'az_el' which is a list of lists of sources, one for each timestamp

    @apiSampleRequest /bulk_az_el
"""

@app.route('/bulk_az_el', methods=['POST', ])
def get_bulk_az_el():
    content_type = request.headers.get('Content-Type')
    if (content_type == 'application/json'):
        request_data = request.json
    else:
        return f"Content-Type {content_type} not supported!"

    try:
        print(f"Request data {request_data}")
        dates_param = request_data['dates']
        lat = angle.from_dms(float(request_data['lat']))
        lon = angle.from_dms(float(request_data['lon']))
        alt = float(request_data['alt'])
        try:
            elevation = float(request_data['alt'])
        except Exception:
            elevation = 0.0

        res = {'lat': lat.to_degrees(),
               'lon': lon.to_degrees(),
               'alt': alt}

        date_list = [parse_date(ts) for ts in dates_param]
        res['dates'] = [d.isoformat() for d in date_list]
        res['az_el'] = [get_catalog_list(d, lat, lon, alt, elevation) for d in date_list]

        return jsonify(res)

    except Exception as err:
        tb = traceback.format_exc()
        ret = f"Exception: {err}"
        lines = tb.split("\n")
        return jsonify({"error": ret, "traceback": lines, "param": f"{res}"})


if __name__ == '__main__':
    print("Hello world")
    app.run(port=8876, host='0.0.0.0')
