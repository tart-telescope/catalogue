use chrono::{DateTime, Datelike, Timelike, Utc};
use sgp4::{Constants, Elements, MinutesSinceEpoch};
use std::error::Error;

/// A TLE record as returned by the /ephemerides endpoint.
#[derive(Debug, serde::Deserialize)]
struct TleRecord {
    name: String,
    line1: String,
    line2: String,
}

/// SGP4 prediction in TEME (km, km/s).
#[derive(Debug)]
struct TemeState {
    name: String,
    date: DateTime<Utc>,
    position: [f64; 3],
    velocity: [f64; 3],
}

/// Raw ECEF position with datetime for further transforms.
#[derive(Debug)]
struct EcefState {
    name: String,
    date: DateTime<Utc>,
    position: [f64; 3],
    velocity: [f64; 3],
}

/// ECEF position and velocity (serializable).
#[derive(Debug, serde::Serialize)]
struct EcefPosition {
    name: String,
    date: String,
    ecef_km: [f64; 3],
    velocity_km_s: [f64; 3],
}

/// Celestial (RA/Dec) position.
#[derive(Debug, serde::Serialize)]
struct CelestialPosition {
    name: String,
    date: String,
    ra_hours: f64,
    dec_degrees: f64,
    distance_km: f64,
}

/// Configuration for the catalogue client.
struct CatalogueClient {
    base_url: String,
}

impl CatalogueClient {
    fn new(base_url: &str) -> Self {
        Self {
            base_url: base_url.to_string(),
        }
    }

    /// Fetch raw TLE data from the /ephemerides endpoint.
    async fn fetch_tles(
        &self,
        date: &DateTime<Utc>,
    ) -> Result<Vec<TleRecord>, Box<dyn Error>> {
        let url = format!(
            "{}/ephemerides?date={}",
            self.base_url,
            date.to_rfc3339()
        );
        let response = reqwest::get(&url).await?;
        let records: Vec<TleRecord> = response.json().await?;
        Ok(records)
    }

    /// Propagate all TLEs to a list of dates, returning TEME states.
    fn propagate_tles(tles: &[TleRecord], dates: &[DateTime<Utc>]) -> Vec<TemeState> {
        let mut results = Vec::new();
        let mut skipped = 0u32;

        for tle in tles {
            let elements = match Elements::from_tle(
                Some(tle.name.clone()),
                tle.line1.as_bytes(),
                tle.line2.as_bytes(),
            ) {
                Ok(e) => e,
                Err(e) => {
                    eprintln!("  skip {} (bad TLE): {:?}", tle.name, e);
                    skipped += 1;
                    continue;
                }
            };

            let constants = match Constants::from_elements(&elements) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("  skip {} (bad constants): {:?}", tle.name, e);
                    skipped += 1;
                    continue;
                }
            };

            let tle_epoch_days = elements.epoch();

