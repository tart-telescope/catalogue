//! TLE propagation and coordinate transforms for the TART catalogue.
//!
//! Pure computation, no I/O: given TLE records (as served by the catalogue's
//! `/ephemerides` endpoint) this crate propagates them with SGP4 and converts
//! the result to ECEF or to azimuth/elevation/range for an observer. The
//! network fetch, the TLE cache and the wasm bindings are all client concerns
//! and live elsewhere — by design, so this core builds anywhere `sgp4` does,
//! including `wasm32-unknown-unknown`.
//!
//! The maths modules are not wasm-gated, so the whole crate is testable with
//! a plain `cargo test`, including parity against the astropy-generated
//! vectors in `tests/`.
//!
//! ```no_run
//! use tart_catalogue_core::propagation::{self, TleRecord};
//!
//! let records: Vec<TleRecord> =
//!     serde_json::from_str(&std::fs::read_to_string("ephemerides.json").unwrap()).unwrap();
//! let (propagators, skipped) = propagation::build_propagators(&records);
//! let rows = propagation::horizontal_positions_at(
//!     &propagators, 1_700_000_000.0, -45.87, 170.60, 100.0, 0.0,
//! );
//! println!("{} usable TLEs, {} skipped, {} above the horizon",
//!     propagators.len(), skipped, rows.len());
//! ```

pub mod geo;
pub mod propagation;
pub mod time;
