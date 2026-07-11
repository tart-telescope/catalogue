# TART Catalogue Python Client

Fetches TLE data from the catalogue server and computes satellite positions
in ECEF, celestial (RA/Dec), or horizontal (Az/El) coordinates.

## Setup

```sh
uv sync
```

## CLI Usage

```sh
# ECEF positions (default)
uv run python -m tart_client.cli ecef

# Celestial (RA/Dec) positions
uv run python -m tart_client.cli celestial

# Horizontal (Az/El) positions
uv run python -m tart_client.cli horizontal

# Benchmark (default 1000 queries over last week)
uv run python -m tart_client.cli benchmark 5000

# Custom server and date
uv run python -m tart_client.cli ecef \
  --url http://localhost:8876 \
  --date 2026-06-16T12:00:00Z
```

Set `TART_CATALOGUE_URL` to avoid repeating `--url`:

```sh
export TART_CATALOGUE_URL=http://localhost:8876
uv run python -m tart_client.cli celestial
```

## Library API

```python
from tart_client import CatalogueClient

client = CatalogueClient()  # defaults to https://tart.elec.ac.nz/catalog
# Or: CatalogueClient(base_url="http://localhost:8876")
```

### Fetch raw TLEs

```python
tles = client.fetch_tles()
# Returns: [{"name": "...", "line1": "...", "line2": "..."}, ...]
```

### ECEF positions

```python
positions = client.ecef_positions()
```

Returns Earth-Centered Earth-Fixed coordinates:

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "ecef_km": [12345.678, -23456.789, 3456.78],
    "velocity_km_s": [1.234, -2.345, 5.678]
  }
]
```

Transform: SGP4 → TEME → rotate by −GMST → ECEF.

### Celestial positions (RA/Dec)

```python
positions = client.celestial_positions()
```

Derived from ECEF. Returns ICRS right ascension and declination:

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

Transform: ECEF → ITRS → ICRS (via astropy).

### Horizontal positions (Az/El)

```python
positions = client.horizontal_positions(
    lat=-45.87,       # observer latitude (degrees)
    lon=170.60,       # observer longitude (degrees)
    alt=100.0,        # observer altitude (meters)
    min_elevation=10,  # min elevation to include (default -90 = all)
    name_regex="GPS",  # optional regex to filter by name (default None)
)
```

Derived from ECEF. Returns topocentric azimuth and elevation:

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "azimuth_deg": 45.123456,
    "elevation_deg": 30.654321,
    "range_km": 20200.123
  }
]
```

Transform: ECEF → observer-relative ENU → Az/El (WGS84 ellipsoid).

### Fast satellite count

```python
count = client.count_satellites()
# Returns: number of TLE records (no propagation)
```

## Caching

TLE data is cached in `~/.cache/tart-catalogue/`:
- **Nearest-match**: any cached entry within 12 hours of the requested time is reused
- **LRU eviction**: oldest 100+ entries are removed
- **In-memory**: parsed SGP4 satellite objects are cached per cache key to avoid re-parsing TLE lines

Server fetch requests are logged to stderr with the request datetime.

## Coordinate transforms

| Method | Input Frame | Output Frame | Transform |
|---|---|---|---|
| `ecef_positions` | TEME (SGP4) | ITRS (ECEF) | Rotate by −GMST |
| `celestial_positions` | ITRS (ECEF) | ICRS (RA/Dec) | astropy ITRS→ICRS |
| `horizontal_positions` | ITRS (ECEF) | ENU → Az/El | WGS84 geodetic + rotation |

## Testing

```sh
uv run pytest tart_client/ -v
```

Tests validate against astropy-generated reference vectors in `test-vectors/test_vectors.json`.
