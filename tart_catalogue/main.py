## Object Position Server FastAPI Implementation
#
# Author Tim Molteno tim@elec.ac.nz (c) 2013-2024
# Converted from Flask to FastAPI

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from tart.util import utc
from tart.util import angle

from tart_catalogue import norad_cache
from tart_catalogue import sun_object

import traceback
import logging


from logging.handlers import RotatingFileHandler


# Global cache objects - initialized at startup
waas_cache = None
gps_cache = None
galileo_cache = None
beidou_cache = None
sun = None

# Setup logging
logger = logging.getLogger("catalog")
if not logger.handlers:
    log_handler = RotatingFileHandler("catalog.log", mode='a',
                                      maxBytes=100000, backupCount=5,
                                      encoding=None, delay=False)
    log_handler.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)
    logger.setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize caches on startup"""
    global waas_cache, gps_cache, galileo_cache, beidou_cache, sun

    logger.info("Initializing caches...")
    waas_cache = norad_cache.NORADCache()
    gps_cache = norad_cache.GPSCache()
    galileo_cache = norad_cache.GalileoCache()
    beidou_cache = norad_cache.BeidouCache()
    sun = sun_object.SunObject()
    logger.info("Caches initialized successfully")
    yield
    logger.info("Shutdown completed.")


# Initialize the FastAPI app
app = FastAPI(
    lifespan=lifespan,
    title="Object Position Server REST API",
    description="API to provide a catalog of known objects",
    version="0.0.1",
    docs_url="/docs",
    redoc_url="/redoc",
    debug=True
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# @app.on_event("startup")
# async def startup_event():
#     """Initialize caches on startup"""
#     global waas_cache, gps_cache, galileo_cache, beidou_cache, sun

#     logger.info("Initializing caches...")
#     waas_cache = norad_cache.NORADCache()
#     gps_cache = norad_cache.GPSCache()
#     galileo_cache = norad_cache.GalileoCache()
#     beidou_cache = norad_cache.BeidouCache()
#     sun = sun_object.SunObject()
#     logger.info("Caches initialized successfully")


def parse_date(date_string: Optional[str] = None):
    """Parse date parameter or return current UTC time"""
    if date_string:
        try:
            # Deal with a URL that has a + sign replaced by a space
            d = utc.from_string(date_string.replace(' ', '+'))
        except Exception as err:
            raise HTTPException(status_code=400,
                                detail=f"Invalid Date '{date_string}' {err}")
    else:
        d = utc.now()

    current_date = utc.now()
    if ((d - current_date).total_seconds() > 86400.0):
        raise HTTPException(status_code=400,
                            detail=f"Date '{date_string}' more than 24 hours in future.")

    return d


def get_catalog_list(date, lat, lon, alt, elevation):
    logger.info(f"get_catalog_list({date}, {lat}, {lon}, {alt}, {elevation}")
    cat = waas_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += gps_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += galileo_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += beidou_cache.get_az_el(date, lat, lon, alt, elevation)
    cat += sun.get_az_el(date, lat, lon, alt, elevation)
    return cat



@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle general exceptions"""
    logger.error(f"Exception: {exc}")
    tb = traceback.format_exc()
    logger.error(f"Traceback: {tb}")
    raise HTTPException(status_code=500, detail=f"Exception: {exc}")


@app.get("/catalog", response_model=List[Dict[str, Any]])
async def get_catalog(
    lat: float = Query(..., description="Latitude in decimal degrees of observer"),
    lon: float = Query(..., description="Longitude in decimal degrees of observer"),
    alt: float = Query(0.0, description="Altitude in meters of observer"),
    ele: float = Query(0.0, description="Elevation in degrees"),
    date: Optional[str] = Query(None, description="UTC date for the request (ISO format)")
) -> List[Dict[str, Any]]:
    """
    Request Object Positions in local horizontal (El Az) coordinates

    Returns a list of objects with local horizontal (El Az) coordinates
    """
    catalogue_sources = [waas_cache, gps_cache, galileo_cache, beidou_cache, sun]

    try:
        logger.info("Hi")
        parsed_date = parse_date(date)
        lat_angle = angle.from_dms(lat)
        lon_angle = angle.from_dms(lon)

        return get_catalog_list(parsed_date, lat_angle, lon_angle, alt, ele)
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error in get_catalog: {err}")
        tb = traceback.format_exc()
        logger.error(f"Traceback: {tb}")
        raise HTTPException(status_code=500, detail=f"Exception: {err}")


@app.get("/position", response_model=List[Dict[str, Any]])
async def get_position(
   date: Optional[str] = Query(None, description="UTC date for the request (ISO format)")
) -> List[Dict[str, Any]]:
    """
    Request SV Positions in ECEF coordinates

    Returns a list of objects with coordinates in ECEF
    """
    catalogue_sources = [waas_cache, gps_cache, galileo_cache, beidou_cache]
    try:
        parsed_date = parse_date(date)

        ret = []
        for src in catalogue_sources:
            ret += src.get_positions(parsed_date)

        return ret
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Error in get_position: {err}")
        tb = traceback.format_exc()
        logger.error(f"Traceback: {tb}")
        return {"error": f"Exception: {err}", "traceback": tb.split("\n")}


# 1. Define the Pydantic model for the request body.
# This replaces the manual parsing of the request JSON and provides
# automatic data validation, serialization, and documentation.
class BulkAzElRequest(BaseModel):
    """
    Schema for the bulk_az_el endpoint request body.
    """
    lat: float
    lon: float
    alt: float
    dates: List[str]


@app.post("/bulk_az_el")
def get_bulk_az_el_fastapi(request_data: BulkAzElRequest):
    """
    Bulk request for SV Positions in local horizontal (El-Az) coordinates.
    """
    try:
        # Access the validated data directly from the Pydantic model instance.
        # No need for manual content-type checking or parsing.
        lat_param = request_data.lat
        lon_param = request_data.lon
        alt_param = request_data.alt
        dates_param = request_data.dates

        print(f"Request data: {request_data.dict()}")

        lat = angle.from_dms(lat_param)
        lon = angle.from_dms(lon_param)
        alt = alt_param

        # The original code has a redundant `try...except` block for `elevation`,
        # but since we have a Pydantic model, we can rely on the `alt` field.
        elevation = 0.0

        res = {
            'lat': lat.to_degrees(),
            'lon': lon.to_degrees(),
            'alt': alt
        }

        # The rest of the logic remains the same.
        date_list = [parse_date(ts) for ts in dates_param]
        res['dates'] = [d.isoformat() for d in date_list]

        res['az_el'] = [get_catalog_list(d, lat, lon, alt, elevation) for d in date_list]

        # FastAPI automatically converts dictionaries to JSON,
        # so there's no need for `jsonify()`.
        return res

    except Exception as err:
        # Use a more explicit HTTPException for API errors.
        # This will return a proper JSON error response with a 500 status code.
        tb = traceback.format_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(err),
                "traceback": tb.split("\n"),
                "param": request_data.dict()
            }
        )


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Object Position Server API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    print("Starting Object Position Server with FastAPI")
    uvicorn.run(app, host="0.0.0.0", port=8876)
