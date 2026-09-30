use crate::store::Store;
use parking_lot::Mutex;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

pub struct AppState {
    pub store: Store,
    pub run_dir: PathBuf,
    pub resource_dir: Option<PathBuf>,
    pub rpc_secret: Mutex<String>,
    pub aria_port: u16,
}

impl AppState {
    pub fn new(app: &AppHandle) -> Self {
        let app_data = app
            .path()
            .app_data_dir()
            .expect("app data dir");
        let run_dir = app_data.join("run");
        std::fs::create_dir_all(&run_dir).ok();
        let state_path = run_dir.join("state.json");
        let secret_path = run_dir.join("rpc.secret");
        let secret = if secret_path.is_file() {
            std::fs::read_to_string(&secret_path).unwrap_or_default().trim().to_string()
        } else {
            let s = uuid::Uuid::new_v4().to_string().replace('-', "")[..24].to_string();
            let _ = std::fs::write(&secret_path, &s);
            s
        };
        let resource_dir = app.path().resource_dir().ok();
        Self {
            store: Store::open(state_path),
            run_dir,
            resource_dir,
            rpc_secret: Mutex::new(secret),
            aria_port: 6811,
        }
    }

    pub fn default_download_dir(&self) -> PathBuf {
        let p = self.store.prefs().default_dir;
        if p.is_empty() {
            dirs::download_dir()
                .unwrap_or_else(|| dirs::home_dir().unwrap_or_default())
                .join("Torrents")
        } else {
            PathBuf::from(p)
        }
    }

    pub fn session_file(&self) -> PathBuf {
        self.run_dir.join("aria2.session")
    }

    pub fn log_file(&self) -> PathBuf {
        self.run_dir.join("aria2.log")
    }
}
