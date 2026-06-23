# Changelog

## v0.4.0

### Added
- `horizontal_positions()` method in both clients: ECEF → ENU → Az/El using WGS84 ellipsoid
- `geodetic_to_ecef()` helper in Rust for observer position computation
- Astropy-generated test vectors in `test-vectors/test_vectors.json` with TEME, ECEF, celestial, and horizontal reference values for 4 dates
- Coordinate transform tests: ECEF vs astropy TEME→ITRS, celestial vs astropy ITRS→ICRS, horizontal vs astropy AltAz
- GMST, Julian day, rotation matrix, and SGP4 orbital property tests in Rust
- `cargo test` in `publish-crate.yml` CI workflow

### Changed
- Caching now uses nearest-match within 12 hours instead of per-file staleness
- `count_satellites()` in Rust no longer does full SGP4 propagation (was 1000× slower than Python)
- Benchmark outputs JSON instead of plain text, includes `cache_entries` field
- Both client READMEs updated with full API documentation and coordinate transform tables

## v0.3.3

### Added
- `tart-catalog-client-py`: Python client fetching TLEs from `/ephemerides` and computing ECEF or celestial (RA/Dec) positions via SGP4 propagation
- `tart-catalog-client-rs`: Rust client with same ECEF/celestial output, using `sgp4` crate and GMST rotation
- `benchmark` subcommand in both clients: measures throughput with configurable iteration count, reports positions/sec, queries/sec, avg query time, and cache size
- `count_satellites()` method in both clients for fast satellite count without position computation
- `CatalogueClient` library API for both clients with `fetch_tles()`, `ecef_positions()`, `celestial_positions()`

### Changed
- Moved server package into `tart-catalogue/` subdirectory for cleaner project layout
- Renamed client directories from `python-client`/`rust-client` to `tart-catalog-client-py`/`tart-catalog-client-rs`
- Renamed packages to `tart-catalogue-client` for consistency
- Both clients default to `https://tart.elec.ac.nz/catalog` when `TART_CATALOGUE_URL` is unset
- Celestial positions are derived from ECEF positions (not computed independently)
- Migrated server from Poetry to `uv` for dependency management

### Performance
- Local ephemerides cache in `~/.cache/tart-catalogue/` with 12-hour freshness window and LRU eviction at 100 entries
- Nearest-match cache lookup: any cached entry within 12 hours of requested time is reused
- In-memory cache of pre-parsed SGP4 propagators avoids re-parsing TLE lines on repeated calls
- Pre-computed GMST rotation matrices per date in Rust, avoiding redundant trig per satellite
- Direct TEME→ECEF rotation in Python, replacing heavy astropy coordinate frame transform

### Fixed
- Circular import in `tart_catalogue` package exposed by uv editable install
- Dockerfile: `uvicorn` not found (switched to `uv run uvicorn`)
- Rust `count_satellites()` was doing full SGP4 propagation (now just counts TLE records)
- `Satrec` objects not pickleable (switched to in-memory dict cache)

## v0.3.2

### Changed
- Bumped version across all packages
- Updated README and TESTING documentation

## v0.3.0

- Initial uv-based release with FastAPI server, app_skyfield tools, and GitHub Actions publishing
