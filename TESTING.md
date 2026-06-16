# Testing the TART Catalogue

## Catalogue server (Python)

### Unit tests

Run the FastAPI test suite with pytest:

```sh
uv run pytest tart_catalogue/ -v
```

Tests use FastAPI's `TestClient` and exercise the `/catalog` endpoint without needing
a running server or network access (TLE data is downloaded from CelesTrak on demand
by the cache layer).

### Manual endpoint testing

Start the server locally:

```sh
uv run uvicorn tart_catalogue.main:app --host 0.0.0.0 --port 8876
```

Then hit the endpoints:

```sh
# List all visible objects from a given location
curl "http://localhost:8876/catalog?lat=-45.87&lon=170.6&alt=100&ele=10"

# ECEF positions for all satellites
curl "http://localhost:8876/position?date=2026-06-16T12:00:00Z"

# Raw TLE data for client-side position calculation
curl "http://localhost:8876/ephemerides"
```

Interactive API docs are available at `http://localhost:8876/docs`.

### Docker Compose

Build and run the full stack (catalogue + nginx reverse proxy):

```sh
make build   # docker compose build
make test    # docker compose up --build
```

The catalogue will be available on port 8876 via nginx.

### Client API test

A script `test/test_api.py` can be used to test the live API:

```sh
make test-client   # runs: uv run python3 test/test_api.py
```

> **Note:** `test/test_api.py` must be created; this target exists in the Makefile
> for integration testing against a running instance.

---

## Rust client (`rust-client/`)

### Build

```sh
cd rust-client
cargo build --release
```

### Run (against production by default)

```sh
cargo run --release
```

This fetches TLEs from `https://tart.elec.ac.nz/catalog/ephemerides`, propagates
positions for now, +6h, +12h, +18h, and +24h, and prints JSON to stdout.

### Run against a local server

```sh
TART_CATALOGUE_URL=http://localhost:8876 cargo run --release
```

### Expected output

Successful runs print a JSON array to stdout:

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

Progress and error messages are printed to stderr, so you can pipe stdout:

```sh
cargo run --release 2>/dev/null | jq '.[0]'
```

### Static checks

```sh
cargo check     # fast compile-check, no binary
cargo clippy    # lint
cargo test      # no tests yet — add with `#[cfg(test)] mod tests { ... }`
```
