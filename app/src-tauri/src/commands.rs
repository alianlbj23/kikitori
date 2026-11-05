//! Tauri commands exposed to the webviews.
//!
//! Role: the only entry points from JavaScript into Rust. Each engine protocol
//! method has one command that validates its arguments (the webview is also an
//! input source) and forwards them through the [`EngineBridge`] with a deadline
//! suited to that method. Overlay commands drive the subtitle window. Which
//! window may call which command is decided by `capabilities/*.json`.

use std::time::Duration;

use serde::Deserialize;
use serde_json::{Map, Value, json};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::connection::RequestError;
use crate::engine::{BridgeStatus, EngineBridge};
use crate::overlay::{OVERLAY_LABEL, OverlaySnapshot, OverlayState, OverlayStyle};

type CmdResult<T> = Result<T, RequestError>;

const QUICK: Duration = Duration::from_secs(10);
/// Hardware probing runs `nvidia-smi` and similar tools in the engine.
const HARDWARE: Duration = Duration::from_secs(30);
/// Includes a 3 s HTTP call to the translation server inside the engine.
const MODELS: Duration = Duration::from_secs(20);
/// The engine answers `session.start` after validating, before loading models.
const SESSION_START: Duration = Duration::from_secs(30);
/// The engine joins its worker threads with a 5 s timeout.
const SESSION_STOP: Duration = Duration::from_secs(15);

const MAX_MODEL_ID_CHARS: usize = 100;
const MAX_DEVICE_ID_CHARS: usize = 512;
/// The engine caps `settings.subtitle` at 16 KiB; the whole patch is far smaller.
const MAX_SETTINGS_PATCH_BYTES: usize = 64 * 1024;

pub const EVENT_OVERLAY_STATE: &str = "overlay://state";

fn invalid(message: impl Into<String>) -> RequestError {
    RequestError::InvalidInput {
        message: message.into(),
    }
}

/// ASR model ids are registry keys like `large-v3-turbo`.
pub fn validate_model_id(model: &str) -> Result<(), RequestError> {
    if model.is_empty() || model.chars().count() > MAX_MODEL_ID_CHARS {
        return Err(invalid(format!(
            "model id must be 1–{MAX_MODEL_ID_CHARS} characters"
        )));
    }
    if !model
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
    {
        return Err(invalid(
            "model id may only contain letters, digits, '.', '_' and '-'",
        ));
    }
    Ok(())
}

pub fn validate_device_id(device_id: Option<&str>) -> Result<(), RequestError> {
    if let Some(id) = device_id
        && (id.is_empty() || id.chars().count() > MAX_DEVICE_ID_CHARS)
    {
        return Err(invalid(format!(
            "device id must be 1–{MAX_DEVICE_ID_CHARS} characters"
        )));
    }
    Ok(())
}

/// `settings.set` takes a partial settings object; the engine validates fields.
pub fn validate_settings_patch(patch: &Value) -> Result<(), RequestError> {
    if !patch.is_object() {
        return Err(invalid("settings must be an object"));
    }
    let size = serde_json::to_vec(patch)
        .map_err(|e| invalid(e.to_string()))?
        .len();
    if size > MAX_SETTINGS_PATCH_BYTES {
        return Err(invalid(format!(
            "settings patch is {size} bytes, limit {MAX_SETTINGS_PATCH_BYTES}"
        )));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Media,
    Interpret,
}

impl Mode {
    fn as_str(self) -> &'static str {
        match self {
            Mode::Media => "media",
            Mode::Interpret => "interpret",
        }
    }
}

pub fn session_start_params(mode: Mode, device_id: Option<&str>) -> Result<Value, RequestError> {
    validate_device_id(device_id)?;
    // `device_id: null` explicitly means "system default device"; the UI always
    // sends its current choice so the engine never falls back to a stale one.
    Ok(json!({ "mode": mode.as_str(), "device_id": device_id }))
}

#[tauri::command]
pub async fn engine_info(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("engine.info", json!({}), QUICK).await
}

#[tauri::command]
pub async fn hardware_detect(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("hardware.detect", json!({}), HARDWARE).await
}

#[tauri::command]
pub async fn models_list(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("models.list", json!({}), MODELS).await
}

#[tauri::command]
pub async fn models_download(bridge: State<'_, EngineBridge>, model: String) -> CmdResult<Value> {
    validate_model_id(&model)?;
    bridge
        .request("models.download", json!({ "model": model }), QUICK)
        .await
}

#[tauri::command]
pub async fn devices_list(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("devices.list", json!({}), QUICK).await
}

#[tauri::command]
pub async fn settings_get(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("settings.get", json!({}), QUICK).await
}

#[tauri::command]
pub async fn settings_set(bridge: State<'_, EngineBridge>, patch: Value) -> CmdResult<Value> {
    validate_settings_patch(&patch)?;
    bridge.request("settings.set", patch, QUICK).await
}

