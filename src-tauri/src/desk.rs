//! Download registry + aria2 orchestration (port of torrent_desk.py core, no HTTP server).

use crate::app_state::AppState;
use crate::aria::{describe_files, Aria2};
use crate::net::{fetch_bytes, format_size, magnet_infohash, magnet_name, normalize_magnet, parse_torrent_bytes};
use crate::sidecar;
use crate::store::{ST_DONE, ST_MISSING, ST_PAUSED, ST_QUEUED};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

fn now_ts() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn as_i64(v: Option<&Value>) -> i64 {
    v.and_then(|x| x.as_i64())
        .or_else(|| v.and_then(|x| x.as_str()).and_then(|s| s.parse().ok()))
        .unwrap_or(0)
}

fn is_metadata_job(status: &Value) -> bool {
    status
        .pointer("/files/0/path")
        .and_then(|v| v.as_str())
        .map_or(false, |p| p.rsplit('/').next().unwrap_or(p).starts_with("[METADATA]"))
}

pub fn prefs(state: &AppState) -> Value {
    let mut p = state.store.prefs();
    if p.default_dir.is_empty() {
        p.default_dir = state
            .default_download_dir()
            .to_string_lossy()
            .into_owned();
    }
    serde_json::to_value(p).unwrap_or(json!({}))
}

pub fn resolve_dir(raw: &str, create: bool) -> Result<String, String> {
    let candidate = shellexpand::tilde(raw.trim()).into_owned();
    if candidate.is_empty() {
        return Err("مقصد را وارد کنید".into());
    }
    let path = PathBuf::from(&candidate);
    if !path.is_absolute() {
        return Err("مسیر مقصد باید مطلق باشد؛ با «انتخاب پوشه» از دیالوگ سیستم استفاده کنید".into());
    }
    if create {
        fs::create_dir_all(&path).map_err(|e| e.to_string())?;
    } else if !path.is_dir() {
        return Err(format!("پوشه وجود ندارد: {}", path.display()));
    }
    Ok(candidate)
}

pub fn dest_for(
    state: &AppState,
    source: &str,
    requested: &str,
    remember: bool,
    create: bool,
) -> Result<String, String> {
    let default = prefs(state)
        .get("default_dir")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let wanted = if !requested.is_empty() {
        requested.to_string()
    } else if !state.store.source_dir(source).is_empty() {
        state.store.source_dir(source)
    } else {
        default
    };
    let directory = resolve_dir(&wanted, create)?;
    state.store.note_dir(&directory);
    if remember {
        state.store.set_source_dir(source, &directory);
    }
    Ok(directory)
}

fn build_aria(state: &AppState) -> Result<Aria2, String> {
    let prefs = state.store.prefs();
    let default_dir = if prefs.default_dir.is_empty() {
        state.default_download_dir().to_string_lossy().into_owned()
    } else {
        prefs.default_dir.clone()
    };
    let bin = sidecar::resolve(
        &state.run_dir.parent().unwrap_or(&state.run_dir),
        state.resource_dir.as_deref(),
    )?;
    Ok(Aria2 {
        binary: bin.path,
        host: "127.0.0.1".into(),
        port: state.aria_port,
        secret: state.rpc_secret.lock().clone(),
        session_file: state.session_file(),
        log_file: state.log_file(),
        default_dir,
        max_concurrent: prefs.max_concurrent,
        file_allocation: prefs.file_allocation,
        proxy: aria_proxy(&prefs.proxy),
    })
}

pub fn health(state: &AppState) -> Result<Value, String> {
    let handle = build_aria(state)?;
    let up = handle.is_up();
    let version = if up { handle.version().unwrap_or_default() } else { String::new() };
    Ok(json!({
        "aria2": {
            "up": up,
            "version": version,
            "endpoint": handle.endpoint(),
            "binary": handle.binary.to_string_lossy(),
        },
        "default_dir": prefs(state).get("default_dir"),
        "sources": 10,
        "sidecar_source": "bundled-or-path",
    }))
}

