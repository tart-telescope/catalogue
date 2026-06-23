# Testing the TART Catalogue

All server commands assume the working directory is `tart-catalogue-server/`.

## Catalogue server (Python)

### Unit tests

```sh
cd tart-catalogue-server
uv run pytest tart_catalogue/ -v
```

Uses FastAPI's `TestClient` — no running server or network needed.

### Integration testbench

Runs the Rust client against a running server:

```sh
cd tart-catalogue-server
make test-client   # Rust client -> localhost:8876
```

Or the Python integration testbench:

```sh
cd tart-catalogue-server
TART_CATALOGUE_URL=http://localhost:8876 python3 test/test_api.py
```

Or against a remote instance:

```sh
TART_CATALOGUE_URL=https://tart.elec.ac.nz/catalog python3 tart-catalogue-server/test/test_api.py
```

Tests cover `/`, `/catalog`, `/catalog` with elevation filter, `/position`,
`/ephemerides` (single and multiple dates), `/bulk_az_el`, and error cases
(future dates, bad date strings). Expected output:

```
Testing against: http://localhost:8876

### /
  PASS  status 200
  PASS  has message
### /catalog
  PASS  status 200
  PASS  returns list
  PASS  satellite has 'name'
  ...
==================================================
Results: N passed, 0 failed out of N
```

### Manual endpoint testing

```sh
cd tart-catalogue-server
uv run uvicorn tart_catalogue.main:app --host 0.0.0.0 --port 8876
```

```sh
curl "http://localhost:8876/catalog?lat=-45.87&lon=170.6&alt=100&ele=10"
curl "http://localhost:8876/position?date=2026-06-16T12:00:00Z"
curl "http://localhost:8876/ephemerides"
curl -X POST http://localhost:8876/bulk_az_el \
  -H "Content-Type: application/json" \
  -d '{"lat":-45.87,"lon":170.6,"alt":100,"dates":["2026-06-16T12:00:00Z"]}'
```

Interactive docs at `http://localhost:8876/docs`.

### Docker Compose

```sh
cd tart-catalogue-server
make build   # docker compose build
make test    # docker compose up --build
```

Or from the repo root:

```sh
docker compose -f tart-catalogue-server/compose.yml build
docker compose -f tart-catalogue-server/compose.yml up --build
```

Available on port 8876 via nginx.

---

## Rust client (`tart-catalog-client-rs/`)

### Build

```sh
cd tart-catalog-client-rs
cargo build --release
```

### Run against production

```sh
cargo run --release                 # ECEF
cargo run --release -- celestial    # RA/Dec
cargo run --release -- horizontal   # Az/El
cargo run --release -- benchmark    # benchmark
```

Fetches TLEs from `https://tart.elec.ac.nz/catalog/ephemerides`, propagates for
now, +6h, +12h, +18h, +24h, and prints JSON to stdout.

### Run against local server

```sh
TART_CATALOGUE_URL=http://localhost:8876 cargo run --release
```

### Expected output

```json
[
  {
    "name": "GPS BIIR-2  (PRN 13)",
    "date": "2026-06-16T12:00:00+00:00",
    "ecef_km": [-12345.6, 23456.7, 3456.8],
    "velocity_km_s": [1.23, -2.34, 5.67]
  }
]
```

Progress and warnings go to stderr:

```sh
cargo run --release 2>/dev/null | jq '.[0]'
```

### Static checks

```sh
cargo check     # fast compile-check
cargo clippy    # lint
cargo test      # 10 unit tests (GMST, rotations, SGP4, ECEF, horizontal)
```

---

## Python client (`tart-catalog-client-py/`)

### Setup

```sh
cd tart-catalog-client-py
uv sync
```

### Run against production

```sh
uv run python -m tart_client.cli ecef        # ECEF
uv run python -m tart_client.cli celestial   # RA/Dec
uv run python -m tart_client.cli horizontal  # Az/El
uv run python -m tart_client.cli benchmark   # benchmark
```

### Run against local server

```sh
TART_CATALOGUE_URL=http://localhost:8876 uv run python -m tart_client.cli celestial
```

### Tests

```sh
uv run pytest tart_client/ -v
```

7 tests covering coordinate transforms validated against astropy reference vectors.
