//! UV-21: the 5RH's splash (header, welcome lines, battery, logo) is gone; boot only clears.
use super::{SCREEN_H, SCREEN_W};
use crate::drivers::st7735::St7735;

pub fn clear(lcd: &mut St7735) {
    lcd.fill_rect(0, 0, SCREEN_W, SCREEN_H, 0x0000);
}