pub fn downloads_view(state: &AppState) -> Result<Value, String> {
    let handle = build_aria(state)?;
    let up = handle.is_up();
    let live = if up { handle.all_status() } else { HashMap::new() };
    let mut items = Vec::new();
    for row in state.store.downloads() {
        let id = row.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let mut gid = row.get("gid").and_then(|v| v.as_str()).unwrap_or("").to_string();
        // Magnets: aria2 finishes a small [METADATA] job, then downloads the content under the gid in followedBy.
        while let Some(next) = live
            .get(&gid)
            .and_then(|s| s.get("followedBy"))
            .and_then(|v| v.get(0))
            .and_then(|v| v.as_str())
            .filter(|n| live.contains_key(*n))
        {
            gid = next.to_string();
            state.store.update(id, json!({"gid": gid, "status": ST_QUEUED, "finished_at": null}));
        }
        let status_row = live.get(&gid);
        let is_meta = status_row.map_or(false, is_metadata_job);
        let total = if is_meta { 0 } else { status_row.map(|s| as_i64(s.get("totalLength"))).unwrap_or(0) };
        let done = if is_meta { 0 } else { status_row.map(|s| as_i64(s.get("completedLength"))).unwrap_or(0) };
        let speed = status_row.map(|s| as_i64(s.get("downloadSpeed"))).unwrap_or(0);
        let mut status = status_row
            .and_then(|s| s.get("status"))
            .and_then(|v| v.as_str())
            .map(|s| match s {
                "active" => "downloading",
                "waiting" => "queued",
                "paused" => "paused",
                "complete" => "done",
                "error" => "error",
                "removed" => "removed",
                _ => s,
            })
            .unwrap_or("")
            .to_string();
        if status.is_empty() {
            status = row
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or(ST_QUEUED)
                .to_string();
            if (status == ST_QUEUED || status == ST_PAUSED) && !gid.is_empty() {
                status = ST_MISSING.into();
            }
        }
        if is_meta && status != "error" && status != "removed" {
            status = "metadata".into();
        }
        if status == "done" && row.get("status").and_then(|v| v.as_str()) != Some(ST_DONE) {
            let files = status_row.map(describe_files).unwrap_or_else(|| {
                row.get("files")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default()
            });
            state.store.update(
                id,
                json!({"status": ST_DONE, "finished_at": now_ts(), "files": files}),
            );
        }
        let progress = if total > 0 {
            (done as f64 * 1000.0 / total as f64).round() / 10.0
        } else if status == "done" {
            100.0
        } else {
            0.0
        };
        let files = status_row.map(describe_files).unwrap_or_else(|| {
            row.get("files")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default()
        });
        items.push(json!({
            "id": id,
            "gid": gid,
            "name": row.get("name").and_then(|v| v.as_str()).unwrap_or("(بدون نام)"),
            "source": row.get("source").and_then(|v| v.as_str()).unwrap_or("manual"),
            "source_label": row.get("source_label").and_then(|v| v.as_str()).unwrap_or("دستی"),
            "dir": status_row.and_then(|s| s.get("dir")).or(row.get("dir")),
            "status": status,
            "progress": progress,
            "total_bytes": if total > 0 { total } else { as_i64(row.get("size_bytes")) },
            "done_bytes": done,
            "total_human": format_size(Some(if total > 0 { total } else { as_i64(row.get("size_bytes")) })),
            "done_human": format_size(Some(done)),
            "speed": speed,
            "speed_human": if speed > 0 { format!("{}/s", format_size(Some(speed))) } else { String::new() },
            "eta_seconds": if speed > 0 && total > done { Some((total - done) / speed) } else { None },
            "seeds": status_row.and_then(|s| s.get("numSeeders")),
            "connections": status_row.and_then(|s| s.get("connections")),
            "error": status_row.and_then(|s| s.get("errorMessage")).or(row.get("error")),
            "magnet": row.get("magnet"),
            "torrent_url": row.get("torrent_url"),
            "page_url": row.get("page_url"),
            "added_at": row.get("added_at"),
            "finished_at": row.get("finished_at"),
            "files": files,
            "resumable": row.get("magnet").is_some() || row.get("torrent_url").is_some(),
            "info_hash": row.get("info_hash"),
        }));
    }
    Ok(json!({
        "aria2": {
            "up": up,
            "version": if up { handle.version().unwrap_or_default() } else { String::new() },
            "endpoint": handle.endpoint(),
            "global": if up { handle.global_stat() } else { json!({}) },
        },
        "items": items,
    }))
}

