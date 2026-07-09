use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::ShellExt;

static BACKEND_PROCESS: Mutex<Option<tauri_plugin_shell::process::CommandChild>> = Mutex::new(None);

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
                println!("[Vibe-Trading] Backend responded with status {} (attempt {})", res.status(), attempt);
            }
            Ok(Err(e)) => {
                println!("[Vibe-Trading] Backend connection error (attempt {}): {}", attempt, e);
            }
            _ => {}
        }
        if attempt < 30 {
            tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        }
    }
    Err("Backend failed to start after 30 seconds.".to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_handle = app.handle().clone();

            // Spawn backend sidecar (bundled with the app)
            // NOTE: sidecar name must match the stem in bundle.externalBin.
            // Bundler copies binaries/vibe-backend-{triple}.exe → <exe_dir>/vibe-backend.exe
            let sidecar_command = app.shell().sidecar("vibe-backend")
                .map_err(|e| {
                    let msg = format!("Failed to create backend sidecar command: {}", e);
                    eprintln!("[Vibe-Trading] {}", msg);
                    msg
                })?;

            let (mut rx, child) = sidecar_command
                .args(["--port", "8899", "--host", "127.0.0.1"])
                .spawn()
                .map_err(|e| {
                    let msg = format!("Failed to start backend sidecar: {}", e);
                    eprintln!("[Vibe-Trading] {}", msg);
                    msg
                })?;

            *BACKEND_PROCESS.lock().unwrap() = Some(child);

            // Read stderr in background to capture errors
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        tauri_plugin_shell::process::CommandEvent::Stderr(line) => {
                            let text = String::from_utf8_lossy(&line);
                            eprintln!("[backend] {}", text.trim());
                        }
                        tauri_plugin_shell::process::CommandEvent::Stdout(line) => {
                            let text = String::from_utf8_lossy(&line);
                            println!("[backend] {}", text.trim());
                        }
                        tauri_plugin_shell::process::CommandEvent::Terminated(payload) => {
                            eprintln!("[Vibe-Trading] Backend exited with code {:?}", payload.code);
                            break;
                        }
                        _ => {}
                    }
                }
            });

            // Wait for backend to be ready
            tauri::async_runtime::spawn(async move {
                // Give backend a moment to fail fast
                tokio::time::sleep(std::time::Duration::from_millis(1500)).await;

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

                        // Kill backend sidecar
                        if let Ok(mut proc_guard) = BACKEND_PROCESS.lock() {
                            if let Some(child) = proc_guard.take() {
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
