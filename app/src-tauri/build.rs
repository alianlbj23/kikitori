// Declaring the app's commands generates `allow-<command>` permissions, so each
// window can only call the commands its capability file lists (least privilege).
fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "engine_info",
            "hardware_detect",
            "models_list",
            "models_download",
            "models_validate",
            "devices_list",
            "settings_get",
            "settings_set",
            "settings_apply_recommendation",
            "session_start",
            "session_stop",
            "engine_shutdown",
            "engine_restart",
            "bridge_status",
            "app_about",
            "open_third_party_licenses",
            "overlay_get_state",
            "overlay_set_style",
            "overlay_set_click_through",
            "overlay_set_visible",
        ]),
    ))
    .expect("failed to run tauri-build");
}
