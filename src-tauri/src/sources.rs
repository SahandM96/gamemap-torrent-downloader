//! Torrent search — v0.1 calls legacy Python adapters (`scripts/search_bridge.py`).
//! ponytail: scrapers rot weekly; full Rust port can replace the bridge without UI changes.

use crate::app_state::AppState;
use serde_json::{json, Value};
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

const DETAIL_LIMIT: u32 = 6;
const EZTV_PAGES: u32 = 4;
const TIMEOUT: f64 = 14.0;
/// Hard ceiling so a hung mirror cannot freeze the desktop UI forever.
const BRIDGE_WALL_SECS: u64 = 75;

pub fn source_meta(state: &AppState) -> Result<Value, String> {
    legacy_call(state, json!({"cmd": "meta"}))
}

pub fn search_all(
    state: &AppState,
    query: &str,
    source_ids: Vec<String>,
    limit: u32,
) -> Result<Value, String> {
    let prefs = state.store.prefs();
    legacy_call(
        state,
        json!({
            "cmd": "search",
            "query": query,
            "sources": source_ids,
            "limit": limit,
            "proxy": prefs.proxy,
            "timeout": TIMEOUT,
            "detail_limit": DETAIL_LIMIT,
            "eztv_pages": EZTV_PAGES,
        }),
    )
}

pub fn probe_all(state: &AppState) -> Result<Value, String> {
    let prefs = state.store.prefs();
    legacy_call(
        state,
        json!({
            "cmd": "probe",
            "proxy": prefs.proxy,
            "timeout": TIMEOUT,
            "detail_limit": DETAIL_LIMIT,
            "eztv_pages": EZTV_PAGES,
        }),
    )
}

fn legacy_call(state: &AppState, payload: Value) -> Result<Value, String> {
    let _ = state;
    let bridge = find_bridge()?;
    let mut child = Command::new("python3")
        .arg(&bridge)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("python3 bridge: {e}"))?;

    // Close stdin after write so Python sees EOF on json.load.
    {
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| "python3 stdin missing".to_string())?;
        let body = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
        stdin.write_all(body.as_bytes()).map_err(|e| e.to_string())?;
    }

    // Drain stdout/stderr on side threads — otherwise a ~100KB result fills the
    // OS pipe (~64KB), Python blocks on write, and we deadlock until the wall kill.
    let mut stdout_pipe = child
        .stdout
        .take()
        .ok_or_else(|| "python3 stdout missing".to_string())?;
    let mut stderr_pipe = child
        .stderr
        .take()
        .ok_or_else(|| "python3 stderr missing".to_string())?;
    let stdout_h = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = std::io::Read::read_to_end(&mut stdout_pipe, &mut buf);
        buf
    });
    let stderr_h = std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = std::io::Read::read_to_end(&mut stderr_pipe, &mut buf);
        buf
    });

    let deadline = std::time::Instant::now() + Duration::from_secs(BRIDGE_WALL_SECS);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if std::time::Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = stdout_h.join();
                let _ = stderr_h.join();
                return Err(format!(
                    "جست‌وجو بیش از {BRIDGE_WALL_SECS} ثانیه طول کشید و قطع شد. پروکسی را چک کنید یا تعداد سایت‌ها را کم کنید."
                ));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(80)),
            Err(e) => {
                let _ = stdout_h.join();
                let _ = stderr_h.join();
                return Err(format!("python3 wait: {e}"));
            }
        }
    };

    let stdout = stdout_h.join().unwrap_or_default();
    let stderr = stderr_h.join().unwrap_or_default();
    if !status.success() {
        let err = String::from_utf8_lossy(&stderr);
        return Err(if err.trim().is_empty() {
            "legacy search failed".into()
        } else {
            err.trim().chars().take(400).collect()
        });
    }
    let parsed: Value = serde_json::from_slice(&stdout).map_err(|e| {
        let head = String::from_utf8_lossy(&stdout);
        format!(
            "bridge JSON: {e} · {}",
            head.chars().take(160).collect::<String>()
        )
    })?;
    Ok(parsed)
}

fn find_bridge() -> Result<PathBuf, String> {
    let candidates = [
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../scripts/search_bridge.py"),
        PathBuf::from("scripts/search_bridge.py"),
    ];
    for c in candidates {
        if c.is_file() {
            return Ok(c.canonicalize().unwrap_or(c));
        }
    }
    Err("scripts/search_bridge.py not found — برای جست‌وجو به python3 و این اسکریپت نیاز است".into())
}
