// UV-21 Pro: the flashlight is PB7 (the bootloader toggles it in flash mode).
use super::pin::{pin, Pin};
use at32f421_pac as pac;

const FLASHLIGHT: Pin = pin(b'B', 7);

pub fn init_flashlight_pin(_gpiof: &pac::Gpiof) {
    FLASHLIGHT.output();
}

pub fn set_flashlight(_gpiof: &pac::Gpiof, on: bool) {
    FLASHLIGHT.set(on);
}
