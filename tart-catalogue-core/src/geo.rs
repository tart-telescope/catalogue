//! Geodetic, ECEF and local topocentric (ENU) conversions.

/// WGS84 semi-major axis, km.
pub const WGS84_A_KM: f64 = 6378.137;
/// WGS84 flattening.
pub const WGS84_F: f64 = 1.0 / 298.257223563;

/// Observer geodetic coordinates to ECEF, in km.
///
/// `alt_m` is metres above the ellipsoid, matching the catalogue API.
pub fn geodetic_to_ecef(lat_deg: f64, lon_deg: f64, alt_m: f64) -> [f64; 3] {
    let (slat, clat) = lat_deg.to_radians().sin_cos();
    let (slon, clon) = lon_deg.to_radians().sin_cos();

    let e2 = WGS84_F * (2.0 - WGS84_F);
    let n = WGS84_A_KM / (1.0 - e2 * slat * slat).sqrt();
    let alt_km = alt_m / 1000.0;

    [
        (n + alt_km) * clat * clon,
        (n + alt_km) * clat * slon,
        (n * (1.0 - e2) + alt_km) * slat,
    ]
}

/// Rotate a 3-vector around the Z axis using pre-computed `(sin, cos)`.
///
/// Public because the same rotation moves both a TEME position and its
/// velocity into ECEF, and callers with both should compute the angle once.
pub fn rotate_z_sc(v: [f64; 3], (s, c): (f64, f64)) -> [f64; 3] {
    [v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]]
}

/// Topocentric look angles for one satellite.
#[derive(Debug, Clone, Copy)]
pub struct Horizontal {
    pub az_deg: f64,
    pub el_deg: f64,
    pub range_km: f64,
}

/// Satellite ECEF position to azimuth/elevation/range from an observer.
///
/// This is the whole geometric core of the module and is deliberately free of
/// any SGP4 or time concerns so it can be tested against astropy vectors
/// directly.
pub fn horizontal_from_ecef(
    sat_ecef: [f64; 3],
    obs_ecef: [f64; 3],
    lat_deg: f64,
    lon_deg: f64,
) -> Horizontal {
    let (slat, clat) = lat_deg.to_radians().sin_cos();
    let (slon, clon) = lon_deg.to_radians().sin_cos();

    let dx = sat_ecef[0] - obs_ecef[0];
    let dy = sat_ecef[1] - obs_ecef[1];
    let dz = sat_ecef[2] - obs_ecef[2];

    // East / North / Up basis at the observer.
    let east = -slon * dx + clon * dy;
    let north = -slat * clon * dx - slat * slon * dy + clat * dz;
    let up = clat * clon * dx + clat * slon * dy + slat * dz;

    let range_km = (east * east + north * north + up * up).sqrt();

    Horizontal {
        az_deg: east.atan2(north).to_degrees().rem_euclid(360.0),
        el_deg: if range_km > 0.0 {
            (up / range_km).asin().to_degrees()
        } else {
            0.0
        },
        range_km,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dunedin_is_on_the_surface() {
        let p = geodetic_to_ecef(-45.87, 170.6, 100.0);
        let r = (p[0] * p[0] + p[1] * p[1] + p[2] * p[2]).sqrt();
        assert!((6360.0..6390.0).contains(&r), "radius {r} km");
        assert!(p[2] < 0.0, "southern hemisphere should have z < 0");
    }

    #[test]
    fn a_satellite_straight_up_is_at_the_zenith() {
        let obs = geodetic_to_ecef(-45.87, 170.6, 100.0);
        let (slat, clat) = (-45.87_f64).to_radians().sin_cos();
        let (slon, clon) = 170.6_f64.to_radians().sin_cos();

        // 2000 km along the local Up vector.
        let up = [clat * clon, clat * slon, slat];
        let sat = [
            obs[0] + 2000.0 * up[0],
            obs[1] + 2000.0 * up[1],
            obs[2] + 2000.0 * up[2],
        ];

        let h = horizontal_from_ecef(sat, obs, -45.87, 170.6);
        assert!((h.el_deg - 90.0).abs() < 1e-6, "elevation {}", h.el_deg);
        assert!((h.range_km - 2000.0).abs() < 1e-6, "range {}", h.range_km);
    }

    #[test]
    fn azimuth_is_wrapped_to_one_turn() {
        let obs = [6378.137, 0.0, 0.0];
        let h = horizontal_from_ecef([6378.137, 100.0, 0.0], obs, 0.0, 0.0);
        assert!((0.0..360.0).contains(&h.az_deg), "azimuth {}", h.az_deg);
    }

    #[test]
    fn rotation_round_trips() {
        let v = [10_000.0, 20_000.0, 30_000.0];
        let angle = 1.234_f64;
        let (s, c) = angle.sin_cos();
        let there = rotate_z_sc(v, (s, c));
        let back = rotate_z_sc(there, (-s, c));
        for axis in 0..3 {
            assert!(
                (back[axis] - v[axis]).abs() < 1e-9,
                "axis {axis}: {back:?} != {v:?}"
            );
        }
    }
}
