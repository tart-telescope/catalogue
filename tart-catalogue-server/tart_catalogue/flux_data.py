"""
Flux data lookup for GNSS satellites.

Loads a JSON file mapping NORAD catalog numbers to flux values (Janskys).
Used to annotate ephemerides with flux information.
"""

import json
import pathlib
import logging

logger = logging.getLogger("catalog")

FLUX_FILE = pathlib.Path(__file__).parent / "flux.json"


def load_flux_data():
    """Load the flux data from the JSON file."""
    try:
        with open(FLUX_FILE) as f:
            data = json.load(f)
        default = data.pop("default", 1.5e6)
        data.pop("_comment", None)
        return {"default": default, "satellites": data}
    except FileNotFoundError:
        logger.warning(f"Flux data file not found: {FLUX_FILE}, using default")
        return {"default": 1.5e6, "satellites": {}}
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON in {FLUX_FILE}, using default")
        return {"default": 1.5e6, "satellites": {}}


def get_flux(satcat, flux_data):
    """Look up flux for a given NORAD catalog number.

    Args:
        satcat: NORAD catalog number (int or str)
        flux_data: dict from load_flux_data()

    Returns: flux in Janskys (float)
    """
    sats = flux_data.get("satellites", {})
    return sats.get(str(satcat), flux_data.get("default", 1.5e6))


def extract_satcat(line1):
    """Extract NORAD catalog number from TLE line 1 (columns 3-7)."""
    try:
        return int(line1[2:7].strip())
    except (ValueError, IndexError):
        return None
