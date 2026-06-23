# TART Catalogue Python Client

Fetches TLE data from the catalogue server and computes satellite positions
in ECEF or celestial (RA/Dec) coordinates.

## Setup

```sh
uv sync
```

## Usage

```sh
# ECEF positions (default)
uv run python -m tart_client.cli ecef

# Celestial (RA/Dec) positions
uv run python -m tart_client.cli celestial

# Custom server and date
uv run python -m tart_client.cli ecef \
  --url http://localhost:8876 \
  --date 2026-06-16T12:00:00Z
```

Or set the server URL once:

```sh
export TART_CATALOGUE_URL=http://localhost:8876
uv run python -m tart_client.cli celestial
```

## Library use

```python
from tart_client import CatalogueClient

client = CatalogueClient(base_url="http://localhost:8876")

# ECEF positions
for sat in client.ecef_positions():
    print(sat["name"], sat["ecef_km"])

# Celestial positions
for sat in client.celestial_positions():
    print(sat["name"], sat["ra_hours"], sat["dec_degrees"])
```

## Output

### ECEF

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "ecef_km": [12345.678, -23456.789, 3456.78],
    "velocity_km_s": [1.234, -2.345, 5.678]
  }
]
```

### Celestial

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "ra_hours": 12.345678,
    "dec_degrees": -45.678901,
    "distance_km": 26500.0
  }
]
```

## How it works

1. **Fetch** — GET `/ephemerides` returns raw TLE (name, line1, line2)
2. **Propagate** — SGP4 propagator computes TEME position at the requested time
3. **Convert** — astropy transforms TEME → ITRS (ECEF) or TEME → ICRS (RA/Dec)
