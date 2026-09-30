use chrono::{DateTime, Datelike, Timelike, Utc};
use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

use tart_catalogue_core::propagation::{self, Propagator, TleRecord};
use tart_catalogue_core::{geo, time};

/// Raw ECEF position with datetime for further transforms.
#[derive(Debug)]
struct EcefState {
    name: String,
    date: DateTime<Utc>,
    position: [f64; 3],
    velocity: [f64; 3],
    jy: f64,
}

/// ECEF position and velocity (serializable).
#[derive(Debug, serde::Serialize)]
struct EcefPosition {
    name: String,
    date: String,
    ecef_km: [f64; 3],
    velocity_km_s: [f64; 3],
    jy: f64,
}

/// Horizontal (Az/El) position.
#[derive(Debug, serde::Serialize)]
struct HorizontalPosition {
    name: String,
    date: String,
    azimuth_deg: f64,
    elevation_deg: f64,
    range_km: f64,
    jy: f64,
}

/// Celestial (RA/Dec) position.
#[derive(Debug, serde::Serialize)]
struct CelestialPosition {
    name: String,
    date: String,
    ra_hours: f64,
    dec_degrees: f64,
    distance_km: f64,
    jy: f64,
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
    propagator_cache: HashMap<String, Vec<Propagator>>,
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

        // Truncate to hour for cache-friendly server requests
        let dt_hour = date
            .with_minute(0).unwrap()
            .with_second(0).unwrap()
            .with_nanosecond(0).unwrap();
        eprintln!("Fetching ephemerides for {}", dt_hour.to_rfc3339());
        let url = format!(
            "{}/ephemerides?date={}",
            self.base_url,
            dt_hour.to_rfc3339()
        );
        let response = reqwest::get(&url).await?;
        let records: Vec<TleRecord> = response.json().await?;

        cache::save(&dt_hour, &records);
        Ok(records)
    }

    /// Get or build cached SGP4 propagators for a set of TLEs.
    fn get_propagators(&mut self, cache_key: &str, tles: &[TleRecord]) -> Vec<Propagator> {
        if let Some(cached) = self.propagator_cache.get(cache_key) {
            return cached.clone();
        }

        let (propagators, _skipped) = propagation::build_propagators(tles);

        self.propagator_cache
            .insert(cache_key.to_string(), propagators.clone());
        propagators
    }

    /// Propagate every satellite to every date, rotating TEME into ECEF.
    ///
    /// The GMST rotation is computed once per date and shared across every
    /// satellite — the core's shape, applied to velocity as well.
    fn propagate_to_ecef_states(
        propagators: &[Propagator],
        dates: &[DateTime<Utc>],
    ) -> Vec<EcefState> {
        let mut states = Vec::new();

        for date in dates {
            let naive = date.naive_utc();
            let rotation = time::rotation_sin_cos(date.timestamp() as f64);

            for propagator in propagators {
                let Some(teme) = propagation::propagate_teme(propagator, &naive) else {
                    continue;
                };
                states.push(EcefState {
                    name: propagator.name.clone(),
                    date: *date,
                    position: geo::rotate_z_sc(teme.position, rotation),
                    velocity: geo::rotate_z_sc(teme.velocity, rotation),
                    jy: propagator.jy,
                });
            }
        }

        states
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
        Ok(Self::propagate_to_ecef_states(&propagators, dates))
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
            .map(|s| {
                EcefPosition {
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
                    jy: s.jy,
                }
            })
            .collect())
    }

    /// Return the number of satellites available at the given date.
    async fn count_satellites(
        &self,
        query_date: &DateTime<Utc>,
    ) -> Result<usize, Box<dyn Error>> {
        let tles = self.fetch_tles(query_date).await?;
        Ok(tles.len())
    }

    /// Return horizontal (Az/El) positions for a given observer location.
    ///
    /// `lat_deg`, `lon_deg` in degrees, `alt_m` in meters.
    /// `min_el` filters satellites below this elevation (default -90 = all).
    /// `name_pattern` is an optional regex to filter satellite names.
    #[allow(clippy::too_many_arguments)] // mirrors the /catalog query parameters
    async fn horizontal_positions(
        &mut self,
        query_date: &DateTime<Utc>,
        dates: &[DateTime<Utc>],
        lat_deg: f64,
        lon_deg: f64,
        alt_m: f64,
        min_el: f64,
        name_pattern: &Option<regex::Regex>,
    ) -> Result<Vec<HorizontalPosition>, Box<dyn Error>> {
        let tles = self.fetch_tles(query_date).await?;
        let cache_key = cache::cache_key(query_date);
        let propagators = self.get_propagators(&cache_key, &tles);

        // One bulk call: the core computes the rotation and the observer once
        // per instant and shares them across every satellite.
        let times: Vec<f64> = dates.iter().map(|d| d.timestamp() as f64).collect();
        let bulk = propagation::horizontal_positions_bulk(
            &propagators,
            &times,
            lat_deg,
            lon_deg,
            alt_m,
            min_el,
        );

        let mut out = Vec::new();
        for (rows, date) in bulk.into_iter().zip(dates.iter()) {
            for s in rows {
                if let Some(re) = name_pattern
                    && !re.is_match(&s.name)
                {
                    continue;
                }
                out.push(HorizontalPosition {
                    name: s.name,
                    date: date.to_rfc3339(),
                    azimuth_deg: round(s.az_deg, 6),
                    elevation_deg: round(s.el_deg, 6),
                    range_km: round(s.range_km, 3),
                    jy: s.jy,
                });
            }
        }
        Ok(out)
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
                let ang = time::gmst_deg(d.timestamp() as f64).to_radians();
                (*d, ang.sin_cos())
            })
            .collect();

        let mut results = Vec::with_capacity(states.len());
        for s in states {
            let &(s_ang, c_ang) = rev_rotations.get(&s.date).ok_or_else(|| {
                format!("no rotation cached for {}", s.date.to_rfc3339())
            })?;
            let inertial = geo::rotate_z_sc(s.position, (s_ang, c_ang));
            let r = (inertial[0].powi(2) + inertial[1].powi(2) + inertial[2].powi(2)).sqrt();
            let ra = inertial[1].atan2(inertial[0]);
            results.push(CelestialPosition {
                name: s.name,
                date: s.date.to_rfc3339(),
                ra_hours: round(ra_hours(ra), 6),
                dec_degrees: round(dec_from_rad(inertial[2] / r), 6),
                distance_km: round(r, 1),
                jy: s.jy,
            });
        }
        Ok(results)
    }
}

