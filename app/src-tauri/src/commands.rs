//! Tauri commands exposed to the webviews.
//!
//! Role: the only entry points from JavaScript into Rust. Each engine protocol
//! method has one command that validates its arguments (the webview is also an
//! input source) and forwards them through the [`EngineBridge`] with a deadline
//! suited to that method. Overlay commands drive the subtitle window. Which
//! window may call which command is decided by `capabilities/*.json`.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;

use crate::connection::RequestError;
use crate::engine::{BridgeStatus, EngineBridge, third_party_licenses_dir};
use crate::overlay::{OVERLAY_LABEL, OverlaySnapshot, OverlayState, OverlayStyle};

type CmdResult<T> = Result<T, RequestError>;

const QUICK: Duration = Duration::from_secs(10);
/// Background methods (`hardware.detect`, `models.list`, `models.validate`,
/// `settings.apply_recommendation`, `settings.preview_recommendation`) run `nvidia-smi` and probe the translation
/// server (2 s timeout per address), and the engine runs them one at a time in
/// arrival order, so a request may first wait for the ones queued before it.
const BACKGROUND: Duration = Duration::from_secs(45);
/// The engine answers `session.start` after validating, before loading models.
const SESSION_START: Duration = Duration::from_secs(30);
/// The engine joins its worker threads with a 5 s timeout.
const SESSION_STOP: Duration = Duration::from_secs(15);

const MAX_MODEL_ID_CHARS: usize = 100;
/// `models.download` id of the optional NVIDIA cuBLAS runtime (hardware.md).
pub const GPU_RUNTIME_ID: &str = "gpu-runtime";
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

/// `models.download` parameters. `accept_license` only exists for the GPU
/// runtime (the engine rejects it for ASR models) and is sent only when the user
/// ticked the NVIDIA EULA box; without it the engine answers
/// `license_not_accepted`, so the gate holds even if the UI misbehaves.
pub fn models_download_params(model: &str, accept_license: bool) -> Result<Value, RequestError> {
    validate_model_id(model)?;
    if model == GPU_RUNTIME_ID {
        if accept_license {
            return Ok(json!({ "model": model, "accept_license": true }));
        }
        return Ok(json!({ "model": model }));
    }
    if accept_license {
        return Err(invalid("accept_license only applies to the GPU runtime"));
    }
    Ok(json!({ "model": model }))
}

/// `models.validate` parameters: the candidate partial settings, or none to
/// check the saved settings.
pub fn models_validate_params(settings: Option<&Value>) -> Result<Value, RequestError> {
    match settings {
        Some(patch) => {
            validate_settings_patch(patch)?;
            Ok(json!({ "settings": patch }))
        }
        None => Ok(json!({})),
    }
}

#[tauri::command]
pub async fn engine_info(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("engine.info", json!({}), QUICK).await
}

#[tauri::command]
pub async fn hardware_detect(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("hardware.detect", json!({}), BACKGROUND)
        .await
}

#[tauri::command]
pub async fn models_list(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge.request("models.list", json!({}), BACKGROUND).await
}

#[tauri::command]
pub async fn models_download(
    bridge: State<'_, EngineBridge>,
    model: String,
    accept_license: Option<bool>,
) -> CmdResult<Value> {
    let params = models_download_params(&model, accept_license.unwrap_or(false))?;
    bridge.request("models.download", params, QUICK).await
}

