# Testing the TART Catalogue

## Catalogue server (Python)

### Unit tests

```sh
uv run pytest tart_catalogue/ -v
```

Uses FastAPI's `TestClient` — no running server or network needed.

### Integration testbench

Exercises all endpoints against a running server:

```sh
uv run python test/test_api.py
```

Or against a remote instance:

```sh
TART_CATALOGUE_URL=https://tart.elec.ac.nz/catalog uv run python test/test_api.py
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
make build   # docker compose build
make test    # docker compose up --build
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
cargo run --release
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
    "position_km": [-12345.6, 23456.7, 3456.8],
    "velocity_km_s": [1.23, -2.34, 5.67]
  }
]
```

Progress and skipped-satellite warnings go to stderr:

```sh
cargo run --release 2>/dev/null | jq '.[0]'
```

### Static checks

```sh
cargo check     # fast compile-check
cargo clippy    # lint
cargo test      # no unit tests yet
```
