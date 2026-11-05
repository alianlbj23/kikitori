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
            "models_pull_translation",
            "languages_list",
            "devices_list",
            "settings_get",
            "settings_set",
            "settings_apply_recommendation",
            "settings_preview_recommendation",
            "settings_preview_languages",
            "session_start",
            "session_stop",
            "session_set_translation",
            "engine_shutdown",
            "engine_restart",
            "bridge_status",
            "app_about",
            "open_third_party_licenses",
            "output_info",
            "transcript_save",
            "open_output_folder",
            "obs_set_output",
            "overlay_get_state",
            "overlay_set_style",
            "overlay_set_click_through",
            "overlay_set_visible",
            "overlay_set_locale",
            "overlay_set_show_translation",
            "update_check",
            "update_install",
        ]),
    ))
    .expect("failed to run tauri-build");
}