pub fn start_download(state: &AppState, payload: Value) -> Result<Value, String> {
    let magnet = normalize_magnet(payload.get("magnet").and_then(|v| v.as_str()).unwrap_or(""));
    let torrent_url = payload
        .get("torrent_url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if magnet.is_empty() && torrent_url.is_empty() {
        return Err("مغناطیس یا فایل .torrent لازم است".into());
    }
    let source = payload
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or("manual");
    let directory = dest_for(
        state,
        source,
        payload.get("dir").and_then(|v| v.as_str()).unwrap_or(""),
        payload.get("remember").and_then(|v| v.as_bool()).unwrap_or(false),
        payload.get("create_dir").and_then(|v| v.as_bool()).unwrap_or(true),
    )?;
    let paused = payload.get("paused").and_then(|v| v.as_bool()).unwrap_or(false);
    let handle = build_aria(state)?;
    handle.ensure_daemon(8.0).map_err(|e| e.to_string())?;
    let proxy = state.store.prefs().proxy;
    let mut parsed_meta = json!({});
    let gid = if !torrent_url.is_empty() {
        let raw = fetch_bytes(&torrent_url, &proxy, 14).map_err(|e| e.0)?;
        if let Some(m) = parse_torrent_bytes(&raw) {
            parsed_meta = json!(m);
        }
        handle
            .add_torrent(&raw, &directory, paused)
            .map_err(|e| e.to_string())?
    } else {
        handle
            .add_magnet(&magnet, &directory, paused)
            .map_err(|e| e.to_string())?
    };
    let name = payload
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let parsed_name = parsed_meta
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let row = state.store.add(json!({
        "gid": gid,
        "name": if !name.is_empty() { name } else if !parsed_name.is_empty() { parsed_name.to_string() } else { magnet_name(&magnet) },
        "source": source,
        "source_label": payload.get("source_label").unwrap_or(&json!(source)),
        "magnet": if magnet.is_empty() { Value::Null } else { json!(magnet) },
        "torrent_url": if torrent_url.is_empty() { Value::Null } else { json!(torrent_url) },
        "page_url": payload.get("page_url"),
        "info_hash": parsed_meta.get("info_hash").cloned().or_else(|| {
            if magnet.is_empty() {
                None
            } else {
                Some(json!(magnet_infohash(&magnet)))
            }
        }),
        "torrent_sha1": parsed_meta.get("torrent_sha1"),
        "dir": directory,
        "size_bytes": payload.get("size_bytes"),
        "status": if paused { ST_PAUSED } else { ST_QUEUED },
    }));
    Ok(json!({"id": row.get("id"), "gid": gid, "dir": directory}))
}

