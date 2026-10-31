//! Sidecar process manager orchestrating the Python FastAPI local backend.

use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use uuid::Uuid;

/// Secure connection parameters provided exclusively to the local webview via Tauri IPC.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendConnectionInfo {
    pub api_url: String,
    pub ws_url: String,
    pub session_token: String,
}

/// Represents the active child process and connection parameters.
pub struct SidecarProcess {
    child: Child,
    pub pid: u32,
    pub port: u16,
    pub token: String,
}

impl SidecarProcess {
    /// Gracefully terminate the Python backend process and its descendants.
    pub async fn terminate(&mut self) {
        let pid = self.pid as i32;
        eprintln!("[Orbit Desktop] Terminating backend process group (PID: {})...", pid);

        #[cfg(unix)]
        {
            // Send SIGTERM to the entire process group
            unsafe {
                libc::kill(-pid, libc::SIGTERM);
            }

            // Wait up to 2 seconds for graceful shutdown (flushing logs and closing SQLite WAL)
            tokio::select! {
                status = self.child.wait() => {
                    match status {
                        Ok(s) => eprintln!("[Orbit Desktop] Backend exited gracefully with status: {}", s),
                        Err(e) => eprintln!("[Orbit Desktop] Error waiting for backend exit: {}", e),
                    }
                }
                _ = tokio::time::sleep(Duration::from_secs(2)) => {
                    eprintln!("[Orbit Desktop] Backend did not terminate within 2s, issuing SIGKILL...");
                    unsafe {
                        libc::kill(-pid, libc::SIGKILL);
                    }
                    let _ = self.child.kill().await;
                }
            }
        }

        #[cfg(not(unix))]
        {
            let _ = self.child.kill().await;
        }
    }
}

/// Managed application state storing backend connection info and process handle.
#[derive(Clone)]
pub struct AppState {
    pub connection_info: Arc<Mutex<Option<BackendConnectionInfo>>>,
    pub process: Arc<Mutex<Option<SidecarProcess>>>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            connection_info: Arc::new(Mutex::new(None)),
            process: Arc::new(Mutex::new(None)),
        }
    }
}

/// Discover an available ephemeral port on the 127.0.0.1 loopback interface.
pub fn find_available_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("Failed to bind to loopback port: {}", e))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("Failed to read local socket address: {}", e))?
        .port();
    drop(listener);
    Ok(port)
}

/// Generate a cryptographically random session token (UUIDv4).
pub fn generate_session_token() -> String {
    Uuid::new_v4().to_string()
}

/// Locate the Python interpreter inside the local project virtual environment.
pub fn find_python_interpreter() -> Result<PathBuf, String> {
    // 1. Direct workspace path
    let candidates = [
        PathBuf::from("backend/.venv/bin/python3"),
        PathBuf::from("../../backend/.venv/bin/python3"),
        PathBuf::from("../backend/.venv/bin/python3"),
        PathBuf::from("/Users/arnav/Desktop/OrbitAI/backend/.venv/bin/python3"),
    ];

    for path in &candidates {
        if path.exists() {
            // Canonicalize only the parent directory, preserving python3 symlink so
            // Python's site.py correctly resolves sys.prefix from pyvenv.cfg.
            if let Some(parent) = path.parent() {
                if let Ok(canon_dir) = parent.canonicalize() {
                    return Ok(canon_dir.join("python3"));
                }
            }
            return Ok(path.clone());
        }
    }

    // 2. Fallback to system Python if venv is missing
    let fallback = PathBuf::from("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13");
    if fallback.exists() {
        return Ok(fallback);
    }

    Err("Could not find a valid Python 3.13 interpreter in backend/.venv".to_string())
}

/// Resolve the backend directory path containing the FastAPI app.
pub fn find_backend_dir() -> Result<PathBuf, String> {
    let candidates = [
        PathBuf::from("backend"),
        PathBuf::from("../../backend"),
        PathBuf::from("../backend"),
        PathBuf::from("/Users/arnav/Desktop/OrbitAI/backend"),
    ];

    for path in &candidates {
        if path.join("app").join("main.py").exists() {
            return Ok(path.canonicalize().unwrap_or_else(|_| path.clone()));
        }
    }

    Err("Could not locate backend directory containing app/main.py".to_string())
}

/// Asynchronously probe the backend health endpoint until it returns HTTP 200 OK.
pub async fn wait_for_health(port: u16, max_retries: u32, retry_delay_ms: u64) -> Result<(), String> {
    let addr = format!("127.0.0.1:{}", port);
    eprintln!("[Orbit Desktop] Probing backend health at {}...", addr);

    for attempt in 1..=max_retries {
        tokio::time::sleep(Duration::from_millis(retry_delay_ms)).await;

        if let Ok(mut stream) = TcpStream::connect(&addr).await {
            let request = format!(
                "GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
                port
            );

            if stream.write_all(request.as_bytes()).await.is_ok() {
                let mut response = Vec::new();
                if stream.read_to_end(&mut response).await.is_ok() {
                    let resp_str = String::from_utf8_lossy(&response);
                    if resp_str.contains("HTTP/1.1 200") || resp_str.contains("HTTP/1.0 200") {
                        eprintln!("[Orbit Desktop] Backend responded healthy on attempt {}", attempt);
                        return Ok(());
                    }
                }
            }
        }
    }

    Err(format!(
        "Backend health check timed out after {} attempts at {}",
        max_retries, addr
    ))
}

