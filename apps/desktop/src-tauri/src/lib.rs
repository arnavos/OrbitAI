//! Tauri 2 desktop shell initialization and lifecycle orchestrator.

pub mod sidecar;

use sidecar::{AppState, BackendConnectionInfo, SupervisorState};
use std::sync::Arc;

/// Secure IPC command providing connection parameters exclusively to the internal webview.
/// If called while the sidecar is completing initial startup or restarting, awaits readiness up to 15 seconds.
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

/// Secure IPC command returning current sidecar supervisor lifecycle state.
#[tauri::command]
async fn get_supervisor_state(
    state: tauri::State<'_, AppState>,
) -> Result<SupervisorState, String> {
    let guard = state.supervisor.state.lock().await;
    Ok(guard.clone())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app_state = AppState::new();
    let state_for_setup = app_state.clone();
    let state_for_exit = app_state.clone();

    let app = tauri::Builder::default()
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![
            get_backend_connection,
            get_supervisor_state
        ])
        .setup(move |app| {
            let supervisor = Arc::clone(&state_for_setup.supervisor);
            let handle = app.handle().clone();

            // Spawn backend under supervisor management
            tauri::async_runtime::spawn(async move {
                supervisor.set_app_handle(handle).await;
                if let Err(e) = supervisor.start_and_supervise().await {
                    eprintln!(
                        "[Orbit Desktop] FATAL: Backend supervisor failed to start: {}",
                        e
                    );
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building orbit desktop application");

    // Clean shutdown lifecycle event listener
    app.run(move |_app_handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            eprintln!("[Orbit Desktop] Exit requested, triggering graceful supervisor shutdown...");
            let state = state_for_exit.clone();
            tauri::async_runtime::block_on(async move {
                state.supervisor.shutdown().await;
            });
        }
    });
}