pub fn control_download(state: &AppState, id: &str, action: &str) -> Result<Value, String> {
    let row = state.store.get(id).ok_or("چنین دانلودی در لیست نیست")?;
    let handle = build_aria(state)?;
    let gid = row.get("gid").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let directory = row
        .get("dir")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    match action {
        "pause" => {
            handle.ensure_daemon(8.0).map_err(|e| e.to_string())?;
            if !gid.is_empty() {
                handle.pause(&gid).map_err(|e| e.to_string())?;
            }
            state.store.update(id, json!({"status": ST_PAUSED}));
        }
        "resume" => {
            handle.ensure_daemon(8.0).map_err(|e| e.to_string())?;
            let live = handle.all_status();
            if gid.is_empty() || !live.contains_key(&gid) {
                let new_gid = readd(&handle, &row, &directory, false)?;
                state.store.update(id, json!({"gid": new_gid, "status": ST_QUEUED, "error": Value::Null}));
            } else {
                handle.unpause(&gid).map_err(|e| e.to_string())?;
                state.store.update(id, json!({"status": ST_QUEUED, "error": Value::Null}));
            }
        }
        "stop" => {
            handle.ensure_daemon(8.0).map_err(|e| e.to_string())?;
            if !gid.is_empty() {
                let _ = handle.remove(&gid);
            }
            state.store.update(id, json!({"gid": Value::Null, "status": ST_PAUSED}));
        }
        "reveal" => {
            open_folder(&directory)?;
        }
        _ => return Err(format!("دستور ناشناخته: {action}")),
    }
    Ok(json!({"id": id, "action": action}))
}

fn readd(handle: &Aria2, row: &Value, directory: &str, paused: bool) -> Result<String, String> {
    let magnet = row.get("magnet").and_then(|v| v.as_str()).unwrap_or("");
    let torrent_url = row.get("torrent_url").and_then(|v| v.as_str()).unwrap_or("");
    if !magnet.is_empty() {
        return handle
            .add_magnet(magnet, directory, paused)
            .map_err(|e| e.to_string());
    }
    if !torrent_url.is_empty() {
        let proxy = "";
        let raw = fetch_bytes(torrent_url, proxy, 14).map_err(|e| e.0)?;
        return handle
            .add_torrent(&raw, directory, paused)
            .map_err(|e| e.to_string());
    }
    Err("مغناطیس ذخیره نشده؛ ادامه‌دادن ممکن نیست".into())
}

pub fn delete_download(state: &AppState, id: &str, remove_files: bool) -> Result<Value, String> {
    let row = state.store.get(id).ok_or("چنین دانلودی در لیست نیست")?;
    let handle = build_aria(state)?;
    let gid = row.get("gid").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if remove_files {
        let directory = row.get("dir").and_then(|v| v.as_str()).unwrap_or("").trim_end_matches('/');
        for path in item_paths(&row, directory, None) {
            if path.is_file() {
                let _ = fs::remove_file(&path);
            }
        }
    }
    if !gid.is_empty() {
        let _ = handle.remove(&gid);
        handle.purge_result(&gid);
    }
    state.store.remove(id);
    Ok(json!({"id": id, "deleted": []}))
}

pub fn move_destination(state: &AppState, id: &str, new_dir: &str, create: bool) -> Result<Value, String> {
    let row = state.store.get(id).ok_or("چنین دانلودی در لیست نیست")?;
    let target = resolve_dir(new_dir, create)?;
    let old = row.get("dir").and_then(|v| v.as_str()).unwrap_or("").trim_end_matches('/');
    let mut moved = Vec::new();
    if !old.is_empty() && old != target.as_str() {
        for path in item_paths(&row, old, None) {
            if !path.is_file() || !path.starts_with(old) {
                continue;
            }
            let rel = path.strip_prefix(old).map_err(|e| e.to_string())?;
            let dest = PathBuf::from(&target).join(rel);
            if let Some(p) = dest.parent() {
                fs::create_dir_all(p).ok();
            }
            if dest.exists() {
                return Err(format!("در مقصد از قبل وجود دارد: {}", dest.display()));
            }
            fs::rename(&path, &dest).map_err(|e| e.to_string())?;
            moved.push(dest.to_string_lossy().into_owned());
        }
    }
    state.store.update(id, json!({"dir": target.clone(), "status": ST_PAUSED}));
    let handle = build_aria(state)?;
    if row.get("status").and_then(|v| v.as_str()) != Some(ST_DONE) {
        if row.get("magnet").is_some() || row.get("torrent_url").is_some() {
            let gid = readd(&handle, &row, &target, true)?;
            state.store.update(id, json!({"gid": gid}));
        }
    }
    state.store.note_dir(&target);
    Ok(json!({"id": id, "dir": target, "moved": moved}))
}

