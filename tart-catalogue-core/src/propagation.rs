//! TLE parsing and SGP4 propagation to ECEF and horizontal coordinates.

use crate::geo::{self, Horizontal};
use crate::time::rotation_sin_cos;
use serde::{Deserialize, Serialize};
use sgp4::{Constants, Elements};

/// One record from the catalogue `/ephemerides` endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TleRecord {
    pub name: String,
    pub line1: String,
    pub line2: String,
    /// Ionospheric flux, carried through for response-shape parity.
    #[serde(default)]
    pub jy: f64,
}

/// A parsed TLE with its SGP4 constants built once and reused for every query.
///
/// Building `Constants` is the expensive part of propagation, so this must be
/// constructed once per TLE set rather than per request. `Clone` because
/// clients cache the built set per TLE file.
#[derive(Clone)]
pub struct Propagator {
    pub name: String,
    pub jy: f64,
    elements: Elements,
    constants: Constants,
}

/// SGP4 prediction in TEME, km and km/s.
#[derive(Debug, Clone, Copy)]
pub struct TemeState {
    pub position: [f64; 3],
    pub velocity: [f64; 3],
}

/// Look angles for one satellite at one instant.
#[derive(Debug, Clone)]
pub struct SatelliteHorizontal {
    pub name: String,
    pub az_deg: f64,
    pub el_deg: f64,
    pub range_km: f64,
    pub jy: f64,
}

impl Propagator {
    /// Parse one TLE. Returns `None` for a malformed record rather than
    /// failing the whole set, so one bad satellite cannot blank the sky.
    pub fn from_tle(record: &TleRecord) -> Option<Self> {
        let elements = Elements::from_tle(
            Some(record.name.clone()),
            record.line1.as_bytes(),
            record.line2.as_bytes(),
        )
        .ok()?;
        let constants = Constants::from_elements(&elements).ok()?;

        Some(Self {
            name: record.name.clone(),
            jy: record.jy,
            elements,
            constants,
        })
    }
}

/// Build propagators for a whole TLE set.
///
/// Returns the propagators and the number of records that failed to parse.
pub fn build_propagators(records: &[TleRecord]) -> (Vec<Propagator>, usize) {
    let mut propagators = Vec::with_capacity(records.len());
    let mut skipped = 0usize;

    for record in records {
        match Propagator::from_tle(record) {
            Some(p) => propagators.push(p),
            None => skipped += 1,
        }
    }

    (propagators, skipped)
}

/// Unpack unix seconds into the (seconds, nanoseconds) pair chrono wants,
/// keeping sub-second precision.
fn unix_to_naive_utc(unix_secs: f64) -> Option<chrono::NaiveDateTime> {
    let secs = unix_secs.floor();
    let mut nanos = ((unix_secs - secs) * 1e9).round() as i64;

    let mut secs = secs as i64;
    if nanos >= 1_000_000_000 {
        nanos -= 1_000_000_000;
        secs += 1;
    }

    chrono::DateTime::from_timestamp(secs, nanos.max(0) as u32).map(|dt| dt.naive_utc())
}

/// Propagate one satellite to one instant, returning the raw TEME state.
///
/// Callers that only want positions should prefer [`propagate_to_ecef`] or
/// [`horizontal_positions_at`]; this exists for consumers that need velocity
/// or an inertial frame.
pub fn propagate_teme(
    propagator: &Propagator,
    datetime: &chrono::NaiveDateTime,
) -> Option<TemeState> {
    // Use sgp4's own helper rather than arithmetic of our own. The upstream
    // `tart-catalogue-client` once subtracted `Elements::epoch()` (*years*
    // since J2000) from a Julian Day offset in *days* — dimensionally invalid,
    // and worth about 74 years. Its tests passed regardless because the test
    // TLE has zero drag, so the radial assertion never noticed the phase
    // error. Reported as tart-telescope/catalogue#9 and fixed in 25172fb,
    // which now calls this same helper; the note stays so nobody reintroduces
    // the arithmetic.
    let minutes = propagator
        .elements
        .datetime_to_minutes_since_epoch(datetime)
        .ok()?;
    let prediction = propagator.constants.propagate(minutes).ok()?;

    Some(TemeState {
        position: prediction.position,
        velocity: prediction.velocity,
    })
}

/// Propagate one satellite to one instant, returning its ECEF position in km.
///
/// `rotation` is `rotation_sin_cos(unix_secs)`, computed once per instant and
/// shared across all satellites.
pub fn propagate_to_ecef(
    propagator: &Propagator,
    datetime: &chrono::NaiveDateTime,
    rotation: (f64, f64),
) -> Option<[f64; 3]> {
    let teme = propagate_teme(propagator, datetime)?;
    Some(geo::rotate_z_sc(teme.position, rotation))
}

/// Horizontal positions for every propagator at a single instant.
///
/// `min_el_deg` filters in the same loop, matching the server, which defaults
/// to 0.0 on `/catalog`. Pass `-90.0` to keep everything (used by tests, since
/// one of the reference vectors sits below the horizon).
pub fn horizontal_positions_at(
    propagators: &[Propagator],
    unix_secs: f64,
    lat_deg: f64,
    lon_deg: f64,
    alt_m: f64,
    min_el_deg: f64,
) -> Vec<SatelliteHorizontal> {
    let Some(datetime) = unix_to_naive_utc(unix_secs) else {
        return Vec::new();
    };

    // Once per instant, not once per satellite — this is the main structural
    // saving over rotating and re-deriving the observer per satellite.
    let rotation = rotation_sin_cos(unix_secs);
    let observer = geo::geodetic_to_ecef(lat_deg, lon_deg, alt_m);

    let mut out = Vec::with_capacity(propagators.len());

    for propagator in propagators {
        let Some(ecef) = propagate_to_ecef(propagator, &datetime, rotation) else {
            continue;
        };
        let Horizontal {
            az_deg,
            el_deg,
            range_km,
        } = geo::horizontal_from_ecef(ecef, observer, lat_deg, lon_deg);

        if el_deg >= min_el_deg {
            out.push(SatelliteHorizontal {
                name: propagator.name.clone(),
                az_deg,
                el_deg,
                range_km,
                jy: propagator.jy,
            });
        }
    }

    out
}

/// Horizontal positions for every propagator at every instant.
///
/// One row per input instant, each row one [`SatelliteHorizontal`] per
/// satellite above `min_el_deg`. The TEME -> ECEF rotation is computed once
/// per instant and shared across every satellite; that loop shape is what
/// makes a scrub across a whole visibility history fast enough to be
/// interactive, which is why it lives here rather than in a client.
pub fn horizontal_positions_bulk(
    propagators: &[Propagator],
    unix_secs: &[f64],
    lat_deg: f64,
    lon_deg: f64,
    alt_m: f64,
    min_el_deg: f64,
) -> Vec<Vec<SatelliteHorizontal>> {
    unix_secs
        .iter()
        .map(|&t| horizontal_positions_at(propagators, t, lat_deg, lon_deg, alt_m, min_el_deg))
        .collect()
}
