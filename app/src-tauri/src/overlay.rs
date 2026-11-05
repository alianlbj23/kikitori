//! Subtitle overlay window state.
//!
//! Role: the overlay window is transparent and may be click-through, so it cannot
//! host its own settings UI. The main window changes the overlay's appearance and
//! behaviour through commands that land here; this module validates those values
//! and keeps the authoritative copy so a freshly loaded overlay can ask for it.
//! Persisting the style is the main window's job (engine `settings.subtitle`).

use std::sync::Mutex;

use serde::{Deserialize, Serialize};

pub const OVERLAY_LABEL: &str = "overlay";
pub const MAIN_LABEL: &str = "main";
/// Global shortcut that toggles click-through; documented in docs/specs/ui.md.
pub const CLICK_THROUGH_SHORTCUT: &str = "CommandOrControl+Alt+K";

pub const FONT_SIZE_RANGE: (u32, u32) = (14, 96);
pub const MAX_LINES_RANGE: (u32, u32) = (1, 3);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OverlayPosition {
    Top,
    Bottom,
}

/// Appearance of the overlay; field names match `settings.subtitle` in the engine.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OverlayStyle {
    /// Translation line size in CSS px; the original line is derived from it.
    pub font_size: u32,
    /// Opacity of the backing plate behind the text, 0–1.
    pub background_opacity: f64,
    /// Which edge of the overlay window the text sticks to.
    pub position: OverlayPosition,
    /// How many utterances the overlay shows at once.
    pub max_lines: u32,
}

impl Default for OverlayStyle {
    fn default() -> Self {
        Self {
            font_size: 32,
            background_opacity: 0.45,
            position: OverlayPosition::Bottom,
            max_lines: 2,
        }
    }
}

impl OverlayStyle {
    pub fn validate(&self) -> Result<(), String> {
        let (lo, hi) = FONT_SIZE_RANGE;
        if !(lo..=hi).contains(&self.font_size) {
            return Err(format!(
                "font_size must be in [{lo}, {hi}], got {}",
                self.font_size
            ));
        }
        if !self.background_opacity.is_finite() || !(0.0..=1.0).contains(&self.background_opacity) {
            return Err(format!(
                "background_opacity must be in [0, 1], got {}",
                self.background_opacity
            ));
        }
        let (lo, hi) = MAX_LINES_RANGE;
        if !(lo..=hi).contains(&self.max_lines) {
            return Err(format!(
                "max_lines must be in [{lo}, {hi}], got {}",
                self.max_lines
            ));
        }
        Ok(())
    }
}

/// Snapshot sent to windows (`overlay_get_state` and `overlay://state`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct OverlaySnapshot {
    pub style: OverlayStyle,
    pub click_through: bool,
    pub visible: bool,
    pub shortcut: &'static str,
    /// False when the global shortcut could not be registered (taken by
    /// another app); the UI then only offers the in-app toggle.
    pub shortcut_registered: bool,
}

#[derive(Debug)]
struct Inner {
    style: OverlayStyle,
    click_through: bool,
    visible: bool,
    shortcut_registered: bool,
}

/// Tauri-managed overlay state.
#[derive(Debug)]
pub struct OverlayState(Mutex<Inner>);

impl Default for OverlayState {
    fn default() -> Self {
        Self(Mutex::new(Inner {
            style: OverlayStyle::default(),
            // Start interactive so a first-time user can move the window; the
            // shortcut makes it click-through once it is in place.
            click_through: false,
            visible: false,
            shortcut_registered: false,
        }))
    }
}

impl OverlayState {
    fn with<T>(&self, f: impl FnOnce(&mut Inner) -> T) -> T {
        let mut guard = self.0.lock().unwrap_or_else(|p| p.into_inner());
        f(&mut guard)
    }

    pub fn snapshot(&self) -> OverlaySnapshot {
        self.with(|s| OverlaySnapshot {
            style: s.style.clone(),
            click_through: s.click_through,
            visible: s.visible,
            shortcut: CLICK_THROUGH_SHORTCUT,
            shortcut_registered: s.shortcut_registered,
        })
    }

    pub fn set_style(&self, style: OverlayStyle) -> Result<(), String> {
        style.validate()?;
        self.with(|s| s.style = style);
        Ok(())
    }

    pub fn set_click_through(&self, enabled: bool) {
        self.with(|s| s.click_through = enabled);
    }

    pub fn set_visible(&self, visible: bool) {
        self.with(|s| s.visible = visible);
    }

    pub fn set_shortcut_registered(&self, ok: bool) {
        self.with(|s| s.shortcut_registered = ok);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_style_is_valid() {
        assert!(OverlayStyle::default().validate().is_ok());
    }

    #[test]
    fn style_ranges() {
        let bad = [
            OverlayStyle {
                font_size: 13,
                ..Default::default()
            },
            OverlayStyle {
                font_size: 97,
                ..Default::default()
            },
            OverlayStyle {
                background_opacity: -0.1,
                ..Default::default()
            },
            OverlayStyle {
                background_opacity: 1.5,
                ..Default::default()
            },
            OverlayStyle {
                background_opacity: f64::NAN,
                ..Default::default()
            },
            OverlayStyle {
                max_lines: 0,
                ..Default::default()
            },
            OverlayStyle {
                max_lines: 4,
                ..Default::default()
            },
        ];
        for style in bad {
            assert!(style.validate().is_err(), "{style:?}");
        }
    }

    #[test]
    fn style_rejects_unknown_fields_and_bad_position() {
        let ok: OverlayStyle = serde_json::from_str(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1}"#,
        )
        .unwrap();
        assert_eq!(ok.position, OverlayPosition::Top);
        assert!(
            serde_json::from_str::<OverlayStyle>(
                r#"{"font_size":40,"background_opacity":0.2,"position":"left","max_lines":1}"#
            )
            .is_err()
        );
        assert!(serde_json::from_str::<OverlayStyle>(
            r#"{"font_size":40,"background_opacity":0.2,"position":"top","max_lines":1,"color":"red"}"#
        )
        .is_err());
    }

    #[test]
    fn state_updates() {
        let state = OverlayState::default();
        assert!(!state.snapshot().click_through);
        state.set_click_through(true);
        assert!(state.snapshot().click_through);
        state.set_visible(true);
        assert!(state.snapshot().visible);
        let bad = OverlayStyle {
            font_size: 2,
            ..Default::default()
        };
        assert!(state.set_style(bad).is_err());
        assert_eq!(state.snapshot().style, OverlayStyle::default());
    }
}
