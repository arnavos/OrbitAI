//! Sidecar process manager orchestrating the Python FastAPI local backend.
//! Provides secure token handover, health probing, active child supervision,
//! automatic crash recovery, and restart throttling.

use std::fs::OpenOptions;
use std::io::Write;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::Emitter;
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

/// Lifecycle state of the sidecar supervisor.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum SupervisorState {
    Initializing,
    Running,
    Restarting,
    FatalCrash,
    Stopped,
}

/// Diagnostic payload emitted when an unexpected crash occurs.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendCrashEvent {
    pub exit_code: Option<i32>,
    pub reason: String,
    pub restart_count: usize,
    pub state: SupervisorState,
}

/// Active backend descriptor containing running metadata and token file.
#[derive(Debug, Clone)]
pub struct ActiveBackend {
    pub pid: u32,
    pub port: u16,
    pub token: String,
    pub token_file: PathBuf,
}

impl ActiveBackend {
    /// Gracefully terminate the backend process group and clean up token file.
    pub async fn terminate(&self) {
        let pid = self.pid as i32;
        eprintln!(
            "[Orbit Desktop] Terminating backend process group (PID: {})...",
            pid
        );

        #[cfg(unix)]
        {
            unsafe {
                libc::kill(-pid, libc::SIGTERM);
            }

            for _ in 0..15 {
                tokio::time::sleep(Duration::from_millis(100)).await;
                if unsafe { libc::kill(pid, 0) } != 0 {
                    break;
                }
            }

            // Force kill if still running
            if unsafe { libc::kill(pid, 0) } == 0 {
                eprintln!("[Orbit Desktop] Backend still running after 1.5s, issuing SIGKILL...");
                unsafe {
                    libc::kill(-pid, libc::SIGKILL);
                }
            }
        }

        cleanup_token_file(&self.token_file);
    }
}

/// Asynchronous supervisor managing the sidecar lifecycle, health, and crash recovery.
pub struct SidecarSupervisor {
    pub state: Arc<Mutex<SupervisorState>>,
    pub connection_info: Arc<Mutex<Option<BackendConnectionInfo>>>,
    pub active_backend: Arc<Mutex<Option<ActiveBackend>>>,
    restart_history: Arc<Mutex<Vec<Instant>>>,
    is_shutting_down: Arc<AtomicBool>,
    app_handle: Arc<Mutex<Option<tauri::AppHandle>>>,
}

impl SidecarSupervisor {
    pub fn new(connection_info: Arc<Mutex<Option<BackendConnectionInfo>>>) -> Arc<Self> {
        Arc::new(Self {
            state: Arc::new(Mutex::new(SupervisorState::Initializing)),
            connection_info,
            active_backend: Arc::new(Mutex::new(None)),
            restart_history: Arc::new(Mutex::new(Vec::new())),
            is_shutting_down: Arc::new(AtomicBool::new(false)),
            app_handle: Arc::new(Mutex::new(None)),
        })
    }

    pub async fn set_app_handle(&self, handle: tauri::AppHandle) {
        *self.app_handle.lock().await = Some(handle);
    }

    /// Spawn initial backend process and launch background supervisor task.
    pub async fn start_and_supervise(self: Arc<Self>) -> Result<(), String> {
        let port = find_available_port()?;
        let token = generate_session_token();

        let (child, active) = spawn_and_verify_backend(port, &token).await?;

        let conn_info = BackendConnectionInfo {
            api_url: format!("http://127.0.0.1:{}", port),
            ws_url: format!("ws://127.0.0.1:{}/api/v1/ws", port),
            session_token: token,
        };

        *self.connection_info.lock().await = Some(conn_info);
        *self.active_backend.lock().await = Some(active);
        *self.state.lock().await = SupervisorState::Running;

        eprintln!(
            "[Orbit Desktop] Backend fully initialized and ready on port {}",
            port
        );

        // Spawn background supervisor task owning the child process
        let supervisor_clone = Arc::clone(&self);
        tokio::spawn(async move {
            supervisor_clone.run_supervision_loop(child).await;
        });

        Ok(())
    }

