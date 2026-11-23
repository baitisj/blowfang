// UV-21 Pro: there are no TX/RX LEDs on the UV-5RH's PA13/PA14. The stock firmware uses those
// pins as the receive-band switch (polarity not known yet), so they stay SWD and these do nothing.
use at32f421_pac as pac;

pub fn init_tx_rx_led_pins(_gpioa: &pac::Gpioa) {}

pub fn set_tx_led(_gpioa: &pac::Gpioa, _on: bool) {}

pub fn set_rx_led(_gpioa: &pac::Gpioa, _on: bool) {}
