//! In-app updates with the engine shut down before the app is replaced.
//!
//! Role: the main window checks for and installs updates through these commands
//! instead of tauri-plugin-updater's JavaScript API. On Windows the plugin starts
//! the NSIS installer and calls `std::process::exit(0)`, so `RunEvent::Exit` (and
//! the `engine.shutdown` in `lib.rs`) never runs; the engine would only notice the
//! closed stdin while the installer overwrites its files. The plugin's own commands
//! build their updater with a fixed `on_before_exit` hook, so this module builds the
//! updater itself (`UpdaterExt::updater_builder`) and installs a hook that first
//! shuts the engine down — `engine.shutdown`, then a kill after
//! [`UPDATE_SHUTDOWN_GRACE`] — and then does what the plugin's default hook does
//! (`AppHandle::cleanup_before_exit`). macOS and Linux do not exit after installing;
//! their restart goes through `request_restart` → `RunEvent::Exit`, which already
//! shuts the engine down.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::engine::{EngineBridge, ShutdownOutcome, UPDATE_SHUTDOWN_GRACE};
use crate::overlay::{MAIN_LABEL, OVERLAY_LABEL, OverlayState};

/// Bounds for the timeouts the webview passes (it is an input source too).
const MIN_TIMEOUT_MS: u64 = 1_000;
const MAX_TIMEOUT_MS: u64 = 30 * 60_000;

/// The update found by the last [`update_check`], waiting for [`update_install`].
#[derive(Default)]
pub struct PendingUpdate {
    update: Mutex<Option<Update>>,
    /// Set by the `on_before_exit` hook: if installing fails after it ran, the
    /// engine is down and has to be started again.
    hook_ran: AtomicBool,
    /// An install is downloading or running; a second click must not start another.
    installing: AtomicBool,
}

/// Clears [`PendingUpdate::installing`] however `update_install` returns.
struct InstallGuard<'a>(&'a AtomicBool);

impl Drop for InstallGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

/// What [`update_check`] reports to the webview.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub current_version: String,
    pub notes: Option<String>,
}

/// Download progress, the same shape as the plugin's JavaScript `DownloadEvent`.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "event", content = "data")]
pub enum DownloadEvent {
    #[serde(rename_all = "camelCase")]
    Started {
        content_length: Option<u64>,
    },
    #[serde(rename_all = "camelCase")]
    Progress {
        chunk_length: usize,
    },
    Finished,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    // Nothing panics while this lock is held; recover rather than cascade.
    m.lock().unwrap_or_else(|p| p.into_inner())
}

/// Validates a timeout passed by the webview.
pub fn validate_timeout_ms(timeout_ms: u64) -> Result<Duration, String> {
    if !(MIN_TIMEOUT_MS..=MAX_TIMEOUT_MS).contains(&timeout_ms) {
        return Err(format!(
            "timeout must be {MIN_TIMEOUT_MS}–{MAX_TIMEOUT_MS} ms, got {timeout_ms}"
        ));
    }
    Ok(Duration::from_millis(timeout_ms))
}

/// Shuts the engine down before the updater exits the app: `engine.shutdown`,
/// wait up to [`UPDATE_SHUTDOWN_GRACE`], then kill. Blocks the calling thread (the
/// hook is synchronous and `std::process::exit` follows it) for at most
/// [`crate::engine::blocking_shutdown_bound`]; never fails — the update goes on
/// either way, so problems are only logged.
pub fn shutdown_engine_before_exit(
    bridge: &EngineBridge,
    runtime: &tokio::runtime::Handle,
) -> Option<ShutdownOutcome> {
    let started = Instant::now();
    let outcome = bridge.shutdown_blocking(runtime, UPDATE_SHUTDOWN_GRACE);
    let elapsed = started.elapsed();
    match outcome {
        Some(ShutdownOutcome::Exited) => {
            log::info!("engine exited before the update in {elapsed:?}");
        }
        Some(ShutdownOutcome::NotRunning) => log::info!("no engine to stop before the update"),
        Some(ShutdownOutcome::Killed) => {
            log::warn!("engine did not exit before the update; killed after {elapsed:?}");
        }
        // shutdown_blocking already logged why.
        None => {}
    }
    outcome
}

/// The `on_before_exit` hook given to the updater (only called on Windows).
fn before_exit_hook(app: AppHandle) -> impl Fn() + Send + Sync + 'static {
    move || {
        app.state::<PendingUpdate>()
            .hook_ran
            .store(true, Ordering::SeqCst);
        match app.try_state::<EngineBridge>() {
            Some(bridge) => {
                let runtime = tauri::async_runtime::handle();
                shutdown_engine_before_exit(bridge.inner(), runtime.inner());
            }
            None => log::warn!("no engine bridge to stop before the update"),
        }
        // What the plugin's default hook does (UpdaterExt::updater_builder).
        app.cleanup_before_exit();
    }
}