    /// Supervise the child process until exit, automatically recovering on unexpected crashes.
    async fn run_supervision_loop(self: Arc<Self>, mut current_child: Child) {
        loop {
            if self.is_shutting_down.load(Ordering::SeqCst) {
                break;
            }

            // Await child termination
            let status_result = current_child.wait().await;

            if self.is_shutting_down.load(Ordering::SeqCst) {
                break;
            }

            let exit_code = match &status_result {
                Ok(s) => s.code(),
                Err(_) => None,
            };
            let reason = match &status_result {
                Ok(s) => format!("Backend terminated unexpectedly: {}", s),
                Err(e) => format!("Backend wait error: {}", e),
            };

            eprintln!("[Orbit Desktop] CRASH DETECTED: {}", reason);

            // Clean up old active backend record and token file
            if let Some(old) = self.active_backend.lock().await.take() {
                cleanup_token_file(&old.token_file);
            }

            // Inner recovery loop: attempt to start and verify a replacement backend
            let mut recovered_child: Option<Child> = None;

            while !self.is_shutting_down.load(Ordering::SeqCst) {
                // Check restart throttling: max 3 attempts within 10 seconds
                let mut history = self.restart_history.lock().await;
                let now = Instant::now();
                history.retain(|t| now.duration_since(*t) < Duration::from_secs(10));

                if history.len() >= 3 {
                    *self.state.lock().await = SupervisorState::FatalCrash;
                    let crash_event = BackendCrashEvent {
                        exit_code,
                        reason: "Exceeded maximum 3 restart attempts within 10 seconds (Throttled)"
                            .to_string(),
                        restart_count: history.len(),
                        state: SupervisorState::FatalCrash,
                    };

                    eprintln!(
                        "[Orbit Desktop] FATAL CRASH: Subprocess entered unrecoverable state"
                    );

                    if let Some(handle) = self.app_handle.lock().await.as_ref() {
                        let _ = handle.emit("orbit://backend-crashed", &crash_event);
                    }
                    break;
                }

                // Throttled exponential backoff: 300ms, 600ms, 900ms
                history.push(now);
                let attempt = history.len();
                let backoff_ms = 300 * (attempt as u64);
                drop(history);

                *self.state.lock().await = SupervisorState::Restarting;
                eprintln!(
                    "[Orbit Desktop] Attempting recovery restart #{} in {}ms...",
                    attempt, backoff_ms
                );
                tokio::time::sleep(Duration::from_millis(backoff_ms)).await;

                if self.is_shutting_down.load(Ordering::SeqCst) {
                    break;
                }

                // Allocate fresh port and session token
                let new_port = match find_available_port() {
                    Ok(p) => p,
                    Err(e) => {
                        eprintln!(
                            "[Orbit Desktop] Failed to allocate port for recovery: {}",
                            e
                        );
                        continue;
                    }
                };

                let new_token = generate_session_token();

                match spawn_and_verify_backend(new_port, &new_token).await {
                    Ok((new_child, new_active)) => {
                        let new_conn = BackendConnectionInfo {
                            api_url: format!("http://127.0.0.1:{}", new_port),
                            ws_url: format!("ws://127.0.0.1:{}/api/v1/ws", new_port),
                            session_token: new_token,
                        };

                        *self.connection_info.lock().await = Some(new_conn.clone());
                        *self.active_backend.lock().await = Some(new_active);
                        *self.state.lock().await = SupervisorState::Running;

                        eprintln!(
                            "[Orbit Desktop] Backend successfully recovered on port {}",
                            new_port
                        );

                        if let Some(handle) = self.app_handle.lock().await.as_ref() {
                            let _ = handle.emit("orbit://backend-restarted", &new_conn);
                        }

                        recovered_child = Some(new_child);
                        break;
                    }
                    Err(e) => {
                        eprintln!(
                            "[Orbit Desktop] Recovery attempt #{} failed to start backend: {}",
                            attempt, e
                        );
                        if let Some(old) = self.active_backend.lock().await.take() {
                            cleanup_token_file(&old.token_file);
                        }
                    }
                }
            }

            match recovered_child {
                Some(child) => {
                    current_child = child;
                }
                None => {
                    // Fatal crash entered or shutting down
                    break;
                }
            }
        }
    }

    /// Gracefully terminate all active supervised processes and clean up files.
    pub async fn shutdown(&self) {
        self.is_shutting_down.store(true, Ordering::SeqCst);
        *self.state.lock().await = SupervisorState::Stopped;

        let active = self.active_backend.lock().await.take();
        if let Some(backend) = active {
            backend.terminate().await;
        }
    }
}

