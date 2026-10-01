use chrono::{DateTime, Datelike, Timelike, Utc};
use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

use tart_catalogue_core::propagation::{self, Propagator};
use tart_catalogue_core::{geo, time};

/// A TLE record as returned by the /ephemerides endpoint.
///
/// The client's wire record carries the optional satellite `code` the server
/// attaches (issue #4); the core's minimal record does not, so propagation
/// converts with [`TleRecord::as_core`] and the code travels alongside by
/// name.
#[derive(Debug, serde::Deserialize, serde::Serialize)]
struct TleRecord {
    name: String,
    line1: String,
    line2: String,
    #[serde(default)]
    jy: f64,
    /// Optional GNSS code (e.g. "E11", "C14", "PRN 13"), passed through
    /// from the server (issue #4).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    code: Option<String>,
}

impl TleRecord {
    fn as_core(&self) -> propagation::TleRecord {
        propagation::TleRecord {
            name: self.name.clone(),
            line1: self.line1.clone(),
            line2: self.line2.clone(),
            jy: self.jy,
        }
    }
}

/// Raw ECEF position with datetime for further transforms.
#[derive(Debug)]
struct EcefState {
    name: String,
    date: DateTime<Utc>,
    position: [f64; 3],
    velocity: [f64; 3],
    jy: f64,
    code: Option<String>,
}

/// ECEF position and velocity (serializable).
#[derive(Debug, serde::Serialize)]
struct EcefPosition {
    name: String,
    date: String,
    ecef_km: [f64; 3],
    velocity_km_s: [f64; 3],
    jy: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    code: Option<String>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    code: Option<String>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    code: Option<String>,
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

        let core_records: Vec<_> = tles.iter().map(TleRecord::as_core).collect();
        let (propagators, _skipped) = propagation::build_propagators(&core_records);

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
        code_by_name: &HashMap<String, Option<String>>,
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
                    code: code_by_name.get(&propagator.name).cloned().flatten(),
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
        let code_by_name = code_map(&tles);
        Ok(Self::propagate_to_ecef_states(
            &propagators,
            dates,
            &code_by_name,
        ))
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
                    code: s.code,
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
        let code_by_name = code_map(&tles);

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
                    code: code_by_name.get(&s.name).cloned().flatten(),
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
                code: s.code,
            });
        }
        Ok(results)
    }
}

/// Name -> satellite code lookup for threading `code` onto outputs.
fn code_map(tles: &[TleRecord]) -> HashMap<String, Option<String>> {
    tles.iter()
        .map(|t| (t.name.clone(), t.code.clone()))
        .collect()
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
            // 100 queries over the one-week window is ~100 distinct hourly
            // cache buckets: at or under the cache's MAX_ENTRIES, so a default
            // run never evicts and re-fetches its own entries. 1000 was ~168
            // buckets and thrashed.
            let count: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(100);
            run_benchmark(&client, count).await?;
        }
        _ => {
            let positions = client.ecef_positions(&now, &dates).await?;
            println!("{}", serde_json::to_string_pretty(&positions)?);
        }
    }

    Ok(())
}

