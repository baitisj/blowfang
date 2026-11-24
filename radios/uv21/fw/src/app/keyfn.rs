use super::App;
use cortex_m::peripheral::SYST;

// TODO: FM radio was made as app, add key function to app
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum KeyFunction {
    None,
    WideNarrow,
    Monitor,
    Mode,
    Flashlight,
}

impl KeyFunction {
    pub fn label(self) -> &'static str {
        match self {
            KeyFunction::None => "NONE",
            KeyFunction::WideNarrow => "WIDE/NAR",
            KeyFunction::Monitor => "MONITOR",
            KeyFunction::Mode => "MODE",
            KeyFunction::Flashlight => "LIGHT",
        }
    }
}

pub(super) fn from_u8(v: u8) -> KeyFunction {
    match v {
        1 => KeyFunction::WideNarrow,
        2 => KeyFunction::Monitor,
        3 => KeyFunction::Mode,
        // 4 => TX tone, 7 => power, 10 => reverse: gone (the UV-21 image never transmits)
        // 5=> fm radio
        // 6 => scan: gone, SURF does it (UV-21)
        8 => KeyFunction::Flashlight,
        // 9 => search (now app)
        _ => KeyFunction::None,
    }
}

pub(super) fn invoke(app: &mut App, syst: &mut SYST, func: KeyFunction) {
    match func {
        KeyFunction::None => {}
        KeyFunction::WideNarrow => app.toggle_wide_narrow(syst),
        KeyFunction::Monitor => app.radio.toggle_monitor(),
        KeyFunction::Mode => app.toggle_modulation(syst),
        // TODO: fm radio
        KeyFunction::Flashlight => app.flashlight.toggle(),
        // TODO: search
    }
}