/// Managed application state storing backend connection info and process supervisor.
#[derive(Clone)]
pub struct AppState {
    pub connection_info: Arc<Mutex<Option<BackendConnectionInfo>>>,
    pub supervisor: Arc<SidecarSupervisor>,
}

impl AppState {
    pub fn new() -> Self {
        let connection_info = Arc::new(Mutex::new(None));
        let supervisor = SidecarSupervisor::new(Arc::clone(&connection_info));
        Self {
            connection_info,
            supervisor,
        }
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self::new()
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

/// Write session token to a temporary runtime file with strict 0600 POSIX permissions.
pub fn write_secure_session_token(token: &str) -> Result<PathBuf, String> {
    let home =
        std::env::var("HOME").map_err(|_| "HOME environment variable not set".to_string())?;
    let run_dir = PathBuf::from(home).join(".orbit").join("run");

    // Ensure application-private directory exists with 0700 permissions
    std::fs::create_dir_all(&run_dir)
        .map_err(|e| format!("Failed to create secure runtime directory: {}", e))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let dir_perms = std::fs::Permissions::from_mode(0o700);
        let _ = std::fs::set_permissions(&run_dir, dir_perms);
    }

    let token_file = run_dir.join(format!("session_{}.token", Uuid::new_v4()));

    let mut options = OpenOptions::new();
    options.write(true).create_new(true);

    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
        options.custom_flags(libc::O_NOFOLLOW);
    }

    let mut file = options.open(&token_file).map_err(|e| {
        format!(
            "Failed to create secure token file ({:?}): {}",
            token_file, e
        )
    })?;

    file.write_all(token.as_bytes())
        .map_err(|e| format!("Failed to write session token: {}", e))?;
    file.flush()
        .map_err(|e| format!("Failed to flush session token file: {}", e))?;

    Ok(token_file)
}

/// Safely remove a token file if it exists.
pub fn cleanup_token_file(path: &Path) {
    if path.exists() {
        let _ = std::fs::remove_file(path);
    }
}

/// Helper to generate candidate directory roots by traversing up from CWD and the current executable.
fn get_search_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();

    // 1. Current working directory and ancestors
    if let Ok(cwd) = std::env::current_dir() {
        let mut cur = Some(cwd.as_path());
        while let Some(dir) = cur {
            roots.push(dir.to_path_buf());
            cur = dir.parent();
        }
    }

    // 2. Directory containing current executable and ancestors
    if let Ok(exe_path) = std::env::current_exe() {
        let mut cur = exe_path.parent();
        while let Some(dir) = cur {
            roots.push(dir.to_path_buf());
            cur = dir.parent();
        }
    }

    roots
}

/// Locate the Python interpreter inside the local project virtual environment.
pub fn find_python_interpreter() -> Result<PathBuf, String> {
    if let Ok(override_path) = std::env::var("ORBIT_PYTHON_PATH") {
        let p = PathBuf::from(override_path);
        if p.exists() {
            return Ok(p);
        } else {
            return Err(format!(
                "Configured ORBIT_PYTHON_PATH does not exist: {:?}",
                p
            ));
        }
    }

    // Try finding backend directory first and inspect its .venv/bin/python3
    if let Ok(backend_dir) = find_backend_dir() {
        let venv_python = backend_dir.join(".venv").join("bin").join("python3");
        if venv_python.exists() {
            if let Some(parent) = venv_python.parent() {
                if let Ok(canon_dir) = parent.canonicalize() {
                    return Ok(canon_dir.join("python3"));
                }
            }
            return Ok(venv_python);
        }
    }

    // Search through candidate directory roots
    for root in get_search_roots() {
        let candidate = root
            .join("backend")
            .join(".venv")
            .join("bin")
            .join("python3");
        if candidate.exists() {
            if let Some(parent) = candidate.parent() {
                if let Ok(canon_dir) = parent.canonicalize() {
                    return Ok(canon_dir.join("python3"));
                }
            }
            return Ok(candidate);
        }
    }

    // Standard macOS Framework fallback if virtualenv is not yet initialized
    let fallback =
        PathBuf::from("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13");
    if fallback.exists() {
        return Ok(fallback);
    }

    Err(
        "Could not find a valid Python 3.13 interpreter in backend/.venv or system paths. Run 'make setup' to initialize the virtual environment or set ORBIT_PYTHON_PATH."
            .to_string(),
    )
}

