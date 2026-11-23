// UV-21 Pro SPI flash: SPI1 on PA5/PA6/PA7 (AF0) as on the UV-5RH, chip select PA2 (PA8 is the
// power latch here).
use super::pin::{pin, Pin};
use at32f421_pac as pac;

const CS: Pin = pin(b'A', 2);

pub fn init_norflash_pins(_gpioa: &pac::Gpioa) {
    for p in [pin(b'A', 5), pin(b'A', 6), pin(b'A', 7)] {
        p.alternate(0);
        p.pull(0);
    }
    CS.set(true);
    CS.output();
}

pub fn set_norflash_cs(_gpioa: &pac::Gpioa, high: bool) {
    CS.set(high);
}
