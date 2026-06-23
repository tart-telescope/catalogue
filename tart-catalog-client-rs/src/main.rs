use chrono::{DateTime, Datelike, Timelike, Utc};
use sgp4::{Constants, Elements, MinutesSinceEpoch};
use std::collections::HashMap;
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

/// Pre-parsed propagator: (name, Constants, epoch_days).
type CachedPropagator = (String, Constants, f64);

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
    const MAX_DELTA_HOURS: f64 = 12.0;

    fn cache_dir() -> PathBuf {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        PathBuf::from(home).join(".cache").join("tart-catalogue")
    }

    /// Round datetime to the nearest hour for a stable cache key.
    pub fn cache_key(dt: &DateTime<Utc>) -> String {
        format!("{:04}-{:02}-{:02}T{:02}", dt.year(), dt.month(), dt.day(), dt.hour())
    }

    /// Parse a cache filename like '2026-06-16T13.json' into hours since epoch.
    fn parse_cache_hours(name: &str) -> Option<f64> {
        let stem = name.strip_suffix(".json")?;
        let parts: Vec<&str> = stem.split('T').collect();
        if parts.len() != 2 {
            return None;
        }
        let date_parts: Vec<&str> = parts[0].split('-').collect();
        if date_parts.len() != 3 {
            return None;
        }
        let year: i32 = date_parts[0].parse().ok()?;
        let month: u32 = date_parts[1].parse().ok()?;
        let day: u32 = date_parts[2].parse().ok()?;
        let hour: u32 = parts[1].parse().ok()?;
        let days = (year as f64 - 1.0) * 365.25 + (month as f64 - 1.0) * 30.44 + day as f64;
        Some(days * 24.0 + hour as f64)
    }

    /// Convert DateTime<Utc> to approximate hours since year 0.
    fn datetime_to_hours(dt: &DateTime<Utc>) -> f64 {
        let days = (dt.year() as f64 - 1.0) * 365.25
            + (dt.month() as f64 - 1.0) * 30.44
            + dt.day() as f64
            + dt.hour() as f64 / 24.0
            + dt.minute() as f64 / 1440.0;
        days * 24.0
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

    /// Find the nearest cached TLE within 12 hours of dt, or None.
    pub fn load(dt: &DateTime<Utc>) -> Option<Vec<TleRecord>> {
        let dir = cache_dir();
        if !dir.exists() {
            return None;
        }
        let target_hours = datetime_to_hours(dt);
        let mut best_path: Option<PathBuf> = None;
        let mut best_delta: f64 = f64::MAX;

        let entries = match fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => return None,
        };

        for entry in entries.filter_map(|e| e.ok()) {
            let path = entry.path();
            let name = path.file_name()?.to_str()?;
            let cache_hours = parse_cache_hours(name)?;
            let delta = (target_hours - cache_hours).abs();
            if delta < best_delta {
                best_delta = delta;
                best_path = Some(path);
            }
        }

        let path = best_path?;
        if best_delta > MAX_DELTA_HOURS {
            return None;
        }

        let _ = fs::File::open(&path)
            .and_then(|f| f.set_times(fs::FileTimes::new().set_modified(SystemTime::now())));
        let raw = fs::read_to_string(&path).ok()?;
        serde_json::from_str(&raw).ok()
    }

    /// Save TLE records to the cache, evicting LRU if needed.
    pub fn save(dt: &DateTime<Utc>, records: &[TleRecord]) {
        let dir = cache_dir();
        let _ = fs::create_dir_all(&dir);
        let path = dir.join(format!("{}.json", cache_key(dt)));
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
    propagator_cache: HashMap<String, Vec<CachedPropagator>>,
}

impl CatalogueClient {
    fn new(base_url: &str) -> Self {
        Self {
            base_url: base_url.to_string(),
            propagator_cache: HashMap::new(),
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

        eprintln!("Fetching ephemerides for {}", date.to_rfc3339());
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

    /// Get or build cached SGP4 propagators for a set of TLEs.
    fn get_propagators(
        &mut self,
        cache_key: &str,
        tles: &[TleRecord],
    ) -> Vec<CachedPropagator> {
        if let Some(cached) = self.propagator_cache.get(cache_key) {
            return cached.clone();
        }

        let mut propagators = Vec::new();
        for tle in tles {
            let elements = match Elements::from_tle(
                Some(tle.name.clone()),
                tle.line1.as_bytes(),
                tle.line2.as_bytes(),
            ) {
                Ok(e) => e,
                Err(_) => continue,
            };
            let epoch = elements.epoch();
            let constants = match Constants::from_elements(&elements) {
                Ok(c) => c,
                Err(_) => continue,
            };
            propagators.push((tle.name.clone(), constants, epoch));
        }

        self.propagator_cache
            .insert(cache_key.to_string(), propagators.clone());
        propagators
    }

    /// Propagate cached Constants to a list of dates, returning TEME states.
    fn propagate_tles(
        propagators: &[CachedPropagator],
        dates: &[DateTime<Utc>],
    ) -> Vec<TemeState> {
        let mut results = Vec::new();
        let mut skipped = 0u32;

        for (name, constants, tle_epoch_days) in propagators {
            let tle_epoch_days = *tle_epoch_days;

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
                    name: name.clone(),
                    date: *date,
                    position: prediction.position,
                    velocity: prediction.velocity,
                });
            }
        }

