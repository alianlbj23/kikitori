// Declaring the app's commands generates `allow-<command>` permissions, so each
// window can only call the commands its capability file lists (least privilege).
fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "engine_info",
            "hardware_detect",
            "models_list",
            "models_download",
            "devices_list",
            "settings_get",
            "settings_set",
            "session_start",
            "session_stop",
            "engine_shutdown",
            "engine_restart",
            "bridge_status",
            "overlay_get_state",
            "overlay_set_style",
            "overlay_set_click_through",
            "overlay_set_visible",
        ]),
    ))
    .expect("failed to run tauri-build");
}
