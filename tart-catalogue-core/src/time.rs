//! Time conversions and sidereal time.
//!
//! Everything here takes unix epoch seconds, which is how time crosses the
//! wasm boundary and the one scale the whole query path stays on. Keeping it
//! that way avoids date parsing (and therefore a date-parsing dependency) in
//! the hot path.

/// Julian Date of the unix epoch, 1970-01-01T00:00:00Z.
pub const UNIX_EPOCH_JD: f64 = 2_440_587.5;

/// Julian Day from unix epoch seconds, preserving the fractional part.
pub fn unix_to_jd(unix_secs: f64) -> f64 {
    unix_secs / 86_400.0 + UNIX_EPOCH_JD
}

/// Greenwich Mean Sidereal Time in degrees, wrapped to `[0, 360)`.
///
/// Vallado, *Fundamentals of Astrodynamics and Applications*, eq. 3-47. The
/// result is expressed in seconds of time and divided by 240 to get degrees.
pub fn gmst_deg(unix_secs: f64) -> f64 {
    let t = (unix_to_jd(unix_secs) - 2_451_545.0) / 36_525.0;
    let gmst_secs = 67_310.548_41
        + (876_600.0 * 3600.0 + 8_640_184.812_866) * t
        + 0.093_104 * t * t
        - 6.2e-6 * t * t * t;

    // seconds of time -> degrees
    (gmst_secs / 240.0).rem_euclid(360.0)
}

/// `(sin, cos)` of the TEME -> ECEF Z-rotation angle, `-GMST` in radians.
///
/// Precomputed once per instant and shared across every satellite, which is
/// the dominant saving over rotating each satellite independently.
pub fn rotation_sin_cos(unix_secs: f64) -> (f64, f64) {
    (-gmst_deg(unix_secs)).to_radians().sin_cos()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_epoch_maps_to_the_right_julian_day() {
        assert!((unix_to_jd(0.0) - UNIX_EPOCH_JD).abs() < 1e-9);
        // 2000-01-01T12:00:00Z is J2000 by definition.
        assert!((unix_to_jd(946_728_000.0) - 2_451_545.0).abs() < 1e-6);
    }

    #[test]
    fn gmst_stays_in_range_and_advances() {
        let t0 = 1_700_000_000.0;
        let g0 = gmst_deg(t0);
        assert!((0.0..360.0).contains(&g0), "gmst out of range: {g0}");

        // Sidereal rate is ~360.9856 deg/day, so ~90.246 deg per 6 hours.
        let g6 = gmst_deg(t0 + 6.0 * 3600.0);
        let delta = (g6 - g0).rem_euclid(360.0);
        assert!(
            (89.0..91.0).contains(&delta),
            "unexpected 6h GMST advance: {delta}"
        );
    }
}
