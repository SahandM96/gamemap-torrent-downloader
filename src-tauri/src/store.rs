//! JSON registry on disk (port of store.py).

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub const ST_QUEUED: &str = "queued";
pub const ST_PAUSED: &str = "paused";
pub const ST_DONE: &str = "done";
pub const ST_ERROR: &str = "error";
pub const ST_REMOVED: &str = "removed";
pub const ST_MISSING: &str = "missing";

const MAX_RECENT: usize = 15;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Prefs {
    pub default_dir: String,
    pub proxy: String,
    pub max_concurrent: u32,
    pub file_allocation: String,
    pub start_paused: bool,
    pub create_dir: bool,
    pub sources_disabled: Vec<String>,
}

impl Default for Prefs {
    fn default() -> Self {
        Self {
            default_dir: String::new(),
            proxy: String::new(),
            max_concurrent: 3,
            file_allocation: "none".into(),
            start_paused: false,
            create_dir: true,
            // Iran-blocked by default — user can re-enable via «مدیریت لیست»
            sources_disabled: vec![
                "1337x".into(),
                "nyaa".into(),
                "fitgirl".into(),
            ],
        }
    }
}

#[derive(Debug)]
pub struct Store {
    path: PathBuf,
    data: Mutex<Value>,
}

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Self {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let data = if path.is_file() {
            fs::read_to_string(&path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_else(empty)
        } else {
            empty()
        };
        Self {
            path,
            data: Mutex::new(data),
        }
    }

    fn save_locked(&self, data: &Value) {
        let tmp = self.path.with_extension("json.tmp");
        if let Ok(text) = serde_json::to_string_pretty(data) {
            let _ = fs::write(&tmp, text);
            let _ = fs::rename(&tmp, &self.path);
        }
    }

    pub fn prefs(&self) -> Prefs {
        let data = self.data.lock();
        let mut p = Prefs::default();
        if let Some(obj) = data.get("prefs").and_then(|v| v.as_object()) {
            if let Some(v) = obj.get("default_dir").and_then(|v| v.as_str()) {
                p.default_dir = v.into();
            }
            if let Some(v) = obj.get("proxy").and_then(|v| v.as_str()) {
                p.proxy = v.into();
            }
            if let Some(v) = obj.get("max_concurrent").and_then(|v| v.as_u64()) {
                p.max_concurrent = v as u32;
            }
            if let Some(v) = obj.get("file_allocation").and_then(|v| v.as_str()) {
                p.file_allocation = v.into();
            }
            if let Some(v) = obj.get("start_paused").and_then(|v| v.as_bool()) {
                p.start_paused = v;
            }
            if let Some(v) = obj.get("create_dir").and_then(|v| v.as_bool()) {
                p.create_dir = v;
            }
            if let Some(arr) = obj.get("sources_disabled").and_then(|v| v.as_array()) {
                p.sources_disabled = arr
                    .iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect();
            }
        }
        p
    }

    pub fn set_prefs(&self, patch: Value) -> Prefs {
        let mut data = self.data.lock();
        let prefs = data
            .as_object_mut()
            .unwrap()
            .entry("prefs")
            .or_insert_with(|| json!({}));
        if let (Some(dst), Some(src)) = (prefs.as_object_mut(), patch.as_object()) {
            for (k, v) in src {
                if matches!(
                    k.as_str(),
                    "default_dir"
                        | "proxy"
                        | "max_concurrent"
                        | "file_allocation"
                        | "start_paused"
                        | "create_dir"
                        | "sources_disabled"
                ) {
                    dst.insert(k.clone(), v.clone());
                }
            }
        }
        self.save_locked(&data);
        drop(data);
        self.prefs()
    }

    pub fn recent_dirs(&self) -> Vec<String> {
        self.data
            .lock()
            .get("recent_dirs")
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default()
    }

    pub fn note_dir(&self, directory: &str) {
        if directory.is_empty() {
            return;
        }
        let mut data = self.data.lock();
        let list = data
            .as_object_mut()
            .unwrap()
            .entry("recent_dirs")
            .or_insert_with(|| json!([]));
        let mut dirs: Vec<String> = list
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .filter(|d| d != directory)
                    .collect()
            })
            .unwrap_or_default();
        dirs.insert(0, directory.to_string());
        dirs.truncate(MAX_RECENT);
        *list = json!(dirs);
        self.save_locked(&data);
    }

    pub fn source_dir(&self, source: &str) -> String {
        self.data
            .lock()
            .get("source_dirs")
            .and_then(|v| v.get(source))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    }

    pub fn set_source_dir(&self, source: &str, directory: &str) {
        let mut data = self.data.lock();
        let map = data
            .as_object_mut()
            .unwrap()
            .entry("source_dirs")
            .or_insert_with(|| json!({}));
        if let Some(obj) = map.as_object_mut() {
            if directory.is_empty() {
                obj.remove(source);
            } else {
                obj.insert(source.to_string(), json!(directory));
            }
        }
        self.save_locked(&data);
    }

    pub fn downloads(&self) -> Vec<Value> {
        self.data
            .lock()
            .get("downloads")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default()
    }

    pub fn get(&self, id: &str) -> Option<Value> {
        self.downloads().into_iter().find(|r| r.get("id").and_then(|v| v.as_str()) == Some(id))
    }

    pub fn add(&self, fields: Value) -> Value {
        let mut row = json!({
            "id": &Uuid::new_v4().to_string()[..12],
            "gid": Value::Null,
            "name": "",
            "source": "",
            "source_label": "",
            "magnet": Value::Null,
            "torrent_url": Value::Null,
            "page_url": Value::Null,
            "info_hash": Value::Null,
            "torrent_sha1": Value::Null,
            "dir": "",
            "size_bytes": Value::Null,
            "status": ST_QUEUED,
            "error": Value::Null,
            "files": [],
            "added_at": chrono::Utc::now().timestamp(),
            "finished_at": Value::Null,
        });
        if let (Some(dst), Some(src)) = (row.as_object_mut(), fields.as_object()) {
            for (k, v) in src {
                dst.insert(k.clone(), v.clone());
            }
        }
        let mut data = self.data.lock();
        let list = data
            .as_object_mut()
            .unwrap()
            .entry("downloads")
            .or_insert_with(|| json!([]));
        if let Some(arr) = list.as_array_mut() {
            arr.insert(0, row.clone());
        }
        self.save_locked(&data);
        row
    }

    pub fn update(&self, id: &str, fields: Value) -> Option<Value> {
        let mut data = self.data.lock();
        let list = data.get_mut("downloads")?.as_array_mut()?;
        for row in list.iter_mut() {
            if row.get("id").and_then(|v| v.as_str()) == Some(id) {
                if let (Some(dst), Some(src)) = (row.as_object_mut(), fields.as_object()) {
                    for (k, v) in src {
                        dst.insert(k.clone(), v.clone());
                    }
                }
                let out = row.clone();
                self.save_locked(&data);
                return Some(out);
            }
        }
        None
    }

    pub fn remove(&self, id: &str) -> Option<Value> {
        let mut data = self.data.lock();
        let list = data.get_mut("downloads")?.as_array_mut()?;
        if let Some(pos) = list
            .iter()
            .position(|r| r.get("id").and_then(|v| v.as_str()) == Some(id))
        {
            let found = list.remove(pos);
            self.save_locked(&data);
            return Some(found);
        }
        None
    }
}

fn empty() -> Value {
    json!({
        "downloads": [],
        "recent_dirs": [],
        "source_dirs": {},
        "prefs": {}
    })
}