/// Spawn the Python FastAPI backend process and verify health.
pub async fn start_backend(port: u16, token: &str) -> Result<SidecarProcess, String> {
    let python_bin = find_python_interpreter()?;
    let backend_dir = find_backend_dir()?;

    eprintln!(
        "[Orbit Desktop] Launching Python backend (bin: {:?}, dir: {:?}, port: {})...",
        python_bin, backend_dir, port
    );

    let mut cmd = Command::new(&python_bin);
    cmd.current_dir(&backend_dir)
        .arg("-m")
        .arg("uvicorn")
        .arg("app.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .env("ORBIT_SESSION_TOKEN", token)
        .env("APP_ENV", "development")
        .env("ORBIT_PARENT_PID", std::process::id().to_string());

    if let Some(venv_dir) = python_bin.parent().and_then(|p| p.parent()) {
        cmd.env("VIRTUAL_ENV", venv_dir);
    }

    #[cfg(unix)]
    {
        // Place the child in its own process group for clean signal handling
        cmd.process_group(0);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn Python process: {}", e))?;

    let pid = child.id().ok_or_else(|| "Failed to retrieve process ID".to_string())?;
    eprintln!("[Orbit Desktop] Subprocess spawned with PID: {}", pid);

    let mut process = SidecarProcess {
        child,
        pid,
        port,
        token: token.to_string(),
    };

    // Wait for health probe to pass
    if let Err(e) = wait_for_health(port, 40, 150).await {
        eprintln!("[Orbit Desktop] Health check failed, terminating subprocess...");
        process.terminate().await;
        return Err(e);
    }

    Ok(process)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_port_allocation() {
        let port1 = find_available_port().expect("port 1 should allocate");
        let port2 = find_available_port().expect("port 2 should allocate");
        assert!(port1 > 1024, "Port should be in non-privileged range");
        assert!(port2 > 1024, "Port should be in non-privileged range");
    }

    #[test]
    fn test_token_generation() {
        let token1 = generate_session_token();
        let token2 = generate_session_token();
        assert_ne!(token1, token2, "Tokens must be unique");
        assert_eq!(token1.len(), 36, "UUIDv4 length should be 36 characters");
    }

    #[test]
    fn test_find_python_interpreter() {
        let res = find_python_interpreter();
        assert!(res.is_ok(), "Should locate valid Python 3.13 interpreter");
        let path = res.unwrap();
        assert!(path.exists(), "Interpreter path must exist");
    }

    #[test]
    fn test_find_backend_dir() {
        let res = find_backend_dir();
        assert!(res.is_ok(), "Should locate backend directory");
        let path = res.unwrap();
        assert!(path.join("app").join("main.py").exists());
    }

    #[tokio::test]
    async fn test_wait_for_health_failure_on_unused_port() {
        let unused_port = find_available_port().expect("should allocate port");
        let result = wait_for_health(unused_port, 2, 20).await;
        assert!(result.is_err(), "Probing unused port must fail");
    }

    #[tokio::test]
    async fn test_backend_lifecycle_start_and_terminate() {
        let port = find_available_port().expect("should find port");
        let token = generate_session_token();

        let mut process = start_backend(port, &token)
            .await
            .expect("Backend child process should launch and report healthy");

        let pid = process.pid;
        assert!(pid > 0);

        // Terminate cleanly
        process.terminate().await;

        // Verify the child process is dead
        #[cfg(unix)]
        {
            let res = unsafe { libc::kill(pid as i32, 0) };
            assert_eq!(res, -1, "Process should no longer exist after termination");
        }
    }

    #[tokio::test]
    async fn test_backend_restart_lifecycle() {
        let port1 = find_available_port().expect("should find port 1");
        let token1 = generate_session_token();
        let mut proc1 = start_backend(port1, &token1).await.expect("proc1 should start");
        let pid1 = proc1.pid;
        proc1.terminate().await;

        let port2 = find_available_port().expect("should find port 2");
        let token2 = generate_session_token();
        let mut proc2 = start_backend(port2, &token2)
            .await
            .expect("proc2 should start on new port/token");
        let pid2 = proc2.pid;
        assert_ne!(pid1, pid2, "Restarted process must receive new PID");
        assert_ne!(token1, token2, "Restarted process must receive new token");
        proc2.terminate().await;
    }
}
