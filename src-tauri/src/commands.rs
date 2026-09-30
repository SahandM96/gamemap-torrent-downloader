use crate::app_state::AppState;
use crate::desk;
use crate::sources;
use serde::Deserialize;
use serde_json::{json, Value};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::ShellExt;

const DONATION_API: &str = "https://api.gamemap.ir/api/v1/donations/create";
const MIN_DONATION_IRR: i64 = 100_000;

#[derive(Debug, Deserialize)]
pub struct ApiRequest {
    pub path: String,
    #[serde(default)]
    pub method: String,
    #[serde(default)]
    pub body: Value,
}

fn ok(data: Value) -> Value {
    json!({ "success": true, "data": data })
}

fn route(state: &AppState, req: ApiRequest) -> Result<Value, String> {
    let method = req.method.to_uppercase();
    let path = req.path.trim().trim_start_matches('/').to_string();

    let data = match (path.as_str(), method.as_str()) {
        ("api/health", "GET") => desk::health(state)?,
        ("api/sources", "GET") => sources::source_meta(state)?,
        ("api/downloads", "GET") => desk::downloads_view(state)?,
        ("api/settings", "GET") => desk::settings_view(state)?,
        ("api/search", "POST") => {
            let q = req.body.get("query").and_then(|v| v.as_str()).unwrap_or("");
            if q.len() < 2 {
                return Err("عبارت جست‌وجو خیلی کوتاه است".into());
            }
            let ids: Vec<String> = req
                .body
                .get("sources")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default();
            let limit = req.body.get("limit").and_then(|v| v.as_u64()).unwrap_or(20) as u32;
            sources::search_all(state, q, ids, limit)?
        }
        ("api/sources/probe", "POST") => sources::probe_all(state)?,
        ("api/download", "POST") => desk::start_download(state, req.body)?,
        ("api/partials/delete", "POST") => desk::delete_partial(
            req.body.get("control").and_then(|v| v.as_str()).unwrap_or(""),
            req.body
                .get("remove_target")
                .and_then(|v| v.as_bool())
                .unwrap_or(true),
        )?,
        ("api/settings", "POST") => desk::set_settings(state, req.body)?,
        ("api/proxy/test", "POST") => {
            let raw = req.body.get("proxy").and_then(|v| v.as_str()).unwrap_or("");
            desk::test_proxy(raw)?
        }
        ("api/proxy/detect", "GET") => desk::detect_proxies(),
        ("api/daemon", "POST") => {
            let action = req.body.get("action").and_then(|v| v.as_str()).unwrap_or("");
            desk::daemon_action(state, action)?
        }
        ("api/mkdir", "POST") => {
            let parent = req.body.get("parent").and_then(|v| v.as_str()).unwrap_or("");
            let name = req
                .body
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if name.is_empty() || name.contains('/') {
                return Err("نام پوشه نامعتبر است".into());
            }
            let parent = desk::resolve_dir(parent, false)?;
            let created = std::path::PathBuf::from(&parent).join(name);
            std::fs::create_dir_all(&created).map_err(|e| e.to_string())?;
            json!({"path": created.to_string_lossy()})
        }
        _ if path.starts_with("api/partials") && method == "GET" => {
            let root = req
                .path
                .split('?')
                .nth(1)
                .and_then(|q| {
                    q.split('&').find_map(|p| {
                        p.strip_prefix("root=").and_then(|v| {
                            urlencoding::decode(v).ok().map(|s| s.into_owned())
                        })
                    })
                })
                .unwrap_or_default();
            desk::partials_view(state, &root)?
        }
        _ if path.starts_with("api/downloads/") && method == "POST" => {
            let rest = path.strip_prefix("api/downloads/").unwrap_or("");
            let (id, action) = rest.split_once('/').unwrap_or((rest, ""));
            let id = urlencoding::decode(id)
                .map_err(|e| e.to_string())?
                .into_owned();
            match action {
                "pause" | "resume" | "stop" | "reveal" => {
                    desk::control_download(state, &id, action)?
                }
                "redir" => desk::move_destination(
                    state,
                    &id,
                    req.body.get("dir").and_then(|v| v.as_str()).unwrap_or(""),
                    req.body
                        .get("create_dir")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(true),
                )?,
                "delete" => desk::delete_download(
                    state,
                    &id,
                    req.body
                        .get("remove_files")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                )?,
                _ => return Err(format!("unknown action {action}")),
            }
        }
        _ => return Err(format!("unknown route {path}")),
    };

    Ok(ok(data))
}

/// Async so long search/probe work never starves the webview IPC pool.
#[tauri::command]
pub async fn desk_api(app: tauri::AppHandle, req: ApiRequest) -> Result<Value, String> {
    tokio::task::spawn_blocking(move || {
        let state = app.state::<AppState>();
        route(&state, req)
    })
    .await
    .map_err(|e| format!("task join: {e}"))?
}