fn item_paths(row: &Value, directory: &str, _live: Option<&Value>) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(arr) = row.get("files").and_then(|v| v.as_array()) {
        for entry in arr {
            let path = entry
                .get("path")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if !path.is_empty() {
                out.push(PathBuf::from(path));
                out.push(PathBuf::from(format!("{path}.aria2")));
            }
        }
    }
    let top = Path::new(directory);
    if top.is_dir() {
        let infohash = magnet_infohash(row.get("magnet").and_then(|v| v.as_str()).unwrap_or(""))
            .to_lowercase();
        let torrent_sha1 = row
            .get("torrent_sha1")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_lowercase();
        if let Ok(read) = fs::read_dir(top) {
            for ent in read.flatten() {
                let p = ent.path();
                let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
                if name.ends_with(".torrent") {
                    let stem = name.trim_end_matches(".torrent").to_lowercase();
                    if stem == infohash || stem == torrent_sha1 {
                        out.push(p);
                    }
                }
            }
        }
    }
    out
}

pub fn partials_view(state: &AppState, root: &str) -> Result<Value, String> {
    let mut bases: Vec<String> = if !root.is_empty() {
        vec![root.to_string()]
    } else {
        let mut b = vec![state.default_download_dir().to_string_lossy().into_owned()];
        b.extend(state.store.recent_dirs());
        b
    };
    bases.sort();
    bases.dedup();
    let mut rows = Vec::new();
    for base in &bases {
        let base_path = PathBuf::from(base);
        if !base_path.is_dir() {
            continue;
        }
        walk_partials(&base_path, 3, &mut rows, state);
    }
    rows.sort_by(|a, b| {
        b.get("modified")
            .and_then(|v| v.as_i64())
            .unwrap_or(0)
            .cmp(&a.get("modified").and_then(|v| v.as_i64()).unwrap_or(0))
    });
    Ok(json!({"roots": bases, "items": rows}))
}

fn walk_partials(base: &Path, depth: i32, rows: &mut Vec<Value>, state: &AppState) {
    if depth < 0 || rows.len() >= 400 {
        return;
    }
    let Ok(read) = fs::read_dir(base) else { return };
    for ent in read.flatten() {
        let p = ent.path();
        if p.is_dir() {
            walk_partials(&p, depth - 1, rows, state);
        } else if p.extension().and_then(|e| e.to_str()) == Some("aria2") {
            let target = PathBuf::from(p.to_string_lossy().trim_end_matches(".aria2"));
            rows.push(json!({
                "control": p.to_string_lossy(),
                "target": target.to_string_lossy(),
                "name": target.file_name().and_then(|n| n.to_str()).unwrap_or(""),
                "dir": target.parent().map(|x| x.to_string_lossy().into_owned()),
                "partial_bytes": 0,
                "partial_human": "-",
                "exists": target.exists(),
                "download_id": Value::Null,
                "resumable": false,
                "modified": ent.metadata().ok().and_then(|m| m.modified().ok()).map(|t| t.duration_since(UNIX_EPOCH).ok().map(|d| d.as_secs() as i64)).flatten().unwrap_or(0),
            }));
        }
    }
}

pub fn delete_partial(control: &str, remove_target: bool) -> Result<Value, String> {
    let path = PathBuf::from(control);
    if !path.is_absolute() || !path.to_string_lossy().ends_with(".aria2") {
        return Err("فایل کنترل .aria2 معتبر نیست".into());
    }
    let target = PathBuf::from(path.to_string_lossy().trim_end_matches(".aria2"));
    fs::remove_file(&path).map_err(|e| e.to_string())?;
    let mut deleted = vec![path.to_string_lossy().into_owned()];
    if remove_target && target.is_file() {
        fs::remove_file(&target).ok();
        deleted.push(target.to_string_lossy().into_owned());
    }
    Ok(json!({"deleted": deleted}))
}