/// Checks the update endpoint (tauri.conf.json); returns the newer version, if any.
#[tauri::command]
pub async fn update_check(
    app: AppHandle,
    pending: State<'_, PendingUpdate>,
    timeout_ms: u64,
) -> Result<Option<UpdateInfo>, String> {
    let timeout = validate_timeout_ms(timeout_ms)?;
    let updater = app
        .updater_builder()
        .timeout(timeout)
        .on_before_exit(before_exit_hook(app.clone()))
        .build()
        .map_err(|e| e.to_string())?;
    let found = updater.check().await.map_err(|e| e.to_string())?;
    let info = found.as_ref().map(|u| UpdateInfo {
        version: u.version.clone(),
        current_version: u.current_version.clone(),
        notes: u.body.clone(),
    });
    *lock(&pending.update) = found;
    Ok(info)
}

/// Downloads, verifies and installs the update found by [`update_check`],
/// reporting progress on `on_event`. On Windows the app exits from inside this
/// call (after the hook shut the engine down); elsewhere it returns and the
/// webview offers a restart.
#[tauri::command]
pub async fn update_install(
    app: AppHandle,
    pending: State<'_, PendingUpdate>,
    on_event: Channel<DownloadEvent>,
    timeout_ms: u64,
) -> Result<(), String> {
    let timeout = validate_timeout_ms(timeout_ms)?;
    if pending.installing.swap(true, Ordering::SeqCst) {
        return Err("an update is already being installed".to_string());
    }
    let _guard = InstallGuard(&pending.installing);
    let mut update = lock(&pending.update)
        .clone()
        .ok_or_else(|| "no update to install; check for updates first".to_string())?;
    update.timeout = Some(timeout);
    pending.hook_ran.store(false, Ordering::SeqCst);
    let send = |event: DownloadEvent| {
        if let Err(e) = on_event.send(event) {
            log::warn!("cannot report update progress: {e}");
        }
    };
    let mut first_chunk = true;
    let result = update
        .download_and_install(
            |chunk_length, content_length| {
                if first_chunk {
                    first_chunk = false;
                    send(DownloadEvent::Started { content_length });
                }
                send(DownloadEvent::Progress { chunk_length });
            },
            || send(DownloadEvent::Finished),
        )
        .await;
    if let Err(e) = result {
        log::error!("update install failed: {e}");
        if pending.hook_ran.load(Ordering::SeqCst) {
            recover_after_failed_install(&app).await;
        }
        return Err(e.to_string());
    }
    Ok(())
}

/// The hook ran (engine stopped, windows hidden by `cleanup_before_exit`) but the
/// installer did not start: bring the app back so the user can see the error and retry.
async fn recover_after_failed_install(app: &AppHandle) {
    let overlay_visible = app
        .try_state::<OverlayState>()
        .is_some_and(|o| o.snapshot().visible);
    let mut labels = vec![MAIN_LABEL];
    if overlay_visible {
        labels.push(OVERLAY_LABEL);
    }
    for label in labels {
        if let Some(window) = app.get_webview_window(label)
            && let Err(e) = window.show()
        {
            log::error!("cannot show the {label} window again: {e}");
        }
    }
    if let Some(bridge) = app.try_state::<EngineBridge>() {
        let bridge = bridge.inner().clone();
        if let Err(e) = bridge.restart().await {
            log::error!("engine did not restart after the failed update: {e}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timeouts_from_the_webview_are_bounded() {
        assert_eq!(validate_timeout_ms(15_000), Ok(Duration::from_secs(15)));
        assert_eq!(
            validate_timeout_ms(MIN_TIMEOUT_MS),
            Ok(Duration::from_secs(1))
        );
        assert!(validate_timeout_ms(MAX_TIMEOUT_MS).is_ok());
        assert!(validate_timeout_ms(0).is_err());
        assert!(validate_timeout_ms(999).is_err());
        assert!(validate_timeout_ms(MAX_TIMEOUT_MS + 1).is_err());
    }

    #[test]
    fn download_events_match_the_plugin_shape() {
        let started = serde_json::to_value(DownloadEvent::Started {
            content_length: Some(10),
        })
        .unwrap();
        assert_eq!(
            started,
            serde_json::json!({"event": "Started", "data": {"contentLength": 10}})
        );
        let progress = serde_json::to_value(DownloadEvent::Progress { chunk_length: 4 }).unwrap();
        assert_eq!(
            progress,
            serde_json::json!({"event": "Progress", "data": {"chunkLength": 4}})
        );
        assert_eq!(
            serde_json::to_value(DownloadEvent::Finished).unwrap(),
            serde_json::json!({"event": "Finished"})
        );
    }
}