/// OS-native folder picker (GTK portal / NSOpenPanel / IFileDialog).
/// Runs off the main thread — blocking dialogs on the main thread deadlock on Linux.
#[tauri::command]
pub async fn pick_folder(
    app: tauri::AppHandle,
    start: Option<String>,
    title: Option<String>,
) -> Result<Option<String>, String> {
    tokio::task::spawn_blocking(move || {
        let mut dlg = app.dialog().file();
        if let Some(t) = title.filter(|t| !t.trim().is_empty()) {
            dlg = dlg.set_title(t);
        }
        let start_dir = start
            .map(std::path::PathBuf::from)
            .filter(|p| p.is_dir())
            .or_else(dirs::download_dir)
            .or_else(dirs::home_dir);
        if let Some(dir) = start_dir {
            dlg = dlg.set_directory(dir);
        }
        dlg.blocking_pick_folder()
            .and_then(|p| p.into_path().ok())
            .map(|p| p.to_string_lossy().into_owned())
    })
    .await
    .map_err(|e| format!("dialog: {e}"))
}

/// Default downloads folder according to the OS (XDG / Known Folders / ~/Downloads).
#[tauri::command]
pub fn os_paths() -> Value {
    json!({
        "downloads": dirs::download_dir().map(|p| p.to_string_lossy().into_owned()),
        "home": dirs::home_dir().map(|p| p.to_string_lossy().into_owned()),
        "os": std::env::consts::OS,
        "sep": std::path::MAIN_SEPARATOR.to_string(),
    })
}

#[tauri::command]
pub fn open_url(app: tauri::AppHandle, url: String) -> Result<(), String> {
    if !url.starts_with("https://") {
        return Err("only https links are allowed".into());
    }
    app.shell()
        .open(url, None)
        .map_err(|e| e.to_string())?;
    Ok(())
}

const TRON_ADDRESS: &str = "TYFVCbpxoW9Ke2HteqWATQDHSybbG1YXJu";

#[tauri::command]
pub fn donate_info() -> Value {
    json!({
        "min_irr": MIN_DONATION_IRR,
        "presets_irr": [100_000, 200_000, 500_000, 1_000_000],
        "tron": TRON_ADDRESS,
        "tron_qr_svg": tron_qr_svg(TRON_ADDRESS),
        "api": DONATION_API,
    })
}

/// High error-correction QR so the centre logo overlay stays scannable.
fn tron_qr_svg(address: &str) -> String {
    use qrcode::render::svg;
    use qrcode::{EcLevel, QrCode};
    QrCode::with_error_correction_level(address.as_bytes(), EcLevel::H)
        .map(|code| {
            code.render::<svg::Color>()
                .min_dimensions(220, 220)
                .quiet_zone(true)
                .dark_color(svg::Color("#0a1220"))
                .light_color(svg::Color("#ffffff"))
                .build()
        })
        .unwrap_or_default()
}

/// Create ZarinPal checkout via GameMap backend (custom amount — direct zarinp.al link is closed).
#[tauri::command]
pub fn create_donation(amount_irr: i64, description: Option<String>) -> Result<Value, String> {
    if amount_irr < MIN_DONATION_IRR {
        return Err(format!(
            "حداقل مبلغ {MIN_DONATION_IRR} ریال است (معادل {} تومان)",
            MIN_DONATION_IRR / 10
        ));
    }
    if amount_irr > 500_000_000 {
        return Err("مبلغ بیش از حد مجاز است".into());
    }
    let desc = description
        .unwrap_or_else(|| "حمایت از GameMap Torrent Desk".into())
        .chars()
        .take(120)
        .collect::<String>();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(DONATION_API)
        .json(&json!({
            "user_id": "torrent-desk",
            "amount_irr": amount_irr,
            "description": desc,
        }))
        .send()
        .map_err(|e| format!("اتصال به API گیم‌مپ ناموفق: {e}"))?;
    let status = resp.status();
    let body: Value = resp
        .json()
        .map_err(|e| format!("پاسخ نامعتبر از API: {e}"))?;
    if !status.is_success() {
        let detail = body
            .get("detail")
            .and_then(|v| v.as_str())
            .or_else(|| body.get("message").and_then(|v| v.as_str()))
            .unwrap_or("ایجاد لینک پرداخت ناموفق بود");
        return Err(detail.to_string());
    }
    let url = body
        .get("payment_url")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "سرور لینک پرداخت برنگرداند".to_string())?;
    if !url.starts_with("https://") {
        return Err("لینک پرداخت نامعتبر است".into());
    }
    Ok(json!({
        "payment_url": url,
        "amount_irr": body.get("amount_irr").cloned().unwrap_or(json!(amount_irr)),
    }))
}