pub fn daemon_action(state: &AppState, action: &str) -> Result<Value, String> {
    let handle = build_aria(state)?;
    match action {
        "start" => Ok(json!({"version": handle.ensure_daemon(8.0).map_err(|e| e.to_string())?})),
        "stop" => {
            handle.save_session();
            handle.shutdown(true);
            Ok(json!({"stopped": true}))
        }
        "save" => {
            handle.save_session();
            Ok(json!({"saved": true}))
        }
        "purge" => {
            handle.purge_results();
            Ok(json!({"purged": true}))
        }
        "log" => {
            let log = state.log_file();
            let tail = if log.is_file() {
                fs::read_to_string(&log)
                    .unwrap_or_default()
                    .lines()
                    .rev()
                    .take(40)
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                    .collect::<Vec<_>>()
                    .join("\n")
            } else {
                String::new()
            };
            Ok(json!({"tail": tail}))
        }
        _ => Err(format!("دستور ناشناخته: {action}")),
    }
}

pub fn settings_view(state: &AppState) -> Result<Value, String> {
    Ok(json!({
        "prefs": prefs(state),
        "recent_dirs": state.store.recent_dirs(),
        "paths": {
            "state": state.run_dir.join("state.json").to_string_lossy(),
            "session": state.session_file().to_string_lossy(),
            "log": state.log_file().to_string_lossy(),
        },
        "env": {"timeout": 14, "detail_limit": 6, "eztv_pages": 4},
    }))
}

/// Empty, or `scheme://[user:pass@]host:port` with a scheme curl + reqwest both accept.
fn validate_proxy(raw: &str) -> Result<String, String> {
    let s = raw.trim();
    if s.is_empty() {
        return Ok(String::new());
    }
    let bad = || {
        "پروکسی نامعتبر است. مثال: socks5h://127.0.0.1:1080 یا http://127.0.0.1:8118".to_string()
    };
    let (scheme, rest) = s.split_once("://").ok_or_else(bad)?;
    if !matches!(
        scheme.to_ascii_lowercase().as_str(),
        "http" | "https" | "socks4" | "socks4a" | "socks5" | "socks5h"
    ) {
        return Err(bad());
    }
    let hostport = rest.trim_end_matches('/').rsplit('@').next().unwrap_or("");
    let (host, port) = hostport.rsplit_once(':').ok_or_else(bad)?;
    if host.is_empty() || host.chars().any(char::is_whitespace) || port.parse::<u16>().is_err() {
        return Err(bad());
    }
    Ok(s.to_string())
}

/// aria2's --all-proxy only speaks HTTP proxies; a SOCKS URI would break the daemon.
fn aria_proxy(proxy: &str) -> String {
    let lower = proxy.to_ascii_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        proxy.to_string()
    } else {
        String::new()
    }
}

pub fn set_settings(state: &AppState, patch: Value) -> Result<Value, String> {
    let mut p = patch;
    if let Some(raw) = p.get("proxy").and_then(|v| v.as_str()) {
        let clean = validate_proxy(raw)?;
        if let Some(obj) = p.as_object_mut() {
            obj.insert("proxy".into(), json!(clean));
        }
    }
    if let Some(dir) = p.get("default_dir").and_then(|v| v.as_str()) {
        if !dir.is_empty() {
            let resolved = resolve_dir(dir, true)?;
            if let Some(obj) = p.as_object_mut() {
                obj.insert("default_dir".into(), json!(resolved));
            }
        }
    }
    let out = state.store.set_prefs(p);
    Ok(json!({"prefs": out}))
}

