// UV-21 Pro: the power latch is PA8 (high = stay on) and the power switch's sense is PA15 (high =
// switched off), both from the stock firmware (blowfang docs/uv21-characteristics.md).
use super::pin::{pin, Pin};
use at32f421_pac as pac;
use core::sync::atomic::{AtomicBool, Ordering};

const POWER_LATCH: Pin = pin(b'A', 8);
const POWER_SENSE: Pin = pin(b'A', 15);

/// False when the sense read "off" at boot: then its polarity is wrong and it is never acted on,
/// so a wrong guess can't switch the radio off at every boot (as in the bring-up image).
static SENSE_OK: AtomicBool = AtomicBool::new(false);

pub fn init_power_latch_pin(_gpiof: &pac::Gpiof) {
    POWER_LATCH.output();
}

pub fn set_power_latch(_gpiof: &pac::Gpiof, on: bool) {
    POWER_LATCH.set(on);
}

pub fn init_power_key_pin(_gpiob: &pac::Gpiob) {
    POWER_SENSE.input(0);
    for _ in 0..1000 {
        cortex_m::asm::nop();
    }
    SENSE_OK.store(!POWER_SENSE.read(), Ordering::Relaxed);
}

/// `true` when the switch says off (and the sense is trusted).
pub fn read_power_key(_gpiob: &pac::Gpiob) -> bool {
    SENSE_OK.load(Ordering::Relaxed) && POWER_SENSE.read()
}

pub fn power_sense_ok() -> bool {
    SENSE_OK.load(Ordering::Relaxed)
}