            for date in dates {
                let date_days = Self::datetime_to_sgp4_days(date);
                let minutes_since_epoch =
                    MinutesSinceEpoch((date_days - tle_epoch_days) * 24.0 * 60.0);

                let prediction = match constants.propagate(minutes_since_epoch) {
                    Ok(p) => p,
                    Err(e) => {
                        eprintln!("  skip {} at {}: {:?}", tle.name, date, e);
                        skipped += 1;
                        continue;
                    }
                };

                results.push(TemeState {
                    name: tle.name.clone(),
                    date: *date,
                    position: prediction.position,
                    velocity: prediction.velocity,
                });
            }
        }

        if skipped > 0 {
            eprintln!("Skipped {} satellite/date combinations", skipped);
        }
        eprintln!(
            "Propagated {} positions from {} TLEs",
            results.len(),
            tles.len()
        );
        results
    }

    /// Convert TEME states to ECEF states.
    fn teme_to_ecef(teme_states: Vec<TemeState>) -> Vec<EcefState> {
        teme_states
            .into_iter()
            .map(|s| {
                let gmst_rad = gmst(&s.date).to_radians();
                let ecef = rotate_z(&s.position, -gmst_rad);
                let vel = rotate_z(&s.velocity, -gmst_rad);
                EcefState {
                    name: s.name,
                    date: s.date,
                    position: ecef,
                    velocity: vel,
                }
            })
            .collect()
    }

    /// Compute ECEF positions for a list of dates (primary computation).
    async fn _propagate_ecef(
        &self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
    ) -> Result<Vec<EcefState>, Box<dyn Error>> {
        let tles = self.fetch_tles(query_date).await?;
        eprintln!("Fetched {} TLE records", tles.len());
        let teme_states = Self::propagate_tles(&tles, dates);
        Ok(Self::teme_to_ecef(teme_states))
    }

    /// Return ECEF positions (km) and velocities (km/s) for all satellites.
    async fn ecef_positions(
        &self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
    ) -> Result<Vec<EcefPosition>, Box<dyn Error>> {
        let states = self._propagate_ecef(query_date, dates).await?;
        Ok(states
            .into_iter()
            .map(|s| EcefPosition {
                name: s.name,
                date: s.date.to_rfc3339(),
                ecef_km: [
                    round(s.position[0], 6),
                    round(s.position[1], 6),
                    round(s.position[2], 6),
                ],
                velocity_km_s: [
                    round(s.velocity[0], 6),
                    round(s.velocity[1], 6),
                    round(s.velocity[2], 6),
                ],
            })
            .collect())
    }

    /// Return celestial (RA/Dec) positions derived from ECEF.
    async fn celestial_positions(
        &self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
    ) -> Result<Vec<CelestialPosition>, Box<dyn Error>> {
        let states = self._propagate_ecef(query_date, dates).await?;
        Ok(states
            .into_iter()
            .map(|s| {
                let gmst_rad = gmst(&s.date).to_radians();
                // ECEF -> inertial by reversing the Earth rotation
                let inertial = rotate_z(&s.position, gmst_rad);
                let r = (inertial[0].powi(2) + inertial[1].powi(2) + inertial[2].powi(2)).sqrt();
                let ra = inertial[1].atan2(inertial[0]);
                let dec = (inertial[2] / r).asin();
                CelestialPosition {
                    name: s.name,
                    date: s.date.to_rfc3339(),
                    ra_hours: round(ra.to_degrees() / 15.0, 6),
                    dec_degrees: round(dec.to_degrees(), 6),
                    distance_km: round(r, 1),
                }
            })
            .collect())
    }

    /// Convert a `DateTime<Utc>` to days since 1949-12-31 00:00 UT (sgp4 epoch).
    fn datetime_to_sgp4_days(dt: &DateTime<Utc>) -> f64 {
        let sgp4_epoch_jd = 2433281.5;
        let jd = julian_day(dt);
        jd - sgp4_epoch_jd
    }
}

/// Rotate a 3-vector around the Z axis by `angle` radians.
fn rotate_z(v: &[f64; 3], angle: f64) -> [f64; 3] {
    let (s, c) = angle.sin_cos();
    [v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]]
}

fn round(x: f64, decimals: u32) -> f64 {
    let scale = 10f64.powi(decimals as i32);
    (x * scale).round() / scale
}

/// Compute the Julian Day for a given UTC datetime.
fn julian_day(dt: &DateTime<Utc>) -> f64 {
    let year = dt.year() as f64;
    let month = dt.month() as f64;
    let day = dt.day() as f64
        + dt.hour() as f64 / 24.0
        + dt.minute() as f64 / 1440.0
        + dt.second() as f64 / 86400.0;

    let a = ((14.0 - month) / 12.0).floor();
    let y = year + 4800.0 - a;
    let m = month + 12.0 * a - 3.0;

    day + ((153.0 * m + 2.0) / 5.0).floor()
        + 365.0 * y
        + (y / 4.0).floor()
        - (y / 100.0).floor()
        + (y / 400.0).floor()
        - 32045.0
}

/// Greenwich Mean Sidereal Time in degrees for a UTC datetime.
fn gmst(dt: &DateTime<Utc>) -> f64 {
    let jd = julian_day(dt);
    let jd0 = (jd + 0.5).floor() - 0.5;
    let t = (jd0 - 2451545.0) / 36525.0;

    let gmst0 = 100.46061837
        + 36000.770053608 * t
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0;

    let frac_day = jd - jd0;
    let gmst_deg = gmst0 + 360.98564736629 * frac_day;

    gmst_deg.rem_euclid(360.0)
}

fn dates_from_now() -> Vec<DateTime<Utc>> {
    let now = Utc::now();
    (0..=24)
        .step_by(6)
        .map(|h| now + chrono::Duration::hours(h))
        .collect()
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let base_url = std::env::var("TART_CATALOGUE_URL")
        .unwrap_or_else(|_| "https://tart.elec.ac.nz/catalog".to_string());

    let client = CatalogueClient::new(&base_url);

    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(|s| s.as_str()).unwrap_or("ecef");

    let now = Utc::now();
    let dates = dates_from_now();

    match cmd {
        "celestial" | "cel" => {
            let positions = client.celestial_positions(&now, &dates).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
        _ => {
            let positions = client.ecef_positions(&now, &dates).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
    }

    Ok(())
}
