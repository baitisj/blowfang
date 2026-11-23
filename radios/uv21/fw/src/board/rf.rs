// UV-21 Pro RF bus (BK4829, 3-wire): SCN PB3, SCK PB5, SDA PB6, from the stock firmware and read
// back by the bring-up image (register 0 = 0x4829). SDA is shared with the I2C device on PA4.
use super::pin::{pin, Pin};
use at32f421_pac as pac;

const SCN: Pin = pin(b'B', 3);
const SCK: Pin = pin(b'B', 5);
const SDA: Pin = pin(b'B', 6);
/// Audio amplifier enable, active high: PB2 (inferred from the stock firmware, not yet measured).
const AMP: Pin = pin(b'B', 2);

pub fn init_fd6818_pins(_gpioa: &pac::Gpioa, _gpioc: &pac::Gpioc) {
    SCN.set(true);
    SCN.output();
    SCK.output();
    SDA.output();
    SCK.pull(0);
    SDA.pull(0);
}

pub fn set_fd6818_scn(_gpioc: &pac::Gpioc, high: bool) {
    SCN.set(high);
}

pub fn set_fd6818_sck(_gpioa: &pac::Gpioa, high: bool) {
    SCK.set(high);
}

pub fn set_shared_sda(_gpioa: &pac::Gpioa, high: bool) {
    SDA.set(high);
}

pub fn set_shared_sda_input(_gpioa: &pac::Gpioa) {
    SDA.input(1);
}

pub fn set_shared_sda_output(_gpioa: &pac::Gpioa) {
    SDA.output();
    SDA.pull(0);
}

pub fn read_shared_sda(_gpioa: &pac::Gpioa) -> bool {
    SDA.read()
}

pub fn init_speaker_switch_pin(_gpiob: &pac::Gpiob) {
    AMP.output();
}

pub fn set_speaker_switch(_gpiob: &pac::Gpiob, on: bool) {
    AMP.set(on);
}
