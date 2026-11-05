//! kikitori desktop app entry point.
//!
//! Role: assembles the Tauri app — plugins (log, window state, updater, process,
//! opener, global shortcut), managed state (the engine bridge, overlay state and the
//! pending update), command handlers and the app lifecycle (start the engine on
//! launch, shut it down on exit — and before a Windows update exits the app, see
//! `updater.rs` — and quit when the main window closes).

mod commands;
mod connection;
mod engine;
mod line_reader;
mod overlay;
mod protocol;
mod updater;

use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_window_state::StateFlags;

use crate::connection::EventSink;
use crate::engine::{BridgeSink, BridgeStatus, EngineBridge, ResolveInput};
use crate::overlay::{CLICK_THROUGH_SHORTCUT, MAIN_LABEL, OverlayState};
use crate::protocol::EngineEvent;

const EVENT_SUBTITLE: &str = "engine://subtitle";
const EVENT_STATUS: &str = "engine://status";
const EVENT_DOWNLOAD: &str = "engine://download-progress";
const EVENT_LOG: &str = "engine://log";
const EVENT_BRIDGE: &str = "bridge://state";

/// Forwards engine events to every window as Tauri events.
struct TauriSink {
    app: AppHandle,
}

impl TauriSink {
    fn emit<S: Serialize + Clone>(&self, name: &str, payload: S) {
        if let Err(e) = self.app.emit(name, payload) {
            log::error!("cannot emit {name}: {e}");
        }
    }
}

impl EventSink for TauriSink {
    fn engine_event(&self, event: EngineEvent) {
        match event {
            EngineEvent::Subtitle(p) => self.emit(EVENT_SUBTITLE, p),
            EngineEvent::Status(p) => self.emit(EVENT_STATUS, p),
            EngineEvent::DownloadProgress(p) => self.emit(EVENT_DOWNLOAD, p),
            EngineEvent::Log(p) => self.emit(EVENT_LOG, p),
            // Filtered out by the connection already; nothing to forward.
            EngineEvent::Unknown { name } => log::debug!("unknown event {name} reached the sink"),
        }
    }
}

impl BridgeSink for TauriSink {
    fn bridge_state(&self, status: &BridgeStatus) {
        self.emit(EVENT_BRIDGE, status.clone());
    }
}

fn build_bridge(app: &AppHandle) -> EngineBridge {
    let env_cmd = std::env::var(engine::ENGINE_CMD_ENV).ok();
    let env_cwd = std::env::var(engine::ENGINE_CWD_ENV).ok();
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| log::error!("cannot resolve the resource directory: {e}"))
        .ok();
    let repo_root = engine::compile_time_repo_root();
    let command = engine::resolve_engine_command(&ResolveInput {
        env_cmd: env_cmd.as_deref(),
        env_cwd: env_cwd.as_deref(),
        debug_build: cfg!(debug_assertions),
        resource_dir: resource_dir.as_deref(),
        repo_root: &repo_root,
    });
    if let Err(e) = &command {
        log::error!("engine command: {e}");
    }
    EngineBridge::new(command, Arc::new(TauriSink { app: app.clone() }))
}

fn register_click_through_shortcut(app: &AppHandle) {
    let result =
        app.global_shortcut()
            .on_shortcut(CLICK_THROUGH_SHORTCUT, |app, _shortcut, event| {
                if event.state != ShortcutState::Pressed {
                    return;
                }
                let overlay = app.state::<OverlayState>();
                let enabled = !overlay.snapshot().click_through;
                if let Err(e) = commands::apply_click_through(app, &overlay, enabled) {
                    log::error!("shortcut could not toggle click-through: {e}");
                }
            });
    let overlay = app.state::<OverlayState>();
    match result {
        Ok(()) => overlay.set_shortcut_registered(true),
        Err(e) => {
            // Another app may own the combination; the in-app toggle still works.
            log::error!("cannot register global shortcut {CLICK_THROUGH_SHORTCUT}: {e}");
            overlay.set_shortcut_registered(false);
        }
    }
}

/// Builds and runs the app; blocks until it exits.
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .plugin(
            // Visibility is deliberately not restored: the overlay is shown when
            // a session starts or the user asks for it.
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::POSITION | StateFlags::SIZE | StateFlags::MAXIMIZED)
                .build(),
        )
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        // Only opens the NVIDIA CUDA EULA page: the URL scope lives in
        // capabilities/main.json, so the webview cannot open anything else.
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(OverlayState::default())
        .manage(updater::PendingUpdate::default())
        .setup(|app| {
            let handle = app.handle().clone();
            register_click_through_shortcut(&handle);
            let bridge = build_bridge(&handle);
            app.manage(bridge.clone());
            tauri::async_runtime::spawn(async move {
                if let Err(e) = bridge.start().await {
                    log::error!("engine did not start: {e}");
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // The overlay has no title bar; closing the main window is how the
            // user quits, so take the overlay (and the engine) down with it.
            if window.label() == MAIN_LABEL
                && let WindowEvent::CloseRequested { .. } = event
            {
                window.app_handle().exit(0);
            }
        })
        .invoke_handler(tauri::generate_handler![
            commands::engine_info,
            commands::hardware_detect,
            commands::models_list,
            commands::models_download,
            commands::models_validate,
            commands::devices_list,
            commands::settings_get,
            commands::settings_set,
            commands::settings_apply_recommendation,
            commands::settings_preview_recommendation,
            commands::session_start,
            commands::session_stop,
            commands::engine_shutdown,
            commands::engine_restart,
            commands::bridge_status,
            commands::app_about,
            commands::open_third_party_licenses,
            commands::overlay_get_state,
            commands::overlay_set_style,
            commands::overlay_set_click_through,
            commands::overlay_set_visible,
            updater::update_check,
            updater::update_install,
        ])
        .build(tauri::generate_context!());

    let app = match app {
        Ok(app) => app,
        Err(e) => {
            log::error!("failed to build the Tauri app: {e}");
            eprintln!("kikitori: failed to start: {e}");
            std::process::exit(1);
        }
    };

    app.run(|handle, event| {
        if let RunEvent::Exit = event
            && let Some(bridge) = handle.try_state::<EngineBridge>()
        {
            let bridge = bridge.inner().clone();
            // Bounded by the shutdown/kill timeouts inside `shutdown`.
            tauri::async_runtime::block_on(bridge.shutdown());
        }
    });
}
