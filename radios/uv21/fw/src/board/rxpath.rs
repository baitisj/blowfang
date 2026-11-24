// UV-21 Pro: the receive front end's band switch. The stock firmware drives PA14 for VHF and 220
// and PA13 for UHF (docs/uv21-characteristics.md, read from the stock image); they are the SWD
// pins, so driving them ends SWD, which nothing here uses (the KDH bootloader reflashes). Ports
// 1 to 11 left them as SWD, with only the internal pulls on them, and receive was weak.
//
// The stock image (read 2026-11-24) drives the band's pin high on receive and both low in idle
// and transmit, which is mode 1, the default. Port 12 was written before that read, so it carries
// four ways to switch, and SURF's 6 key still steps through them as a cross-check:
//
// | mode | PA13 / PA14                     | RF chip GPIO5 / GPIO6 (the UV-5RH's switch) |
// |------|---------------------------------|---------------------------------------------|
// | 0    | left as SWD (ports 1 to 11)     | driven by band                              |
// | 1    | the band's pin high, other low  | off                                         |
// | 2    | the band's pin low, other high  | off                                         |
// | 3    | as mode 1                       | driven by band                              |
//
// GPIO5/6 come off in modes 1 and 2 because on this board they aren't the band switch: one of
// them probably lights the top LED faintly on the bands below 300 MHz.
use super::pin::{pin, Pin};

const RX_UHF: Pin = pin(b'A', 13);
const RX_VHF: Pin = pin(b'A', 14);

pub const RX_PATH_MODES: u8 = 4;
pub const RX_PATH_DEFAULT: u8 = 1;

static mut MODE: u8 = 0;
static mut UHF: bool = true;

pub fn rx_path_mode() -> u8 {
    unsafe { MODE }
}

/// whether this mode also drives the RF chip's GPIO5/GPIO6 by band
pub fn rx_path_rf_gpio56(mode: u8) -> bool {
    mode == 0 || mode == 3
}

/// takes the pins for `mode` (0 hands them back to SWD) and switches to the last band set
pub fn set_rx_path_mode(mode: u8) {
    let mode = mode % RX_PATH_MODES;
    unsafe { MODE = mode };
    if mode == 0 {
        RX_UHF.alternate(0); // SWDIO, pull-up as after reset
        RX_UHF.pull(1);
        RX_VHF.alternate(0); // SWCLK, pull-down
        RX_VHF.pull(2);
        return;
    }
    set_rx_path(unsafe { UHF });
    RX_UHF.pull(0);
    RX_VHF.pull(0);
    RX_UHF.output();
    RX_VHF.output();
}

/// the receive band: UHF (300 MHz and up) or VHF and 220
pub fn set_rx_path(uhf: bool) {
    unsafe { UHF = uhf };
    let m = rx_path_mode();
    if m == 0 {
        return;
    }
    let on = m != 2;
    RX_UHF.set(if uhf { on } else { !on });
    RX_VHF.set(if uhf { !on } else { on });
}
