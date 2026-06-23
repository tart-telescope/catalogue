# TART Catalogue Rust Client

Fast Rust binary that fetches TLEs from the catalogue server and computes
satellite positions in ECEF or celestial (RA/Dec) coordinates.

## Build

```sh
cargo build --release
```

## Usage

```sh
# ECEF positions (default)
cargo run --release

# Celestial (RA/Dec) positions
cargo run --release -- celestial

# Custom server
TART_CATALOGUE_URL=http://localhost:8876 cargo run --release
```

## Output

### ECEF (`ecef`)

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

### Celestial (`celestial` / `cel`)

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

Progress and warnings go to stderr. Pipe stdout for clean JSON:

```sh
cargo run --release 2>/dev/null | jq '.[0]'
```

## How it works

1. **Fetch** — GET `/ephemerides` returns raw TLE (name, line1, line2)
2. **Propagate** — `sgp4` crate computes TEME position at requested times
3. **Convert**:
   - **ECEF** — rotate TEME position by −GMST around Z axis
   - **Celestial** — TEME is an inertial frame; RA = atan2(y, x), Dec = asin(z/r)

Bad satellites are skipped with a warning to stderr; the rest continue.