/// Convert a Right Ascension in radians to hours in the range [0, 24).
///
/// `atan2` returns values in [-pi, pi]; wrap so RA never comes out negative.
fn ra_hours(ra_rad: f64) -> f64 {
    (ra_rad.rem_euclid(2.0 * std::f64::consts::PI)).to_degrees() / 15.0
}

/// Convert an elevation/latitude angle in radians to degrees.
fn dec_from_rad(dec_rad: f64) -> f64 {
    dec_rad.to_degrees()
}

fn round(x: f64, decimals: u32) -> f64 {
    let scale = 10f64.powi(decimals as i32);
    (x * scale).round() / scale
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
        "horizontal" | "azel" | "az" => {
            let positions = client.horizontal_positions(&now, &dates, -45.87, 170.60, 100.0, -90.0, &None::<regex::Regex>).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
        "benchmark" | "bench" => {
            let count: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(1000);
            run_benchmark(&client, count).await?;
        }
        _ => {
            let positions = client.ecef_positions(&now, &dates).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
    }

    Ok(())
}

async fn run_benchmark(client: &CatalogueClient, count: usize) -> Result<(), Box<dyn Error>> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    const GPS_TLE_L1: &str = "1 24876U 97035A   24164.50000000  .00000080  00000+0  00000+0 0  9999";
    const GPS_TLE_L2: &str = "2 24876  55.4401 180.3028 0103987  60.0787 301.0966  2.00562231196828";

    /// The reference instant from test-vectors/test_vectors.json, where
    /// astropy puts PRN 13 at az 283.494 deg, el -1.273 deg, range 25778.21 km
    /// for the Dunedin observer. The maths tests live in tart-catalogue-core;
    /// what this one guards is the client's DateTime -> unix-seconds handoff.
    fn test_date() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2024, 6, 12, 12, 0, 0).unwrap()
    }

    fn test_tle() -> TleRecord {
        TleRecord {
            name: "GPS BIIR-2  (PRN 13)".to_string(),
            line1: GPS_TLE_L1.to_string(),
            line2: GPS_TLE_L2.to_string(),
            jy: 0.0,
        }
    }

    fn test_propagators() -> Vec<Propagator> {
        let (propagators, skipped) = propagation::build_propagators(&[test_tle()]);
        assert_eq!(skipped, 0, "the reference TLE should parse");
        propagators
    }

    #[test]
    fn horizontal_matches_the_reference_vector() {
        let propagators = test_propagators();
        let dates = vec![test_date()];

        let states = CatalogueClient::propagate_to_ecef_states(&propagators, &dates);
        assert_eq!(states.len(), 1);

        let observer = geo::geodetic_to_ecef(-45.87, 170.60, 100.0);
        let h = geo::horizontal_from_ecef(states[0].position, observer, -45.87, 170.60);

        assert!((h.az_deg - 283.493_7).abs() < 0.1, "azimuth {}", h.az_deg);
        assert!((h.el_deg - (-1.272_9)).abs() < 0.1, "elevation {}", h.el_deg);
        assert!((h.range_km - 25_778.209).abs() < 1.0, "range {}", h.range_km);
    }

    #[test]
    fn ra_hours_is_wrapped_and_round_survives() {
        // atan2 range is [-pi, pi]; RA must still land in [0, 24).
        let ra_neg = ra_hours(-0.5);
        assert!((0.0..24.0).contains(&ra_neg), "wrapped RA {ra_neg}");
        assert_eq!(round(1.234_567, 4), 1.2346);
    }

    #[test]
    fn dates_from_now_spans_the_day_in_six_hour_steps() {
        let dates = dates_from_now();
        assert_eq!(dates.len(), 5);
        assert!(dates.windows(2).all(|w| w[1] > w[0]));
    }
}
