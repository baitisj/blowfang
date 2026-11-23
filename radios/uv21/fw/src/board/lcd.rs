// UV-21 Pro LCD: SPI2 SCK PB13 / MOSI PB15 (AF0) as on the UV-5RH; CS PB12, reset PB8, D/C PC15;
// backlight PF7, high = on (all from the stock firmware, backlight proven by bring-up image 2).
use super::pin::{pin, Pin};
use at32f421_pac as pac;

const CS: Pin = pin(b'B', 12);
const RST: Pin = pin(b'B', 8);
const DC: Pin = pin(b'C', 15);
const SCK: Pin = pin(b'B', 13);
const MOSI: Pin = pin(b'B', 15);
const BACKLIGHT: Pin = pin(b'F', 7);

pub fn init_lcd_control_pins(_gpiob: &pac::Gpiob) {
    CS.set(true);
    RST.set(true);
    CS.output();
    RST.output();
    DC.output();
    SCK.alternate(0);
    MOSI.alternate(0);
}

pub fn set_lcd_cs(_gpiob: &pac::Gpiob, high: bool) {
    CS.set(high);
}

pub fn set_lcd_dc(_gpiob: &pac::Gpiob, data_mode: bool) {
    DC.set(data_mode);
}

pub fn set_lcd_reset(_gpiob: &pac::Gpiob, high: bool) {
    RST.set(high);
}

pub fn init_lcd_backlight_pin(_gpioa: &pac::Gpioa) {
    BACKLIGHT.set(false);
    BACKLIGHT.output();
}

pub fn set_lcd_backlight(_gpioa: &pac::Gpioa, on: bool) {
    BACKLIGHT.set(on);
}