#[tauri::command]
pub async fn session_start(
    bridge: State<'_, EngineBridge>,
    mode: Mode,
    device_id: Option<String>,
) -> CmdResult<Value> {
    let params = session_start_params(mode, device_id.as_deref())?;
    bridge.request("session.start", params, SESSION_START).await
}

#[tauri::command]
pub async fn session_stop(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("session.stop", json!({}), SESSION_STOP)
        .await
}

#[tauri::command]
pub async fn engine_shutdown(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.shutdown().await;
    Ok(Value::Object(Map::new()))
}

#[tauri::command]
pub async fn engine_restart(bridge: State<'_, EngineBridge>) -> CmdResult<BridgeStatus> {
    if let Err(e) = bridge.restart().await {
        log::error!("manual engine restart failed: {e}");
    }
    // The status carries the outcome (Ready or Failed with a reason).
    Ok(bridge.status())
}

#[tauri::command]
pub fn bridge_status(bridge: State<'_, EngineBridge>) -> BridgeStatus {
    bridge.status()
}

#[tauri::command]
pub fn overlay_get_state(overlay: State<'_, OverlayState>) -> OverlaySnapshot {
    overlay.snapshot()
}

fn broadcast_overlay(app: &AppHandle, overlay: &OverlayState) {
    if let Err(e) = app.emit(EVENT_OVERLAY_STATE, overlay.snapshot()) {
        log::error!("cannot emit overlay state: {e}");
    }
}

#[tauri::command]
pub fn overlay_set_style(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    style: OverlayStyle,
) -> CmdResult<OverlaySnapshot> {
    overlay.set_style(style).map_err(invalid)?;
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

/// Applies click-through to the overlay window; shared with the global shortcut.
pub fn apply_click_through(
    app: &AppHandle,
    overlay: &OverlayState,
    enabled: bool,
) -> CmdResult<()> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or_else(|| invalid("overlay window does not exist"))?;
    window.set_ignore_cursor_events(enabled).map_err(|e| {
        log::error!("set_ignore_cursor_events({enabled}) failed: {e}");
        invalid(format!("cannot change click-through: {e}"))
    })?;
    overlay.set_click_through(enabled);
    broadcast_overlay(app, overlay);
    Ok(())
}

#[tauri::command]
pub fn overlay_set_click_through(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    enabled: bool,
) -> CmdResult<OverlaySnapshot> {
    apply_click_through(&app, &overlay, enabled)?;
    Ok(overlay.snapshot())
}

#[tauri::command]
pub fn overlay_set_visible(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    visible: bool,
) -> CmdResult<OverlaySnapshot> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or_else(|| invalid("overlay window does not exist"))?;
    let result = if visible {
        window.show()
    } else {
        window.hide()
    };
    result.map_err(|e| {
        log::error!("overlay show/hide failed: {e}");
        invalid(format!("cannot show or hide the subtitle window: {e}"))
    })?;
    if visible {
        // Some window managers drop the topmost flag while a window is hidden.
        if let Err(e) = window.set_always_on_top(true) {
            log::warn!("cannot re-apply always-on-top: {e}");
        }
    }
    overlay.set_visible(visible);
    broadcast_overlay(&app, &overlay);
    Ok(overlay.snapshot())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_ids() {
        assert!(validate_model_id("large-v3-turbo").is_ok());
        assert!(validate_model_id("base").is_ok());
        assert!(validate_model_id("").is_err());
        assert!(validate_model_id("../etc/passwd").is_err());
        assert!(validate_model_id("a b").is_err());
        assert!(validate_model_id(&"a".repeat(101)).is_err());
    }

    #[test]
    fn device_ids() {
        assert!(validate_device_id(None).is_ok());
        assert!(validate_device_id(Some("{0.0.1.00000000}.{abc}")).is_ok());
        assert!(validate_device_id(Some("")).is_err());
        assert!(validate_device_id(Some(&"x".repeat(513))).is_err());
    }

    #[test]
    fn settings_patch() {
        assert!(validate_settings_patch(&json!({"partner_language": "en"})).is_ok());
        assert!(validate_settings_patch(&json!([1])).is_err());
        assert!(validate_settings_patch(&json!("x")).is_err());
        let big = json!({"subtitle": {"note": "x".repeat(MAX_SETTINGS_PATCH_BYTES)}});
        assert!(validate_settings_patch(&big).is_err());
    }

    #[test]
    fn session_params_send_explicit_null_device() {
        assert_eq!(
            session_start_params(Mode::Media, None).unwrap(),
            json!({"mode": "media", "device_id": null})
        );
        assert_eq!(
            session_start_params(Mode::Interpret, Some("mic-1")).unwrap(),
            json!({"mode": "interpret", "device_id": "mic-1"})
        );
        assert!(serde_json::from_value::<Mode>(json!("karaoke")).is_err());
    }
}
