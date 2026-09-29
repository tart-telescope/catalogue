# Changelog

## Unreleased

### Fixed
- Rust client: two date bugs made every position wrong (issue #9)
  - the SGP4 propagation interval mixed `Elements::epoch()` (years since
    J2000) with days since 1949-12-31, a ~74-year error; propagation now uses
    `Elements::datetime_to_minutes_since_epoch()`
  - `julian_day()` returned a Julian Day *Number* (noon-based) where a Julian
    *Date* was expected, rotating GMST by ~180.5°; it is now midnight-based
  - new regression tests pin the client to the astropy reference vectors in
    `test-vectors/test_vectors.json` (0.05° tolerance); both bugs would have
    been caught by these tests
- Server `FileCache`: download throttling is per target file and only applies
  to failed attempts (issue #5). The throttle used to be keyed by the
  CelesTrak URL, which is date-independent: in a multi-date `/bulk_az_el`
  request only the first date downloaded its own TLEs, and every other date
  silently fell back to the wrong day's data
- Server `FileCache`: TLE data downloaded for a past date is also filed under
  its own TLE epoch (with a logged warning) instead of masquerading as the
  requested day (issue #5); downloads are written atomically so a failed
  transfer leaves no partial file
- `/bulk_az_el`: debug `print` replaced with logging

### Added
- Optional `code` key for satellites (issue #4): `"E11"` (Galileo, official
  GSC SV ID table), `"C14"` (BeiDou), `"PRN 13"` (GPS), QZSS PRN codes; the
  key is omitted where no guaranteed match exists. Included in `/catalog`,
  `/position`, `/bulk_az_el` and `/ephemerides`, and passed through by both
  clients
- `/bulk_az_el`: optional `elevation` filter (parity with `/catalog`)
- Test harnesses for the bulk endpoint and the ephemeris file cache
  (issues #1, #5): offline tests for per-date data isolation, download
  throttling regressions, determinism across server restarts, bulk==catalog
  parity, and a full server-chain check against the astropy reference vectors
  (measured agreement ~0.002°)

### Changed
- Python client `celestial_positions`: the ITRS -> ICRS transform now runs once
  over all satellites at their shared epoch instead of once per satellite; the
  outputs are unchanged (0 mismatches over 140 live TLEs and the pinned
  astropy-vector tests) and a call for 140 satellites drops from ~3 s to ~45 ms.
  This removes ~18 s from a tart2ms `--add-model` conversion
  (tart-telescope/tart2ms#53).

## v0.5.2

### Fixed
- Server `/bulk_az_el`: invalid or >24h-future dates now return HTTP 400 instead
  of being re-wrapped as a 500 error
- Server `FileCache`: `get_object()` recursion on repeated download failure is
  now bounded (raises `RuntimeError` after 5 days of fallback); `os.makedirs()`
  uses `exist_ok=True` instead of silently swallowing errors
- Python client: `count_satellites()` with no `dt` argument no longer crashes
- Rust client: celestial Right Ascension is normalized to the [0, 24) hour range
  (was emitting negative values for roughly half of satellites); replaced
  panic-prone `.unwrap()` map lookups with descriptive errors

## v0.5.1

### Fixed
- Python client: prevent astropy from attempting IERS table downloads
  (`iers.conf.auto_max_age = None` alongside `auto_download = False`)

## v0.5.0

### Added
- `min_elevation` and `name_regex` filter parameters to `horizontal_positions()` in both clients
- Server: flux data loaded from `flux.json`, included as `jy` field in `/ephemerides` response
- Server: bug fixes (Pydantic v2 deprecation, /position error handler, stale debug log)

## v0.4.1

### Added
- `horizontal` CLI subcommand in both clients with configurable observer location

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
