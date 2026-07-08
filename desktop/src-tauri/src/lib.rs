use std::process::{Command, Stdio, Child};
use std::sync::Mutex;
use std::io::{BufRead, BufReader};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;

static BACKEND_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

#[tauri::command]
fn get_backend_url() -> String {
    "http://127.0.0.1:8899".to_string()
}

async fn wait_backend_ready() -> Result<(), String> {
    let client = reqwest::Client::new();
    let url = "http://127.0.0.1:8899/health";

    for attempt in 1..=30 {
        match tokio::time::timeout(
            std::time::Duration::from_secs(2),
            client.get(url).send()
        ).await {
            Ok(Ok(res)) if res.status().is_success() => {
                println!("[Vibe-Trading] Backend ready at http://127.0.0.1:8899");
                return Ok(());
            }
            Ok(Ok(res)) => {
                // Server responded but with non-success status - might still be starting
                println!("[Vibe-Trading] Backend responded with status {} (attempt {})", res.status(), attempt);
            }
            Ok(Err(e)) => {
                println!("[Vibe-Trading] Backend connection error (attempt {}): {}", attempt, e);
            }
            _ => {
                // timeout - server not ready yet
            }
        }
        if attempt < 30 {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        }
    }
    Err("Backend failed to start after 30 seconds.\n\nMake sure 'vibe-trading' is installed:\n  pip install vibe-trading-ai".to_string())
}

fn try_spawn_backend() -> Result<Child, String> {
    // Try vibe-trading CLI first
    match Command::new("vibe-trading")
        .args(&["serve", "--port", "8899", "--host", "127.0.0.1"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => return Ok(child),
        Err(_) => {
            // vibe-trading not found - try python -m vibe_trading
            for python in &["python", "python3", "py"] {
                if let Ok(child) = Command::new(python)
                    .args(&["-m", "vibe_trading", "serve", "--port", "8899", "--host", "127.0.0.1"])
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                {
                    return Ok(child);
                }
            }
        }
    }
    Err("Cannot start backend.\n\nMake sure 'vibe-trading-ai' is installed:\n  pip install vibe-trading-ai".to_string())
}

fn spawn_stderr_reader(stderr: std::process::ChildStderr) {
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines() {
            if let Ok(line) = line {
                eprintln!("[backend] {}", line);
            }
        }
    });
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_handle = app.handle().clone();

            // Spawn backend process
            let mut backend_child = try_spawn_backend()
                .map_err(|e| {
                    eprintln!("[Vibe-Trading] {}", e);
                    e
                })?;

            // Read stderr in background to capture errors
            if let Some(stderr) = backend_child.stderr.take() {
                spawn_stderr_reader(stderr);
            }
            // Discard stdout
            drop(backend_child.stdout.take());

            *BACKEND_PROCESS.lock().unwrap() = Some(backend_child);

            // Wait for backend to be ready
            tauri::async_runtime::spawn(async move {
                // Give backend a moment to fail fast
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;

                // Check if backend process already died
                {
                    let mut guard = BACKEND_PROCESS.lock().unwrap();
                    if let Some(ref mut child) = *guard {
                        match child.try_wait() {
                            Ok(Some(status)) => {
                                let msg = format!(
                                    "Backend process exited immediately with code: {:?}\n\n\
                                     Try running manually:\n  vibe-trading serve --port 8899",
                                    status.code()
                                );
                                if let Some(window) = app_handle.get_webview_window("main") {
                                    tauri_plugin_dialog::MessageDialogBuilder::new(
                                        window.dialog().clone(),
                                        "Backend Crashed",
                                        &msg,
                                    )
                                    .kind(tauri_plugin_dialog::MessageDialogKind::Error)
                                    .show(|_| {});
                                }
                                app_handle.exit(1);
                                return;
                            }
                            Ok(None) => {} // still running
                            Err(_) => {}   // can't check
                        }
                    }
                }

                match wait_backend_ready().await {
                    Ok(()) => {}
                    Err(err) => {
                        if let Some(window) = app_handle.get_webview_window("main") {
                            tauri_plugin_dialog::MessageDialogBuilder::new(
                                window.dialog().clone(),
                                "Backend Error",
                                &err,
                            )
                            .kind(tauri_plugin_dialog::MessageDialogKind::Error)
                            .show(|_| {});
                        }
                        app_handle.exit(1);
                    }
                }
            });

            // Register window close handler for graceful shutdown
            if let Some(window) = app.get_webview_window("main") {
                window.on_window_event(|event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();

                        // Kill backend process
                        if let Ok(mut proc_guard) = BACKEND_PROCESS.lock() {
                            if let Some(mut child) = proc_guard.take() {
                                let _ = child.kill();
                            }
                        }

                        std::thread::spawn(move || {
                            std::thread::sleep(std::time::Duration::from_millis(500));
                            std::process::exit(0);
                        });
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_backend_url])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
