# TART Catalogue Rust Client

Fast Rust binary that fetches TLEs from the catalogue server and computes
satellite positions in ECEF, celestial (RA/Dec), or horizontal (Az/El) coordinates.

## Build

```sh
cargo build --release
```

## CLI Usage

```sh
# ECEF positions (default)
cargo run --release

# Celestial (RA/Dec) positions
cargo run --release -- celestial

# Override server (defaults to https://tart.elec.ac.nz/catalog)
TART_CATALOGUE_URL=http://localhost:8876 cargo run --release

# Benchmark (default 1000 queries)
cargo run --release -- benchmark 5000
```

Progress and warnings go to stderr. Pipe stdout for clean JSON:

```sh
cargo run --release 2>/dev/null | jq '.[0]'
```

## Library

The `CatalogueClient` struct provides the core API:

```rust
let mut client = CatalogueClient::new("https://tart.elec.ac.nz/catalog");
```

### Fetch raw TLEs

```rust
let tles: Vec<TleRecord> = client.fetch_tles(&date).await?;
```

### ECEF positions

```rust
let positions: Vec<EcefPosition> = client.ecef_positions(&date, &dates).await?;
```

Returns Earth-Centered Earth-Fixed coordinates:

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "date": "2026-06-16T12:00:00+00:00",
    "ecef_km": [12345.678, -23456.789, 3456.78],
    "velocity_km_s": [1.234, -2.345, 5.678]
  }
]
```

Transform: SGP4 → TEME → rotate by −GMST → ECEF.

### Celestial positions (RA/Dec)

```rust
let positions: Vec<CelestialPosition> = client.celestial_positions(&date, &dates).await?;
```

Derived from ECEF:

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "date": "2026-06-16T12:00:00+00:00",
    "ra_hours": 12.345678,
    "dec_degrees": -45.678901,
    "distance_km": 26500.0
  }
]
```

Transform: ECEF → rotate by +GMST → inertial → RA = atan2(y, x), Dec = asin(z/r).

### Horizontal positions (Az/El)

```rust
let positions: Vec<HorizontalPosition> = client.horizontal_positions(
    &date, &dates,
    -45.87,     // observer latitude (degrees)
    170.60,     // observer longitude (degrees)
    100.0,      // observer altitude (meters)
).await?;
```

Returns topocentric azimuth and elevation:

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "date": "2026-06-16T12:00:00+00:00",
    "azimuth_deg": 45.123456,
    "elevation_deg": 30.654321,
    "range_km": 20200.123
  }
]
```

Transform: ECEF → observer-relative ENU (WGS84) → Az/El.

### Fast satellite count

```rust
let count: usize = client.count_satellites(&date).await?;
```

Returns the number of TLE records (no SGP4 propagation).

## Caching

TLE data is cached in `~/.cache/tart-catalogue/`:
- **Nearest-match**: any cached entry within 12 hours of requested time is reused
- **LRU eviction**: oldest 100+ entries are removed
- **In-memory**: pre-built SGP4 `Constants` are cached per cache key, avoiding re-parsing TLE lines and re-initializing propagators

Server fetch requests are logged to stderr with the request datetime.

## Coordinate transforms

| Method | Input | Output | Transform |
|---|---|---|---|
| `ecef_positions` | TEME (SGP4) | ECEF | Rotate by −GMST (pre-computed per date) |
| `celestial_positions` | ECEF | RA/Dec | Rotate by +GMST → atan2/asin |
| `horizontal_positions` | ECEF | Az/El | WGS84 → ENU → spherical |

## Testing

```sh
cargo test
```

Tests cover GMST, Julian day, rotation matrices, SGP4 orbital properties,
geodetic-to-ECEF conversion, and horizontal coordinate roundtrip verification.