/// The benchmark's summary JSON.
///
/// `elapsed_secs` is floored at 1 ns: a fully cache-hit run can complete
/// below timer resolution and measure as exactly 0.0 s, and dividing by that
/// turns the rates infinite — which serde_json renders as `null`
/// (`queries_per_sec`) and saturates to `u64::MAX` (`positions_per_sec`)
/// instead of numbers.
fn benchmark_stats(
    server: &str,
    queries: usize,
    total_positions: usize,
    elapsed_secs: f64,
    cache_entries: usize,
) -> serde_json::Value {
    let secs = elapsed_secs.max(1e-9);

    serde_json::json!({
        "server": server,
        "queries": queries,
        "total_positions": total_positions,
        "elapsed_s": (secs * 100.0).round() / 100.0,
        "positions_per_sec": (total_positions as f64 / secs).round() as u64,
        "queries_per_sec": ((queries as f64 / secs) * 10.0).round() / 10.0,
        "avg_query_ms": ((secs / queries as f64 * 1000.0) * 10.0).round() / 10.0,
        "cache_entries": cache_entries,
    })
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

    let result = benchmark_stats(
        &client.base_url,
        n,
        total_positions,
        elapsed.as_secs_f64(),
        cache::count(),
    );
    println!("{}", serde_json::to_string_pretty(&result)?);

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    const GPS_TLE_L1: &str = "1 24876U 97035A   24164.50000000  .00000080  00000+0  00000+0 0  9999";
    const GPS_TLE_L2: &str = "2 24876  55.4401 180.3028 0103987  60.0787 301.0966  2.00562231196828";

    fn test_date() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2024, 6, 12, 12, 0, 0).unwrap()
    }

    fn test_tle() -> TleRecord {
        TleRecord {
            name: "GPS BIIR-2  (PRN 13)".to_string(),
            line1: GPS_TLE_L1.to_string(),
            line2: GPS_TLE_L2.to_string(),
            jy: 0.0,
            code: None,
        }
    }

    fn test_propagators() -> Vec<Propagator> {
        let (propagators, skipped) = propagation::build_propagators(&[test_tle().as_core()]);
        assert_eq!(skipped, 0, "the reference TLE should parse");
        propagators
    }

    /// The client's DateTime -> unix-seconds handoff into the core must land
    /// on the astropy reference for the same instant.
    #[test]
    fn horizontal_matches_the_reference_vector() {
        let propagators = test_propagators();
        let dates = vec![test_date()];
        let codes = HashMap::new();

        let states = CatalogueClient::propagate_to_ecef_states(&propagators, &dates, &codes);
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

    /// A fully cache-hit run can measure as exactly 0.0 s; the rate fields
    /// must stay finite JSON numbers, not `null` / `u64::MAX`.
    #[test]
    fn benchmark_stats_survive_a_zero_elapsed_run() {
        let stats = benchmark_stats("https://example", 10, 1390, 0.0, 35);

        assert!(stats["queries_per_sec"].is_u64() || stats["queries_per_sec"].is_f64());
        assert!(stats["positions_per_sec"].is_u64());
        assert!(
            stats["positions_per_sec"].as_u64().unwrap() < u64::MAX / 2,
            "positions_per_sec saturated: {}",
            stats["positions_per_sec"]
        );
        assert_eq!(stats["elapsed_s"].as_f64().unwrap(), 0.0);
        assert_eq!(stats["avg_query_ms"].as_f64().unwrap(), 0.0);
    }

    #[test]
    fn benchmark_stats_computes_the_expected_rates() {
        let stats = benchmark_stats("https://example", 10, 1390, 5.0, 35);

        assert_eq!(stats["elapsed_s"].as_f64().unwrap(), 5.0);
        assert_eq!(stats["positions_per_sec"].as_u64().unwrap(), 278);
        assert_eq!(stats["queries_per_sec"].as_f64().unwrap(), 2.0);
        assert_eq!(stats["avg_query_ms"].as_f64().unwrap(), 500.0);
        assert_eq!(stats["cache_entries"].as_u64().unwrap(), 35);
    }

    #[test]
    fn test_tle_record_flux_default() {
        let json = r#"{"name":"TEST","line1":"","line2":""}"#;
        let rec: TleRecord = serde_json::from_str(json).unwrap();
        assert!((rec.jy - 0.0).abs() < 1.0, "jy={}", rec.jy);
    }

    #[test]
    fn test_tle_record_code() {
        let json = r#"{"name":"GSAT0213","line1":"","line2":"","code":"E04"}"#;
        let rec: TleRecord = serde_json::from_str(json).unwrap();
        assert_eq!(rec.code.as_deref(), Some("E04"));
    }

    #[test]
    fn test_tle_record_code_default() {
        let json = r#"{"name":"TEST","line1":"","line2":""}"#;
        let rec: TleRecord = serde_json::from_str(json).unwrap();
        assert!(rec.code.is_none());
    }

    // ------------------------------------------------------------------
    // Reference-vector regression tests (issue #9), carried over from the
    // pre-split client (25172fb).
    //
    // test-vectors/test_vectors.json holds astropy-computed TEME, ECEF and
    // horizontal values for a fixed TLE and observer. These assertions pin
    // the client against an independent implementation and would have
    // caught both date bugs on their own. The core runs its own copies in
    // tart-catalogue-core/tests; these exercise the client's paths.
    // ------------------------------------------------------------------

    #[derive(serde::Deserialize)]
    struct VectorTle {
        #[allow(dead_code)]
        name: String,
        line1: String,
        line2: String,
    }

    #[derive(serde::Deserialize)]
    struct VectorObserver {
        lat_deg: f64,
        lon_deg: f64,
        alt_m: f64,
    }

    #[derive(serde::Deserialize)]
    struct VectorDate {
        date: String,
        teme_km: [f64; 3],
        ecef_km: [f64; 3],
    }

    #[derive(serde::Deserialize)]
    struct VectorHorizontal {
        azimuth_deg: f64,
        elevation_deg: f64,
        range_km: f64,
    }

    #[derive(serde::Deserialize)]
    struct TestVectors {
        tle: VectorTle,
        observer: VectorObserver,
        dates: Vec<VectorDate>,
        horizontal: Vec<VectorHorizontal>,
    }

    fn load_test_vectors() -> TestVectors {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../test-vectors/test_vectors.json");
        let raw = std::fs::read_to_string(path).expect("read test-vectors/test_vectors.json");
        serde_json::from_str(&raw).expect("parse test vectors")
    }

    fn vector_dates(v: &TestVectors) -> Vec<DateTime<Utc>> {
        v.dates
            .iter()
            .map(|d| {
                DateTime::parse_from_rfc3339(&d.date)
                    .expect("vector date parses")
                    .with_timezone(&Utc)
            })
            .collect()
    }

    /// Propagators for the TLE the vectors were generated against.
    fn vector_propagators(v: &TestVectors) -> Vec<Propagator> {
        let record = TleRecord {
            name: v.tle.name.clone(),
            line1: v.tle.line1.clone(),
            line2: v.tle.line2.clone(),
            jy: 0.0,
            code: None,
        };
        let (propagators, skipped) = propagation::build_propagators(&[record.as_core()]);
        assert_eq!(skipped, 0, "the reference TLE should parse");
        propagators
    }

    /// Propagate the test-vector TLE through the production code path
    /// (core propagation + the client's ECEF assembly) at the vector dates.
    fn vector_ecef_states(v: &TestVectors) -> Vec<EcefState> {
        let codes = HashMap::new();
        CatalogueClient::propagate_to_ecef_states(&vector_propagators(v), &vector_dates(v), &codes)
    }

    #[test]
    fn test_teme_matches_astropy_vectors() {
        let v = load_test_vectors();
        let propagator = &vector_propagators(&v)[0];

        for d in &v.dates {
            let naive = DateTime::parse_from_rfc3339(&d.date)
                .expect("vector date parses")
                .naive_utc();
            let teme = propagation::propagate_teme(propagator, &naive).expect("propagation");
            for i in 0..3 {
                assert!(
                    (teme.position[i] - d.teme_km[i]).abs() < 0.5,
                    "teme[{}] at {}: got {}, want {}",
                    i,
                    d.date,
                    teme.position[i],
                    d.teme_km[i]
                );
            }
        }
    }

    #[test]
    fn test_ecef_matches_astropy_vectors() {
        let v = load_test_vectors();
        let states = vector_ecef_states(&v);
        assert_eq!(states.len(), v.dates.len());
        for (s, d) in states.iter().zip(v.dates.iter()) {
            for i in 0..3 {
                assert!(
                    (s.position[i] - d.ecef_km[i]).abs() < 1.0,
                    "ecef[{}] at {}: got {}, want {}",
                    i,
                    d.date,
                    s.position[i],
                    d.ecef_km[i]
                );
            }
        }
    }

    #[test]
    fn test_horizontal_matches_astropy_vectors() {
        let v = load_test_vectors();
        let states = vector_ecef_states(&v);
        let obs = geo::geodetic_to_ecef(v.observer.lat_deg, v.observer.lon_deg, v.observer.alt_m);
        assert_eq!(states.len(), v.horizontal.len());

        for (s, h) in states.iter().zip(v.horizontal.iter()) {
            let got = geo::horizontal_from_ecef(
                s.position,
                obs,
                v.observer.lat_deg,
                v.observer.lon_deg,
            );
            let mut d_az = (got.az_deg - h.azimuth_deg) % 360.0;
            if d_az > 180.0 {
                d_az -= 360.0;
            }
            if d_az < -180.0 {
                d_az += 360.0;
            }
            assert!(
                d_az.abs() < 0.05,
                "az at {}: got {}, want {}",
                s.date.to_rfc3339(),
                got.az_deg,
                h.azimuth_deg
            );
            assert!(
                (got.el_deg - h.elevation_deg).abs() < 0.05,
                "el at {}: got {}, want {}",
                s.date.to_rfc3339(),
                got.el_deg,
                h.elevation_deg
            );
            assert!(
                (got.range_km - h.range_km).abs() < 1.0,
                "range at {}: got {}, want {}",
                s.date.to_rfc3339(),
                got.range_km,
                h.range_km
            );
        }
    }
}
