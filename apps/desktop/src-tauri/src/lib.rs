#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|_app| {
            // M0 Stage 0.2: Base Tauri setup verified
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running orbit desktop application");
}