        if skipped > 0 {}
        results
    }

    /// Convert TEME states to ECEF states using pre-computed rotations.
    fn teme_to_ecef(teme_states: Vec<TemeState>, rotations: &HashMap<DateTime<Utc>, (f64, f64)>) -> Vec<EcefState> {
        teme_states
            .into_iter()
            .map(|s| {
                let &(s_ang, c_ang) = rotations.get(&s.date).unwrap();
                let ecef = rotate_z_sc(&s.position, s_ang, c_ang);
                let vel = rotate_z_sc(&s.velocity, s_ang, c_ang);
                EcefState {
                    name: s.name,
                    date: s.date,
                    position: ecef,
                    velocity: vel,
                }
            })
            .collect()
    }

    /// Pre-compute GMST rotation (sin, cos) for each date.
    fn precompute_rotations(dates: &[DateTime<Utc>]) -> HashMap<DateTime<Utc>, (f64, f64)> {
        dates.iter().map(|d| {
            let ang = -gmst(d).to_radians();
            (*d, ang.sin_cos())
        }).collect()
    }

    /// Compute ECEF positions for a list of dates (primary computation).
    async fn _propagate_ecef(
        &mut self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
    ) -> Result<Vec<EcefState>, Box<dyn Error>> {
        let tles = self.fetch_tles(query_date).await?;
        let cache_key = cache::cache_key(query_date);
        let propagators = self.get_propagators(&cache_key, &tles);
        let rotations = Self::precompute_rotations(dates);
        let teme_states = Self::propagate_tles(&propagators, dates);
        Ok(Self::teme_to_ecef(teme_states, &rotations))
    }

    /// Return ECEF positions (km) and velocities (km/s) for all satellites.
    async fn ecef_positions(
        &mut self,
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

    /// Return the number of satellites available at the given date.
    async fn count_satellites(
        &mut self,
        query_date: &DateTime<Utc>,
    ) -> Result<usize, Box<dyn Error>> {
        let states = self._propagate_ecef(query_date, &[*query_date]).await?;
        Ok(states.len())
    }

    /// Return celestial (RA/Dec) positions derived from ECEF.
    async fn celestial_positions(
        &mut self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
    ) -> Result<Vec<CelestialPosition>, Box<dyn Error>> {
        let states = self._propagate_ecef(query_date, dates).await?;
        // Pre-compute reverse rotations (ECEF -> inertial)
        let rev_rotations: HashMap<DateTime<Utc>, (f64, f64)> = dates
            .iter()
            .map(|d| {
                let ang = gmst(d).to_radians();
                (*d, ang.sin_cos())
            })
            .collect();
        Ok(states
            .into_iter()
            .map(|s| {
                let &(s_ang, c_ang) = rev_rotations.get(&s.date).unwrap();
                let inertial = rotate_z_sc(&s.position, s_ang, c_ang);
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

/// Rotate a 3-vector around the Z axis using pre-computed (sin, cos).
fn rotate_z_sc(v: &[f64; 3], s: f64, c: f64) -> [f64; 3] {
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

    let mut client = CatalogueClient::new(&base_url);

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
            let count: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(1000);
            run_benchmark(&mut client, count).await?;
        }
        _ => {
            let positions = client.ecef_positions(&now, &dates).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
    }

    Ok(())
}

async fn run_benchmark(client: &mut CatalogueClient, count: usize) -> Result<(), Box<dyn Error>> {
    let n = count.max(1);
    let now = Utc::now();
    let week_ago = now - chrono::Duration::days(7);
    let step = (now - week_ago) / n as i32;

    let start = Instant::now();
    let mut total_positions = 0usize;
    for i in 0..n {
        let dt = week_ago + step * i as i32;
        total_positions += client.count_satellites(&dt).await?;
    }
    let elapsed = start.elapsed();
    let secs = elapsed.as_secs_f64();

    let result = serde_json::json!({
        "server": client.base_url,
        "queries": n,
        "total_positions": total_positions,
        "elapsed_s": (secs * 100.0).round() / 100.0,
        "positions_per_sec": (total_positions as f64 / secs).round() as u64,
        "queries_per_sec": ((n as f64 / secs) * 10.0).round() / 10.0,
        "avg_query_ms": ((secs / n as f64 * 1000.0) * 10.0).round() / 10.0,
        "cache_entries": cache::count(),
    });
    println!("{}", serde_json::to_string_pretty(&result)?);

    Ok(())
}
