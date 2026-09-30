//! aria2c JSON-RPC client + daemon lifecycle (port of aria.py).

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};
use thiserror::Error;

pub const STATUS_KEYS: &[&str] = &[
    "gid",
    "status",
    "totalLength",
    "completedLength",
    "uploadLength",
    "downloadSpeed",
    "uploadSpeed",
    "connections",
    "numSeeders",
    "seeder",
    "errorCode",
    "errorMessage",
    "dir",
    "files",
    "bittorrent",
    "infoHash",
    "followedBy",
    "verifiedLength",
];

#[derive(Debug, Error)]
pub enum AriaError {
    #[error("{message}")]
    Rpc { message: String, code: Option<i64> },
    #[error("{0}")]
    Other(String),
}

impl AriaError {
    pub fn is_unknown_gid(&self) -> bool {
        match self {
            AriaError::Rpc { message, code } => {
                *code == Some(1) || message.to_lowercase().contains("is not found")
            }
            _ => false,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Aria2 {
    pub binary: PathBuf,
    pub host: String,
    pub port: u16,
    pub secret: String,
    pub session_file: PathBuf,
    pub log_file: PathBuf,
    pub default_dir: String,
    pub max_concurrent: u32,
    pub file_allocation: String,
    pub proxy: String,
}

impl Aria2 {
    pub fn endpoint(&self) -> String {
        format!("http://{}:{}/jsonrpc", self.host, self.port)
    }

    pub fn rpc(&self, method: &str, params: Vec<Value>, timeout_secs: u64) -> Result<Value, AriaError> {
        let mut args = vec![json!(format!("token:{}", self.secret))];
        args.extend(params);
        let payload = json!({
            "jsonrpc": "2.0",
            "id": "desk",
            "method": method,
            "params": args,
        });
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .map_err(|e| AriaError::Other(e.to_string()))?;
        let resp = client
            .post(self.endpoint())
            .json(&payload)
            .send()
            .map_err(|e| AriaError::Rpc {
                message: format!("aria2 RPC unreachable ({e})"),
                code: None,
            })?;
        let status = resp.status();
        let body: Value = resp.json().map_err(|e| AriaError::Other(e.to_string()))?;
        if let Some(err) = body.get("error") {
            return Err(AriaError::Rpc {
                message: err
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("aria2 error")
                    .to_string(),
                code: err.get("code").and_then(|v| v.as_i64()),
            });
        }
        if !status.is_success() && body.get("result").is_none() {
            return Err(AriaError::Other(format!("aria2 HTTP {status}")));
        }
        Ok(body.get("result").cloned().unwrap_or(Value::Null))
    }

    pub fn version(&self) -> Result<String, AriaError> {
        let r = self.rpc("aria2.getVersion", vec![], 4)?;
        Ok(r.get("version")
            .and_then(|v| v.as_str())
            .unwrap_or("?")
            .to_string())
    }

    pub fn is_up(&self) -> bool {
        self.version().is_ok()
    }

    fn daemon_args(&self) -> Vec<String> {
        let _ = fs_mkdir(&self.session_file);
        let _ = fs_mkdir(&self.log_file);
        let mut args = vec![
            "--enable-rpc".into(),
            format!("--rpc-listen-port={}", self.port),
            "--rpc-listen-all=false".into(),
            format!("--rpc-secret={}", self.secret),
            "--continue=true".into(),
            "--auto-file-renaming=false".into(),
            "--allow-overwrite=false".into(),
            "--seed-time=0".into(),
            "--bt-enable-lpd=true".into(),
            "--enable-dht=true".into(),
            "--dht-listen-port=6881".into(),
            "--listen-port=6881".into(),
            format!("--max-concurrent-downloads={}", self.max_concurrent),
            format!("--file-allocation={}", self.file_allocation),
            format!("--dir={}", self.default_dir),
            format!("--save-session={}", self.session_file.display()),
            "--save-session-interval=30".into(),
            "--summary-interval=0".into(),
            "--console-log-level=warn".into(),
            format!("--log={}", self.log_file.display()),
            "--quiet=true".into(),
            "--daemon=true".into(),
        ];
        if !self.proxy.is_empty() {
            args.push(format!("--all-proxy={}", self.proxy));
        }
        if self.session_file.is_file()
            && fs_size(&self.session_file).unwrap_or(0) > 0
        {
            args.push(format!("--input-file={}", self.session_file.display()));
        }
        args
    }

    pub fn ensure_daemon(&self, wait_secs: f64) -> Result<String, AriaError> {
        if self.is_up() {
            let _ = self.sync_globals();
            return self.version();
        }
        let out = Command::new(&self.binary)
            .args(self.daemon_args())
            .output()
            .map_err(|e| AriaError::Other(e.to_string()))?;
        let deadline = Instant::now() + Duration::from_secs_f64(wait_secs);
        while Instant::now() < deadline {
            if self.is_up() {
                let _ = self.sync_globals();
                return self.version();
            }
            thread::sleep(Duration::from_millis(350));
        }
        let detail = String::from_utf8_lossy(&out.stderr);
        Err(AriaError::Other(format!(
            "aria2c RPC on {} not answering. {}",
            self.endpoint(),
            if detail.trim().is_empty() {
                self.log_file.display().to_string()
            } else {
                detail.trim().to_string()
            }
        )))
    }

    pub fn sync_globals(&self) -> Result<(), AriaError> {
        let options = json!({
            "max-concurrent-downloads": self.max_concurrent.to_string(),
            "file-allocation": self.file_allocation,
            "all-proxy": self.proxy,
        });
        let _ = self.rpc("aria2.changeGlobalOption", vec![options], 6);
        Ok(())
    }

    pub fn shutdown(&self, force: bool) {
        let method = if force {
            "aria2.forceShutdown"
        } else {
            "aria2.shutdown"
        };
        let _ = self.rpc(method, vec![], 6);
    }

    pub fn save_session(&self) {
        let _ = self.rpc("aria2.saveSession", vec![], 6);
    }

    pub fn global_stat(&self) -> Value {
        self.rpc("aria2.getGlobalStat", vec![], 6)
            .unwrap_or_else(|e| json!({"error": e.to_string()}))
    }

    pub fn add_magnet(&self, magnet: &str, directory: &str, paused: bool) -> Result<String, AriaError> {
        let options = json!({
            "dir": directory,
            "pause": if paused { "true" } else { "false" },
        });
        let r = self.rpc(
            "aria2.addUri",
            vec![json!([magnet]), options],
            20,
        )?;
        Ok(r.as_str().unwrap_or("").to_string())
    }

    pub fn add_torrent(
        &self,
        torrent_bytes: &[u8],
        directory: &str,
        paused: bool,
    ) -> Result<String, AriaError> {
        let options = json!({
            "dir": directory,
            "pause": if paused { "true" } else { "false" },
        });
        let encoded = B64.encode(torrent_bytes);
        let r = self.rpc(
            "aria2.addTorrent",
            vec![json!(encoded), json!([]), options],
            20,
        )?;
        Ok(r.as_str().unwrap_or("").to_string())
    }

    pub fn pause(&self, gid: &str) -> Result<(), AriaError> {
        self.rpc("aria2.pause", vec![json!(gid)], 10)?;
        Ok(())
    }

    pub fn unpause(&self, gid: &str) -> Result<(), AriaError> {
        self.rpc("aria2.unpause", vec![json!(gid)], 10)?;
        Ok(())
    }

    pub fn remove(&self, gid: &str) -> Result<(), AriaError> {
        match self.rpc("aria2.forceRemove", vec![json!(gid)], 10) {
            Ok(_) => Ok(()),
            Err(e) if e.is_unknown_gid() => Ok(()),
            Err(e) => Err(e),
        }
    }

    pub fn purge_result(&self, gid: &str) {
        let _ = self.rpc("aria2.removeDownloadResult", vec![json!(gid)], 6);
    }

    pub fn purge_results(&self) {
        let _ = self.rpc("aria2.purgeDownloadResult", vec![], 6);
    }

    pub fn status(&self, gid: &str) -> Result<Value, AriaError> {
        self.rpc(
            "aria2.tellStatus",
            vec![json!(gid), json!(STATUS_KEYS)],
            10,
        )
    }

    pub fn all_status(&self) -> HashMap<String, Value> {
        let mut rows = HashMap::new();
        let queries = [
            ("aria2.tellActive", vec![json!(STATUS_KEYS)]),
            ("aria2.tellWaiting", vec![json!(0), json!(500), json!(STATUS_KEYS)]),
            ("aria2.tellStopped", vec![json!(0), json!(300), json!(STATUS_KEYS)]),
        ];
        for (method, params) in queries {
            if let Ok(Value::Array(list)) = self.rpc(method, params, 10) {
                for row in list {
                    if let Some(gid) = row.get("gid").and_then(|v| v.as_str()) {
                        rows.insert(gid.to_string(), row);
                    }
                }
            }
        }
        rows
    }
}

pub fn describe_files(status: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    let Some(files) = status.get("files").and_then(|v| v.as_array()) else {
        return out;
    };
    for item in files.iter().take(500) {
        let path = item
            .get("path")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim();
        if path.is_empty() {
            continue;
        }
        out.push(json!({
            "path": path,
            "name": path.rsplit('/').next().unwrap_or(path),
            "length": item.get("length").and_then(|v| v.as_i64()).unwrap_or(0),
            "completed": item.get("completedLength").and_then(|v| v.as_i64()).unwrap_or(0),
            "selected": item.get("selected").and_then(|v| v.as_str()) != Some("false"),
        }));
    }
    out
}

fn fs_mkdir(path: &Path) -> std::io::Result<()> {
    if let Some(p) = path.parent() {
        fs::create_dir_all(p)?;
    }
    Ok(())
}

fn fs_size(path: &Path) -> std::io::Result<u64> {
    Ok(std::fs::metadata(path)?.len())
}

use std::fs;
