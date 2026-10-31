//! Tauri 2 desktop shell initialization and lifecycle orchestrator.

pub mod sidecar;

use sidecar::{AppState, BackendConnectionInfo};

/// Secure IPC command providing connection parameters exclusively to the internal webview.
/// If called while the sidecar is completing initial startup, awaits readiness up to 15 seconds.
#[tauri::command]
async fn get_backend_connection(
    state: tauri::State<'_, AppState>,
) -> Result<BackendConnectionInfo, String> {
    for _ in 0..150 {
        {
            let guard = state.connection_info.lock().await;
            if let Some(ref info) = *guard {
                return Ok(info.clone());
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    Err("Backend connection initialization timed out after 15 seconds".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app_state = AppState::new();
    let state_for_setup = app_state.clone();
    let state_for_exit = app_state.clone();

    let app = tauri::Builder::default()
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![get_backend_connection])
        .setup(move |_app_handle| {
            let state = state_for_setup;

            // 1. Allocate an available ephemeral loopback port
            let port = match sidecar::find_available_port() {
                Ok(p) => p,
                Err(e) => {
                    eprintln!("[Orbit Desktop] Error allocating loopback port: {}", e);
                    return Err(Box::new(std::io::Error::new(std::io::ErrorKind::Other, e)));
                }
            };

            // 2. Generate cryptographically random ephemeral session token
            let token = sidecar::generate_session_token();

            // 3. Asynchronously spawn backend and monitor health before providing connection info
            tauri::async_runtime::spawn(async move {
                match sidecar::start_backend(port, &token).await {
                    Ok(process) => {
                        let conn_info = BackendConnectionInfo {
                            api_url: format!("http://127.0.0.1:{}", port),
                            ws_url: format!("ws://127.0.0.1:{}/api/v1/ws", port),
                            session_token: token,
                        };

                        *state.connection_info.lock().await = Some(conn_info);
                        *state.process.lock().await = Some(process);
                        eprintln!("[Orbit Desktop] Backend fully initialized and ready on port {}", port);
                    }
                    Err(e) => {
                        eprintln!("[Orbit Desktop] FATAL: Backend failed to start: {}", e);
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building orbit desktop application");

    // Clean shutdown lifecycle event listener
    app.run(move |_app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            eprintln!("[Orbit Desktop] Exit requested, triggering graceful subprocess shutdown...");
            let state = state_for_exit.clone();
            tauri::async_runtime::block_on(async move {
                let mut guard = state.process.lock().await;
                if let Some(mut proc) = guard.take() {
                    proc.terminate().await;
                }
            });
        }
    });
}