#[tauri::command]
pub async fn models_validate(
    bridge: State<'_, EngineBridge>,
    settings: Option<Value>,
) -> CmdResult<Value> {
    let params = models_validate_params(settings.as_ref())?;
    bridge.request("models.validate", params, BACKGROUND).await
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

/// `only_if_new` is the first-run auto-apply: the engine writes only while no
/// settings were saved yet, so a combination the user just saved is kept.
pub fn apply_recommendation_params(only_if_new: bool) -> Value {
    if only_if_new {
        json!({ "only_if_new": true })
    } else {
        json!({})
    }
}

#[tauri::command]
pub async fn settings_apply_recommendation(
    bridge: State<'_, EngineBridge>,
    only_if_new: Option<bool>,
) -> CmdResult<Value> {
    let params = apply_recommendation_params(only_if_new.unwrap_or(false));
    bridge
        .request("settings.apply_recommendation", params, BACKGROUND)
        .await
}

/// What `settings_apply_recommendation` would write, without saving (`differs`
/// tells the UI whether to offer it).
#[tauri::command]
pub async fn settings_preview_recommendation(bridge: State<'_, EngineBridge>) -> CmdResult<Value> {
    bridge
        .request("settings.preview_recommendation", json!({}), BACKGROUND)
        .await
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

/// Folder inside the resource directory with kikitori's own LICENSE and the
/// notices for the Rust crates and npm packages compiled into the app
/// (generated by `packaging/app/licenses.py`, bundled through `bundle.resources`
/// in `tauri.sidecar.conf.json`).
pub const APP_LICENSES_DIR: &str = "licenses";

/// The app's license folder for a given resource directory.
pub fn app_licenses_dir(resource_dir: &Path) -> PathBuf {
    resource_dir.join(APP_LICENSES_DIR)
}

/// Which bundled license folder to open. The webview picks one of these two
/// names; the path itself is always computed here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LicenseFolder {
    /// The app's own license plus Rust/npm notices ([`APP_LICENSES_DIR`]).
    App,
    /// The engine's `THIRD_PARTY_LICENSES` (PyInstaller-bundled Python components).
    Engine,
}

/// License facts for the settings page's "about" section.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AboutInfo {
    /// The app's own license (Cargo.toml / tauri.conf.json `bundle.license`).
    pub license: &'static str,
    /// The app's license folder, when it exists (release builds).
    pub app_licenses: Option<String>,
    /// The bundled engine's third-party license folder, when it exists
    /// (release builds); development builds have no bundled engine.
    pub engine_licenses: Option<String>,
}

/// `path` as users expect to read and paste it: without the Windows verbatim
/// prefix (`\\?\C:\…` → `C:\…`, `\\?\UNC\server\share` → `\\server\share`).
///
/// Tauri canonicalizes `resource_dir()`, which on Windows adds that prefix; the
/// about page showed it, and the file manager's address bar rejects it.
pub fn display_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        rest.to_owned()
    } else {
        text.into_owned()
    }
}

/// `folder`'s path under `resource_dir`, only when it is an existing directory.
pub fn license_folder(resource_dir: Option<&Path>, folder: LicenseFolder) -> Option<String> {
    let dir = match folder {
        LicenseFolder::App => app_licenses_dir(resource_dir?),
        LicenseFolder::Engine => third_party_licenses_dir(resource_dir?),
    };
    dir.is_dir().then(|| display_path(&dir))
}

pub fn about_info(resource_dir: Option<&Path>) -> AboutInfo {
    AboutInfo {
        license: env!("CARGO_PKG_LICENSE"),
        app_licenses: license_folder(resource_dir, LicenseFolder::App),
        engine_licenses: license_folder(resource_dir, LicenseFolder::Engine),
    }
}

fn resource_dir(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path()
        .resource_dir()
        .map_err(|e| log::error!("cannot resolve the resource directory: {e}"))
        .ok()
}

#[tauri::command]
pub fn app_about(app: AppHandle) -> AboutInfo {
    about_info(resource_dir(&app).as_deref())
}

