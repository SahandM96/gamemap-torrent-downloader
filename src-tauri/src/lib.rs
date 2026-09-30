mod app_state;
mod aria;
mod commands;
mod desk;
mod net;
mod sidecar;
mod sources;
mod store;

use app_state::AppState;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let handle = app.handle().clone();
            app.manage(AppState::new(&handle));
            // Auto-start aria2c in the background so the UI is not stuck on «خاموش».
            let boot = handle.clone();
            std::thread::spawn(move || {
                let state = boot.state::<AppState>();
                match desk::daemon_action(&state, "start") {
                    Ok(v) => eprintln!(
                        "[torrent-desk] aria2 ready: {}",
                        v.get("version").and_then(|x| x.as_str()).unwrap_or("?")
                    ),
                    Err(e) => eprintln!("[torrent-desk] aria2 auto-start: {e}"),
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::desk_api,
            commands::pick_folder,
            commands::os_paths,
            commands::open_url,
            commands::donate_info,
            commands::create_donation,
        ])
        .run(tauri::generate_context!())
        .expect("error running GameMap Torrent Desk");
}