/// Live check: can curl reach the internet through this proxy (or direct if empty)?
pub fn test_proxy(raw: &str) -> Result<Value, String> {
    let proxy = validate_proxy(raw)?;
    let mut cmd = Command::new("curl");
    cmd.args([
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        "8",
        "https://apibay.org/q.php?q=proxycheck",
    ]);
    if !proxy.is_empty() {
        cmd.args(["--proxy", &proxy]);
    }
    let out = cmd.output().map_err(|e| format!("curl در دسترس نیست: {e}"))?;
    let code = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if !out.status.success() || code != "200" {
        let detail = if !code.is_empty() && code != "000" {
            format!("HTTP {code}")
        } else {
            err.chars().take(120).collect::<String>()
        };
        let mut msg = format!("پروکسی پاسخ نداد ({detail}).");
        if proxy.contains(":9050") && proxy.starts_with("http") {
            msg.push_str(" پورت ۹۰۵۰ معمولاً SOCKS است — از socks5h://127.0.0.1:9050 استفاده کنید.");
        } else if !proxy.is_empty() {
            msg.push_str(" برای xray معمولاً socks5h://127.0.0.1:10808 است.");
        }
        return Err(msg);
    }
    Ok(json!({
        "ok": true,
        "proxy": proxy,
        "via": if proxy.is_empty() { "direct" } else { "proxy" },
    }))
}

/// Guess listening local proxy ports (xray/v2ray/tor common defaults).
pub fn detect_proxies() -> Value {
    let candidates = [
        ("socks5h://127.0.0.1:10808", 10808_u16),
        ("socks5h://127.0.0.1:1080", 1080),
        ("socks5h://127.0.0.1:9050", 9050),
        ("http://127.0.0.1:10809", 10809),
        ("http://127.0.0.1:7890", 7890),
        ("http://127.0.0.1:8118", 8118),
    ];
    let found: Vec<String> = candidates
        .iter()
        .filter(|(_, port)| std::net::TcpStream::connect_timeout(
            &std::net::SocketAddr::from(([127, 0, 0, 1], *port)),
            std::time::Duration::from_millis(80),
        ).is_ok())
        .map(|(url, _)| (*url).to_string())
        .collect();
    json!({ "proxies": found })
}

fn open_folder(path: &str) -> Result<(), String> {
    let target = PathBuf::from(shellexpand::tilde(path).into_owned());
    if !target.exists() {
        return Err(format!("مسیر وجود ندارد: {}", target.display()));
    }
    // Native file manager per OS
    let opener = if cfg!(target_os = "windows") {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    Command::new(opener)
        .arg(&target)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

// ponytail: shellexpand without extra dep — minimal tilde
mod shellexpand {
    pub fn tilde(s: &str) -> std::borrow::Cow<'_, str> {
        if s.starts_with("~/") {
            if let Some(h) = dirs::home_dir() {
                return format!("{}/{}", h.display(), &s[2..]).into();
            }
        }
        s.into()
    }
}

#[cfg(test)]
mod tests {
    use super::{aria_proxy, is_metadata_job, validate_proxy};
    use serde_json::json;

    #[test]
    fn metadata_job_detection() {
        assert!(is_metadata_job(&json!({"files": [{"path": "[METADATA]The Sims 4"}]})));
        assert!(is_metadata_job(&json!({"files": [{"path": "/dl/[METADATA]x"}]})));
        assert!(!is_metadata_job(&json!({"files": [{"path": "/dl/The Sims 4/setup.exe"}]})));
        assert!(!is_metadata_job(&json!({})));
    }

    #[test]
    fn proxy_validation() {
        assert_eq!(validate_proxy("  ").unwrap(), "");
        assert!(validate_proxy("socks5h://127.0.0.1:1080").is_ok());
        assert!(validate_proxy("http://user:pw@proxy.local:8118/").is_ok());
        assert!(validate_proxy("127.0.0.1:1080").is_err());
        assert!(validate_proxy("ftp://1.2.3.4:21").is_err());
        assert!(validate_proxy("socks5://host:99999").is_err());
        assert!(validate_proxy("http://:8080").is_err());
        assert_eq!(aria_proxy("socks5h://127.0.0.1:1080"), "");
        assert_eq!(aria_proxy("http://127.0.0.1:8118"), "http://127.0.0.1:8118");
    }
}