/// Resolve the backend directory path containing the FastAPI app.
pub fn find_backend_dir() -> Result<PathBuf, String> {
    if let Ok(override_dir) = std::env::var("ORBIT_BACKEND_DIR") {
        let p = PathBuf::from(override_dir);
        if p.join("app").join("main.py").exists() {
            return Ok(p.canonicalize().unwrap_or(p));
        } else {
            return Err(format!(
                "Configured ORBIT_BACKEND_DIR does not contain app/main.py: {:?}",
                p
            ));
        }
    }

    for root in get_search_roots() {
        let candidate = root.join("backend");
        if candidate.join("app").join("main.py").exists() {
            return Ok(candidate.canonicalize().unwrap_or(candidate));
        }
        if root.join("app").join("main.py").exists() {
            return Ok(root.canonicalize().unwrap_or(root));
        }
    }

    Err(
        "Could not locate backend directory containing app/main.py. Ensure ORBIT is run from the project repository or set ORBIT_BACKEND_DIR."
            .to_string(),
    )
}

/// Asynchronously probe the backend health endpoint until it returns HTTP 200 OK.
pub async fn wait_for_health(
    port: u16,
    max_retries: u32,
    retry_delay_ms: u64,
) -> Result<(), String> {
    let addr = format!("127.0.0.1:{}", port);

    for _attempt in 1..=max_retries {
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

/// Spawn the Python FastAPI backend process using secure token file handover and verify health.
pub async fn spawn_and_verify_backend(
    port: u16,
    token: &str,
) -> Result<(Child, ActiveBackend), String> {
    let python_bin = find_python_interpreter()?;
    let backend_dir = find_backend_dir()?;

    // Create 0600 token file in 0700 runtime directory
    let token_file = write_secure_session_token(token)?;

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
        .env("ORBIT_SESSION_TOKEN_FILE", &token_file)
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

    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            cleanup_token_file(&token_file);
            return Err(format!("Failed to spawn Python process: {}", e));
        }
    };

    let pid = match child.id() {
        Some(p) => p,
        None => {
            cleanup_token_file(&token_file);
            return Err("Failed to retrieve process ID".to_string());
        }
    };

    eprintln!("[Orbit Desktop] Subprocess spawned with PID: {}", pid);

    let active = ActiveBackend {
        pid,
        port,
        token: token.to_string(),
        token_file: token_file.clone(),
    };

    // Wait for health probe to pass
    if let Err(e) = wait_for_health(port, 40, 150).await {
        eprintln!("[Orbit Desktop] Health check failed, terminating subprocess...");
        active.terminate().await;
        return Err(e);
    }

    Ok((child, active))
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
    fn test_secure_token_file_creation_and_permissions() {
        let token = "test-secret-token-uuid-123456";
        let token_path = write_secure_session_token(token).expect("should write token file");
        assert!(token_path.exists(), "Token file must exist");

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let meta = std::fs::metadata(&token_path).expect("metadata should read");
            let mode = meta.permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "Token file permissions must be strictly 0600");
        }

        let content = std::fs::read_to_string(&token_path).expect("should read token");
        assert_eq!(content, token);

        cleanup_token_file(&token_path);
        assert!(!token_path.exists(), "Cleanup must remove token file");
    }

    #[tokio::test]
    async fn test_find_python_interpreter() {
        let python = find_python_interpreter();
        assert!(python.is_ok(), "Should locate Python 3.13 interpreter");
    }

    #[tokio::test]
    async fn test_find_backend_dir() {
        let backend = find_backend_dir();
        assert!(
            backend.is_ok(),
            "Should locate backend directory containing app/main.py"
        );
    }

    #[tokio::test]
    async fn test_wait_for_health_failure_on_unused_port() {
        let unused_port = 59999;
        let res = wait_for_health(unused_port, 2, 50).await;
        assert!(res.is_err(), "Health check should fail on unused port");
    }

    #[tokio::test]
    async fn test_backend_lifecycle_start_and_terminate() {
        let port = find_available_port().expect("available port");
        let token = generate_session_token();

        let (_child, active) = spawn_and_verify_backend(port, &token)
            .await
            .expect("backend should spawn and become healthy");

        assert!(active.pid > 0);
        assert_eq!(active.port, port);

        // Terminate
        active.terminate().await;
    }

    #[tokio::test]
    async fn test_supervisor_crash_recovery_detection() {
        let conn_info = Arc::new(Mutex::new(None));
        let supervisor = SidecarSupervisor::new(Arc::clone(&conn_info));

        // Start supervised backend
        Arc::clone(&supervisor)
            .start_and_supervise()
            .await
            .expect("supervised backend should start");

        // Verify running
        let initial_pid = {
            let guard = supervisor.active_backend.lock().await;
            guard.as_ref().unwrap().pid
        };
        assert!(initial_pid > 0);

        // Simulate an actual crash using SIGKILL (kill -9) on the child
        eprintln!(
            "[Test] Simulating unexpected backend crash with SIGKILL on PID {}...",
            initial_pid
        );
        unsafe {
            libc::kill(initial_pid as i32, libc::SIGKILL);
        }

        // Await supervisor detection and recovery restart (up to 8 seconds)
        let mut recovered = false;
        for _ in 0..80 {
            tokio::time::sleep(Duration::from_millis(100)).await;
            let guard = supervisor.active_backend.lock().await;
            if let Some(ref proc) = *guard {
                if proc.pid != initial_pid {
                    eprintln!("[Test] Recovered successfully with new PID {}", proc.pid);
                    recovered = true;
                    break;
                }
            }
        }

        assert!(
            recovered,
            "Supervisor must detect crash and recover child process"
        );

        // Clean shutdown
        supervisor.shutdown().await;
    }

    #[tokio::test]
    async fn test_supervisor_fatal_crash_throttling() {
        let conn_info = Arc::new(Mutex::new(None));
        let supervisor = SidecarSupervisor::new(Arc::clone(&conn_info));

        // Start supervised backend
        Arc::clone(&supervisor)
            .start_and_supervise()
            .await
            .expect("supervised backend should start");

        // Simulate 4 rapid crashes within 10 seconds (max 3 restarts allowed, 4th triggers FatalCrash)
        for _ in 1..=4 {
            let mut pid = 0;
            for _ in 0..40 {
                let guard = supervisor.active_backend.lock().await;
                if let Some(ref proc) = *guard {
                    pid = proc.pid;
                    break;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }

            if pid == 0 {
                break;
            }

            unsafe {
                libc::kill(pid as i32, libc::SIGKILL);
            }
            tokio::time::sleep(Duration::from_millis(400)).await;
        }

        // Await supervisor entering FatalCrash state
        let mut fatal = false;
        for _ in 0..60 {
            tokio::time::sleep(Duration::from_millis(100)).await;
            let state = supervisor.state.lock().await;
            if *state == SupervisorState::FatalCrash {
                fatal = true;
                break;
            }
        }

        assert!(
            fatal,
            "Supervisor must enter FatalCrash state after 3 rapid crashes"
        );

        // Clean shutdown
        supervisor.shutdown().await;
    }

    #[tokio::test]
    async fn test_supervisor_recovery_handles_repeated_spawn_failures() {
        let conn_info = Arc::new(Mutex::new(None));
        let supervisor = SidecarSupervisor::new(Arc::clone(&conn_info));

        // Spawn a short-lived process as initial child that exits immediately
        let child = Command::new("/bin/sh")
            .arg("-c")
            .arg("exit 1")
            .spawn()
            .expect("should spawn dummy exiting child");

        // Set invalid python path so that recovery restart attempts fail to spawn
        std::env::set_var("ORBIT_PYTHON_PATH", "/nonexistent/invalid/python_bin");

        // Spawn supervision loop in background task
        let supervisor_clone = Arc::clone(&supervisor);
        tokio::spawn(async move {
            supervisor_clone.run_supervision_loop(child).await;
        });

        // Await supervisor entering FatalCrash state
        let mut fatal = false;
        for _ in 0..60 {
            tokio::time::sleep(Duration::from_millis(100)).await;
            let state = supervisor.state.lock().await;
            if *state == SupervisorState::FatalCrash {
                fatal = true;
                break;
            }
        }

        // Clean up environment variable
        std::env::remove_var("ORBIT_PYTHON_PATH");

        assert!(
            fatal,
            "Supervisor must transition to FatalCrash after repeated spawn failures during recovery"
        );

        supervisor.shutdown().await;
    }
}
