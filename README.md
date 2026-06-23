# TART Object Position Server

REST API providing positions of GNSS satellites and other objects visible to the
[TART](https://github.com/tmolteno/TART) radio telescope.

Live instance: `https://tart.elec.ac.nz/catalog`

## Quick start

```sh
cd tart-catalogue-server
uv sync
uv run uvicorn tart_catalogue.main:app --host 0.0.0.0 --port 8876
```

Open `http://localhost:8876/docs` for the interactive API docs.

## Docker

```sh
cd tart-catalogue-server
docker compose build
docker compose up -d
```

Or from the repo root:

```sh
docker compose -f tart-catalogue-server/compose.yml build
docker compose -f tart-catalogue-server/compose.yml up -d
```

The catalogue is served on port 8876 through an nginx reverse proxy.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Health check |
| `/catalog` | GET | Visible objects in local horizontal (Az/El) coordinates |
| `/position` | GET | ECEF positions and velocities for all satellites |
| `/ephemerides` | GET | Raw TLE data for client-side orbit propagation |
| `/bulk_az_el` | POST | Bulk Az/El positions for multiple dates |

### `/catalog`

```
GET /catalog?lat=-45.87&lon=170.60&alt=100&ele=10&date=2026-06-16T12:00:00Z
```

Returns a list of objects with `name`, `el` (elevation°), `az` (azimuth°), `r` (range m), `jy` (flux density).

### `/position`

```
GET /position?date=2026-06-16T12:00:00Z
```

Returns `name`, `ecef` [x,y,z] in meters, `ecef_dot` [vx,vy,vz] in m/s, `jy`.

### `/ephemerides`

```
GET /ephemerides?date=2026-06-16T12:00:00Z
```

Returns `name`, `line1`, `line2` — feed into any SGP4 library to compute positions client-side.

## Clients

### Python client

```sh
cd tart-catalog-client-py
uv sync
uv run python -m tart_client.cli ecef        # ECEF positions
uv run python -m tart_client.cli celestial   # RA/Dec positions
uv run python -m tart_client.cli horizontal  # Az/El positions
```

### Rust client

```sh
cd tart-catalog-client-rs
cargo run --release                          # ECEF (default)
cargo run --release -- celestial             # RA/Dec
cargo run --release -- horizontal            # Az/El
```

See each client's README for full API documentation.

## Testing

```sh
cd tart-catalogue-server
make test-client        # integration testbench
uv run pytest tart_catalogue/ -v  # unit tests
```

See [TESTING.md](TESTING.md) for full instructions.

## Projects

| Directory | Description |
|---|---|
| `tart-catalogue-server/` | Python FastAPI server, Docker, Makefile, tests |
| `tart-catalog-client-py/` | Python client library + CLI |
| `tart-catalog-client-rs/` | Rust client library + CLI |
| `app_skyfield/` | Skyfield-based satellite catalogue tools |
| `test-vectors/` | Astropy-generated reference data for tests |
| `tart-catalogue-server/nginx/` | Nginx reverse proxy config |