/// Opens one of the bundled license folders (the app's notices or the engine's
/// THIRD_PARTY_LICENSES) in the file manager. Takes only a [`LicenseFolder`]
/// name from the webview, never a path, so it cannot open anything else.
#[tauri::command]
pub fn open_third_party_licenses(app: AppHandle, folder: LicenseFolder) -> CmdResult<()> {
    let dir = license_folder(resource_dir(&app).as_deref(), folder)
        .ok_or_else(|| invalid("this build has no bundled license folder of that kind"))?;
    app.opener()
        .open_path(dir.clone(), None::<String>)
        .map_err(|e| {
            log::error!("cannot open {dir}: {e}");
            invalid(format!("cannot open the license folder: {e}"))
        })
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

/// Tells the overlay which UI locale the main window uses, so its labels and
/// title follow the language chosen on the settings page.
#[tauri::command]
pub fn overlay_set_locale(
    app: AppHandle,
    overlay: State<'_, OverlayState>,
    locale: String,
) -> CmdResult<OverlaySnapshot> {
    overlay.set_locale(locale).map_err(invalid)?;
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
    fn download_params_gate_the_gpu_runtime_license() {
        assert_eq!(
            models_download_params("base", false).unwrap(),
            json!({"model": "base"})
        );
        assert!(models_download_params("base", true).is_err());
        assert_eq!(
            models_download_params(GPU_RUNTIME_ID, true).unwrap(),
            json!({"model": "gpu-runtime", "accept_license": true})
        );
        // Not accepted: no flag at all, so the engine refuses with license_not_accepted.
        assert_eq!(
            models_download_params(GPU_RUNTIME_ID, false).unwrap(),
            json!({"model": "gpu-runtime"})
        );
        assert!(models_download_params("../x", false).is_err());
    }

    #[test]
    fn apply_params_send_only_if_new_only_when_asked() {
        assert_eq!(apply_recommendation_params(false), json!({}));
        assert_eq!(
            apply_recommendation_params(true),
            json!({"only_if_new": true})
        );
    }

    #[test]
    fn validate_params_wrap_the_candidate() {
        assert_eq!(models_validate_params(None).unwrap(), json!({}));
        let patch = json!({"asr": {"device": "cuda"}});
        assert_eq!(
            models_validate_params(Some(&patch)).unwrap(),
            json!({"settings": {"asr": {"device": "cuda"}}})
        );
        assert!(models_validate_params(Some(&json!([1]))).is_err());
    }

    #[test]
    fn about_reports_licenses_only_when_bundled() {
        let none = about_info(None);
        assert_eq!(none.license, "MIT");
        assert_eq!(none.app_licenses, None);
        assert_eq!(none.engine_licenses, None);

        let resources = std::env::temp_dir().join(format!("kikitori-about-{}", std::process::id()));
        let empty = about_info(Some(&resources));
        assert_eq!((empty.app_licenses, empty.engine_licenses), (None, None));

        // Each folder is reported on its own: an app built without the engine
        // still shows its notices, and vice versa.
        let app_dir = app_licenses_dir(&resources);
        std::fs::create_dir_all(&app_dir).unwrap();
        let only_app = about_info(Some(&resources));
        assert_eq!(
            only_app.app_licenses,
            Some(app_dir.to_string_lossy().into_owned())
        );
        assert_eq!(only_app.engine_licenses, None);

        let engine_dir = third_party_licenses_dir(&resources);
        std::fs::create_dir_all(&engine_dir).unwrap();
        let both = about_info(Some(&resources));
        assert_eq!(
            both.app_licenses,
            Some(app_dir.to_string_lossy().into_owned())
        );
        assert_eq!(
            both.engine_licenses,
            Some(engine_dir.to_string_lossy().into_owned())
        );
        if let Err(e) = std::fs::remove_dir_all(&resources) {
            eprintln!("could not remove {}: {e}", resources.display());
        }
    }

    #[test]
    fn license_paths_are_shown_without_the_verbatim_prefix() {
        // Tauri's resource_dir() is canonicalized, which on Windows yields
        // `\\?\C:\...`; the about page showed that prefix to users, and the
        // file manager's address bar does not accept it.
        assert_eq!(
            display_path(Path::new(r"\\?\C:\Program Files\kikitori\licenses")),
            r"C:\Program Files\kikitori\licenses"
        );
        assert_eq!(
            display_path(Path::new(r"\\?\UNC\server\share\kikitori")),
            r"\\server\share\kikitori"
        );
        assert_eq!(
            display_path(Path::new(r"C:\Program Files\kikitori")),
            r"C:\Program Files\kikitori"
        );
        assert_eq!(
            display_path(Path::new("/usr/lib/kikitori/licenses")),
            "/usr/lib/kikitori/licenses"
        );

        let resources =
            std::env::temp_dir().join(format!("kikitori-verbatim-{}", std::process::id()));
        let app_dir = app_licenses_dir(&resources);
        std::fs::create_dir_all(&app_dir).unwrap();
        if cfg!(windows) {
            let verbatim = PathBuf::from(format!(r"\\?\{}", resources.display()));
            assert_eq!(
                about_info(Some(&verbatim)).app_licenses,
                Some(app_dir.to_string_lossy().into_owned())
            );
        }
        if let Err(e) = std::fs::remove_dir_all(&resources) {
            eprintln!("could not remove {}: {e}", resources.display());
        }
    }

    #[test]
    fn app_licenses_live_in_the_resource_root() {
        let resources = PathBuf::from("/usr/lib/kikitori");
        assert_eq!(app_licenses_dir(&resources), resources.join("licenses"));
        // Same folder name as the bundle.resources target in tauri.sidecar.conf.json.
        let conf: Value = serde_json::from_str(include_str!("../tauri.sidecar.conf.json")).unwrap();
        assert_eq!(
            conf["bundle"]["resources"]["licenses/"],
            json!(format!("{APP_LICENSES_DIR}/"))
        );
    }

    #[test]
    fn license_folder_names_are_the_only_input() {
        assert_eq!(
            serde_json::from_value::<LicenseFolder>(json!("app")).unwrap(),
            LicenseFolder::App
        );
        assert_eq!(
            serde_json::from_value::<LicenseFolder>(json!("engine")).unwrap(),
            LicenseFolder::Engine
        );
        for bad in [json!("../etc"), json!("/tmp"), json!(1), json!(null)] {
            assert!(serde_json::from_value::<LicenseFolder>(bad).is_err());
        }
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
