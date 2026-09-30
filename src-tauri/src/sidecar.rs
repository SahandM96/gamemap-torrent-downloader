//! Resolve bundled / PATH aria2c (GPL sidecar — not linked).

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const PINNED_VERSION: &str = "1.37.0";

#[derive(Debug, Clone)]
pub struct AriaBinary {
    pub path: PathBuf,
    pub source: &'static str,
}

pub fn resolve(app_data: &Path, resource_dir: Option<&Path>) -> Result<AriaBinary, String> {
    let exe = if cfg!(windows) { "aria2c.exe" } else { "aria2c" };

    // 1) previously extracted into app data
    let data_bin = app_data.join("bin").join(exe);
    if is_usable(&data_bin) {
        return Ok(AriaBinary {
            path: data_bin,
            source: "app-data",
        });
    }

    // 2) bundled next to resources / sidecars
    let manifest_sidecar =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../sidecars").join(exe);
    if manifest_sidecar.is_file() {
        return copy_to_app_data(&manifest_sidecar, app_data, exe);
    }

    if let Some(res) = resource_dir {
        for candidate in [
            res.join(exe),
            res.join("sidecars").join(exe),
            res.join("aria2").join(exe),
        ] {
            if candidate.is_file() {
                return copy_to_app_data(&candidate, app_data, exe);
            }
        }
    }

    // 3) PATH
    if let Ok(path) = which(exe) {
        return Ok(AriaBinary {
            path,
            source: "path",
        });
    }

    Err(format!(
        "aria2c پیدا نشد. بستهٔ اپ باید باینری aria2 {PINNED_VERSION} را همراه داشته باشد، یا آن را روی سیستم نصب کنید (PATH). aria2 تحت GPL-2+ است و به‌صورت sidecar جدا اجرا می‌شود."
    ))
}

fn which(name: &str) -> Result<PathBuf, ()> {
    if let Ok(p) = env::var("TORRENT_DESK_ARIA_BIN") {
        let p = PathBuf::from(p);
        if is_usable(&p) {
            return Ok(p);
        }
    }
    let path_var = env::var_os("PATH").ok_or(())?;
    for dir in env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if is_usable(&candidate) {
            return Ok(candidate);
        }
    }
    Err(())
}

fn copy_to_app_data(src: &Path, app_data: &Path, exe: &str) -> Result<AriaBinary, String> {
    let dest_dir = app_data.join("bin");
    fs::create_dir_all(&dest_dir).map_err(|e| e.to_string())?;
    let dest = dest_dir.join(exe);
    fs::copy(src, &dest).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&dest).map_err(|e| e.to_string())?.permissions();
        perms.set_mode(0o755);
        let _ = fs::set_permissions(&dest, perms);
    }
    Ok(AriaBinary {
        path: dest,
        source: "bundled",
    })
}

fn is_usable(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    Command::new(path)
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}
