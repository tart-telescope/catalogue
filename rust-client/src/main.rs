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

/// A computed satellite position in TEME (km, km/s).
#[derive(Debug, serde::Serialize)]
struct SatellitePosition {
    name: String,
    date: String,
    /// TEME position [x, y, z] in km.
    position_km: [f64; 3],
    /// TEME velocity [vx, vy, vz] in km/s.
    velocity_km_s: [f64; 3],
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
    async fn fetch_ephemerides(
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

    /// Fetch TLEs and compute positions for a list of dates.
    async fn compute_positions(
        &self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
    ) -> Result<Vec<SatellitePosition>, Box<dyn Error>> {
        let tles = self.fetch_ephemerides(query_date).await?;
        eprintln!("Fetched {} TLE records", tles.len());

        let mut all_positions = Vec::new();
        let mut skipped = 0u32;

        for tle in &tles {
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

                let position = prediction.position;
                let velocity = prediction.velocity;

                all_positions.push(SatellitePosition {
                    name: tle.name.clone(),
                    date: date.to_rfc3339(),
                    position_km: [position[0], position[1], position[2]],
                    velocity_km_s: [velocity[0], velocity[1], velocity[2]],
                });
            }
        }

        if skipped > 0 {
            eprintln!("Skipped {} satellite/date combinations", skipped);
        }
        eprintln!(
            "Computed {} positions from {} TLEs",
            all_positions.len(),
            tles.len()
        );

        Ok(all_positions)
    }

    /// Convert a `DateTime<Utc>` to days since 1949-12-31 00:00 UT (sgp4 epoch).
    fn datetime_to_sgp4_days(dt: &DateTime<Utc>) -> f64 {
        let sgp4_epoch_jd = 2433281.5;
        let jd = julian_day(dt);
        jd - sgp4_epoch_jd
    }
}

/// Compute the Julian Day for a given UTC datetime.
fn julian_day(dt: &DateTime<Utc>) -> f64 {
    let year = dt.year() as f64;
    let month = dt.month() as f64;
    let day = dt.day() as f64
        + dt.hour() as f64 / 24.0
        + dt.minute() as f64 / 1440.0
        + dt.second() as f64 / 86400.0;

    let a = (14.0 - month) / 12.0;
    let y = year + 4800.0 - a;
    let m = month + 12.0 * a - 3.0;

    day + (153.0 * m + 2.0) / 5.0
        + 365.0 * y
        + (y / 4.0).floor()
        - (y / 100.0).floor()
        + (y / 400.0).floor()
        - 32045.0
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let base_url = std::env::var("TART_CATALOGUE_URL")
        .unwrap_or_else(|_| "https://tart.elec.ac.nz/catalog".to_string());

    let client = CatalogueClient::new(&base_url);

    let now = Utc::now();

    let dates: Vec<DateTime<Utc>> = (0..=24)
        .step_by(6)
        .map(|h| now + chrono::Duration::hours(h))
        .collect();

    let positions = client.compute_positions(&now, &dates).await?;

    let json = serde_json::to_string_pretty(&positions)?;
    println!("{}", json);

    Ok(())
}
