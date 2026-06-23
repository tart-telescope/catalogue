# TART Object Position Server

REST API providing positions of GNSS satellites and other objects visible to the
[TART](https://github.com/tmolteno/TART) radio telescope.

Live instance: `https://tart.elec.ac.nz/catalog`

## Quick start

```sh
# Install dependencies
uv sync

# Run the server
uv run uvicorn tart_catalogue.main:app --host 0.0.0.0 --port 8876
```

Open `http://localhost:8876/docs` for the interactive API docs.

## Docker

```sh
docker compose build
docker compose up -d
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

Query the visible sky from a location.

```
GET /catalog?lat=-45.87&lon=170.60&alt=100&ele=10&date=2026-06-16T12:00:00Z
```

Returns a list of objects with `name`, `el` (elevation°), `az` (azimuth°), `r` (range m), `jy` (flux density).

### `/position`

ECEF positions for all satellite constellations.

```
GET /position?date=2026-06-16T12:00:00Z
```

Returns `name`, `ecef` [x,y,z] in meters, `ecef_dot` [vx,vy,vz] in m/s, `jy`.

### `/ephemerides`

Raw TLE (Two-Line Element) data for client-side SGP4 propagation.

```
GET /ephemerides?date=2026-06-16T12:00:00Z
```

Returns `name`, `line1`, `line2` — feed these into any SGP4 library to compute
positions without further server calls.

## Rust client

A fast Rust binary that calls `/ephemerides` and propagates positions locally:

```sh
cd tart-catalog-client-rs
cargo run --release
```

See `tart-catalog-client-rs/` for details.

## Testing

See [TESTING.md](TESTING.md).

## Projects

| Directory | Description |
|---|---|
| `tart-catalogue-server/` | Python FastAPI server (Dockerfile, compose.yml) |
| `app_skyfield/` | Skyfield-based satellite catalogue tools |
| `tart-catalog-client-py/` | Python CLI/library client |
| `tart-catalog-client-rs/` | Rust CLI client |
| `nginx/` | Nginx reverse proxy config |
| `test/` | Integration testbench |
