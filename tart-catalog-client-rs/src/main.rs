use chrono::{DateTime, Datelike, Timelike, Utc};
use sgp4::{Constants, Elements, MinutesSinceEpoch};
use std::error::Error;
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

/// A TLE record as returned by the /ephemerides endpoint.
#[derive(Debug, serde::Deserialize, serde::Serialize)]
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

/// Local cache of ephemerides in ~/.cache/tart-catalogue/
mod cache {
    use super::*;
    use std::time::SystemTime;

    const MAX_ENTRIES: usize = 100;
    const STALE_SECS: u64 = 12 * 3600;

    fn cache_dir() -> PathBuf {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        PathBuf::from(home).join(".cache").join("tart-catalogue")
    }

    /// Round datetime to the nearest hour for a stable cache key.
    fn cache_key(dt: &DateTime<Utc>) -> String {
        format!("{:04}-{:02}-{:02}T{:02}", dt.year(), dt.month(), dt.day(), dt.hour())
    }

    fn cache_path(dt: &DateTime<Utc>) -> PathBuf {
        cache_dir().join(format!("{}.json", cache_key(dt)))
    }

    /// Remove least recently used cache files if over the limit.
    fn evict_lru() {
        let dir = cache_dir();
        if !dir.exists() {
            return;
        }
        let mut files: Vec<_> = match fs::read_dir(&dir) {
            Ok(entries) => entries
                .filter_map(|e| e.ok())
                .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
                .collect(),
            Err(_) => return,
        };
        if files.len() <= MAX_ENTRIES {
            return;
        }
        files.sort_by_key(|e| e.metadata().ok().and_then(|m| m.modified().ok()));
        for entry in files.iter().take(files.len() - MAX_ENTRIES) {
            let _ = fs::remove_file(entry.path());
        }
    }

    /// Load cached TLE records if fresh, returning None if missing or stale.
    pub fn load(dt: &DateTime<Utc>) -> Option<Vec<TleRecord>> {
        let path = cache_path(dt);
        if !path.exists() {
            return None;
        }
        let meta = path.metadata().ok()?;
        let age = SystemTime::now()
            .duration_since(meta.modified().ok()?)
            .ok()?
            .as_secs();
        if age > STALE_SECS {
            let _ = fs::remove_file(&path);
            return None;
        }
        // Touch to update LRU
        let _ = fs::File::open(&path).and_then(|f| f.set_times(fs::FileTimes::new().set_modified(std::time::SystemTime::now())));
        let raw = fs::read_to_string(&path).ok()?;
        serde_json::from_str(&raw).ok()
    }

    /// Save TLE records to the cache, evicting LRU if needed.
    pub fn save(dt: &DateTime<Utc>, records: &[TleRecord]) {
        let dir = cache_dir();
        let _ = fs::create_dir_all(&dir);
        let path = cache_path(dt);
        if let Ok(json) = serde_json::to_string(records) {
            let _ = fs::write(&path, json);
        }
        evict_lru();
    }

    /// Count the number of cached entries.
    pub fn count() -> usize {
        let dir = cache_dir();
        if !dir.exists() {
            return 0;
        }
        fs::read_dir(&dir)
            .map(|entries| {
                entries
                    .filter_map(|e| e.ok())
                    .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
                    .count()
            })
            .unwrap_or(0)
    }
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

    /// Fetch raw TLE data from the /ephemerides endpoint, with local caching.
    async fn fetch_tles(
        &self,
        date: &DateTime<Utc>,
    ) -> Result<Vec<TleRecord>, Box<dyn Error>> {
        if let Some(cached) = cache::load(date) {
            return Ok(cached);
        }

        let url = format!(
            "{}/ephemerides?date={}",
            self.base_url,
            date.to_rfc3339()
        );
        let response = reqwest::get(&url).await?;
        let records: Vec<TleRecord> = response.json().await?;

        cache::save(date, &records);
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
                Err(_e) => {
                    skipped += 1;
                    continue;
                }
            };

            let constants = match Constants::from_elements(&elements) {
                Ok(c) => c,
                Err(_e) => {
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
                    Err(_e) => {
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
        }
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
        "benchmark" | "bench" => {
            run_benchmark(&client).await?;
        }
        _ => {
            let positions = client.ecef_positions(&now, &dates).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
    }

    Ok(())
}

async fn run_benchmark(client: &CatalogueClient) -> Result<(), Box<dyn Error>> {
    const N: usize = 10_000;
    let now = Utc::now();
    let week_ago = now - chrono::Duration::days(7);
    let step = (now - week_ago) / N as i32;

    let start = Instant::now();
    let mut total_positions = 0usize;
    for i in 0..N {
        let dt = week_ago + step * i as i32;
        let positions = client.celestial_positions(&dt, &[dt]).await?;
        total_positions += positions.len();
    }
    let elapsed = start.elapsed();
    let secs = elapsed.as_secs_f64();

    let result = serde_json::json!({
        "server": client.base_url,
        "queries": N,
        "total_positions": total_positions,
        "elapsed_s": (secs * 100.0).round() / 100.0,
        "positions_per_sec": (total_positions as f64 / secs).round() as u64,
        "queries_per_sec": ((N as f64 / secs) * 10.0).round() / 10.0,
        "avg_query_ms": ((secs / N as f64 * 1000.0) * 10.0).round() / 10.0,
        "cache_entries": cache::count(),
    });
    println!("{}", serde_json::to_string_pretty(&result)?);

    Ok(())
}
