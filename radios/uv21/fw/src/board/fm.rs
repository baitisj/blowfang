// UV-21 Pro: the I2C device that shares the RF chip's SDA (PB6) has its clock on PA4 (stock
// firmware). It answers at 0x60 in the dump, which isn't the RDA5807's address, so the FM radio
// may not work yet; the pins are right either way.
use super::pin::{pin, Pin};
use at32f421_pac as pac;

const SCL: Pin = pin(b'A', 4);

pub fn init_rda5807_scl_pin(_gpioc: &pac::Gpioc) {
    SCL.output();
    SCL.set(true);
}

pub fn set_rda5807_scl(_gpioc: &pac::Gpioc, high: bool) {
    SCL.set(high);
}
