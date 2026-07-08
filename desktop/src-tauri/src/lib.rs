use std::process::{Command, Stdio, Child};
use std::sync::Mutex;
use tauri::Manager;

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
            Ok(Ok(res)) if res.status() == 200 => {
                println!("[Vibe-Trading] Backend ready at http://127.0.0.1:8899");
                return Ok(());
            }
            _ => {
                if attempt < 30 {
                    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                }
            }
        }
    }
    Err("Backend failed to start after 30 seconds".to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let app_handle = app.handle().clone();
            
            // Spawn backend process
            let backend_child = Command::new("vibe-trading")
                .args(&["serve", "--port", "8899", "--host", "127.0.0.1"])
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .spawn()
                .map_err(|e| format!("Failed to spawn backend: {}", e))?;
            
            *BACKEND_PROCESS.lock().unwrap() = Some(backend_child);
            
            // Wait for backend to be ready
            tauri::async_runtime::spawn(async move {
                match wait_backend_ready().await {
                    Ok(()) => {
                        // Backend is ready
                    }
                    Err(err) => {
                        tauri_plugin_dialog::MessageDialogBuilder::new("Backend Error", &err)
                            .kind(tauri_plugin_dialog::MessageDialogKind::Error)
                            .show(|_| {});
                        app_handle.exit(1);
                    }
                }
            });
            
            // Register window close handler for graceful shutdown
            if let Some(window) = app.get_window("main") {
                window.on_window_event(|event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        
                        // Kill backend process
                        if let Ok(mut proc_guard) = BACKEND_PROCESS.lock() {
                            if let Some(mut child) = proc_guard.take() {
                                let _ = child.kill();
                            }
                        }
                        
                        // Wait briefly for process cleanup, then exit (off main thread to avoid blocking event loop)
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
