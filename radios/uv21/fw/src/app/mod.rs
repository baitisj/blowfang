//! UV-21 Pro: what the firmware keeps besides SURF (radios/uv21/surf), the only screen since
//! port 14 (the operator, 2026-11-24: "Take out the VFO code from the UV-21 port"). The UV-5RH's
//! VFO and channel screens, sides, dual watch, menus, FM broadcast radio and their key handling
//! are gone; this holds the radio, the keypad, the battery reading and the backlight timer that
//! SURF's housekeeping calls.

use crate::device::keypad::{KeyEvent, Keypad};
use crate::device::radio::Radio;
use crate::device::storage::Storage;
use crate::flash_map::{self, addr};
use cortex_m::peripheral::SYST;

pub const FIRMWARE_VERSION: &str = env!("GIT_VERSION");

/// housekeeping ticks (SURF calls it every 50 ms) per backlight-delay step
const BACKLIGHT_STEP_TICKS: u16 = 100;

pub struct App<'a> {
    radio: Radio<'a>,
    keypad: Keypad<'a>,
    /// the backlight delay setting from the SPI flash, 0-4 (0 = always on); read only
    backlight_time: u8,
    bl_idle_ticks: u16,
    battery_cal: [u8; addr::DEV_BATT_LEN],
    battery_bars: u8,
}

impl<'a> App<'a> {
    pub fn new(radio: Radio<'a>, keypad: Keypad<'a>, storage: &mut Storage<'_>) -> Self {
        let settings = storage
            .load_settings()
            .unwrap_or(flash_map::Settings::DEFAULT);
        let mut app = App {
            radio,
            keypad,
            backlight_time: settings.backlight_time,
            bl_idle_ticks: 0,
            battery_cal: storage.read_battery_calibration(),
            battery_bars: 4,
        };
        app.note_activity();
        app
    }

    pub fn radio_mut(&mut self) -> &mut Radio<'a> {
        &mut self.radio
    }

    /// SURF's keys: the keypad's next event (after a poll); a key keeps the backlight on
    pub fn surf_key_event(&mut self, syst: &mut SYST) -> Option<KeyEvent> {
        self.keypad.poll(syst);
        let ev = self.keypad.pop_event();
        if ev.is_some() {
            self.note_activity();
        }
        ev
    }

    fn note_activity(&mut self) {
        self.bl_idle_ticks = self.backlight_time as u16 * BACKLIGHT_STEP_TICKS;
    }

    pub fn poll_backlight(&mut self) {
        if self.radio.audio_is_open() {
            self.note_activity();
        }
        if self.bl_idle_ticks > 0 {
            self.bl_idle_ticks -= 1;
        }
    }

    pub fn backlight_should_be_on(&self) -> bool {
        self.backlight_time == 0 || self.bl_idle_ticks > 0
    }

    pub fn poll_battery(&mut self) {
        let raw = (self.radio.read_battery_raw() >> 4) as u8;
        let t = &self.battery_cal;
        self.battery_bars = if raw > t[5] {
            4
        } else if raw > t[4] {
            3
        } else if raw > t[3] {
            2
        } else if raw > t[2] {
            1
        } else {
            0
        };
    }

    #[allow(dead_code)]
    pub fn battery_bars(&self) -> u8 {
        self.battery_bars
    }
}
