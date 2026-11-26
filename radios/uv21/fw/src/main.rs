#![no_std]
#![no_main]

mod app;
mod board;
mod device;
mod drivers;
mod flash_map;
mod hal;
mod surf;
mod ui;
mod umod;

use at32f421_pac as pac;
use core::fmt::Write;
use cortex_m_rt::entry;
use panic_halt as _;

use device::radio::{
    AniConfig, ChannelConfig, Modulation, Power, Radio, SubAudio,
};
use drivers::fd6818b::Fd6818;
use drivers::norflash::NorFlash;
use hal::clock::{self, SCLK_HZ};

#[cfg_attr(not(debug_assertions), allow(dead_code))]
const BAUD: u32 = 115_200;

const TEST_FREQ_HZ: u32 = 439_500_000;
const WIDE_BAND: bool = true;


/// Serial diagnostics only ever run in a debug (`cargo build`, no
/// `--release`) image -- this board's 60K flash budget (`memory.x`) has no
/// room to spare for `core::fmt` formatting machinery in the shipped
/// release binary. Every call site uses this instead of `writeln!` directly
/// so the argument expressions (and any `{..}` formatting they pull in)
/// vanish entirely under `--release` rather than merely going silent.
macro_rules! dbg_println {
    ($serial:expr, $($arg:tt)*) => {{
        #[cfg(debug_assertions)]
        {
            writeln!($serial, $($arg)*).ok();
        }
        #[cfg(not(debug_assertions))]
        {
            let _ = &$serial;
        }
    }};
}

struct Serial {
    usart1: pac::Usart1,
}

impl Serial {
    fn write_byte(&mut self, b: u8) {
        while self.usart1.sts().read().tdbe().bit_is_clear() {}
        self.usart1.dt().write(|w| unsafe { w.dt().bits(b as u16) });
    }
}

impl Write for Serial {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for &b in s.as_bytes() {
            if b == b'\n' {
                self.write_byte(b'\r');
            }
            self.write_byte(b);
        }
        Ok(())
    }
}

impl Serial {
    #[cfg_attr(not(debug_assertions), allow(dead_code))]
    fn flush(&mut self) {
        while self.usart1.sts().read().tdc().bit_is_clear() {}
    }
}

/// One-time boot calibration and App construction kept out of `main()`'s permanent stack frame
/// so Storage, calibration buffers and settings temporaries are reclaimed before SURF runs.
#[inline(never)]
fn boot_build_app<'a>(
    gpioa: &'a pac::Gpioa,
    gpiob: &'a pac::Gpiob,
    spi1: &'a pac::Spi1,
    adc: &'a pac::Adc,
    cp: &mut cortex_m::Peripherals,
    mut rfic: Fd6818<'a>,
    serial: &Serial,
) -> app::App<'a> {
    let norflash = NorFlash::new(gpioa, spi1);
    let mut storage = device::storage::Storage::new(norflash);
    let mut flag_byte = [0u8; 1];
    storage.read_raw(flash_map::addr::PA_TABLE_BASE_HIGH, &mut flag_byte);
    let cal = storage.read_calibration();
    if flag_byte[0] != 0xFF {
        rfic.set_xtal_adjust(cal[6]);
        rfic.set_audio_calibration(cal[0], cal[1], cal[2], cal[3], cal[4]);
        rfic.set_squelch_cal(storage.read_squelch_cal());
    }
    hal::con::puts("calibration");
    hal::con::kv_hex("applied", (flag_byte[0] != 0xFF) as u32, 1);
    hal::con::nl();
    dbg_println!(
        serial,
        "calibration {}",
        if flag_byte[0] != 0xFF {
            "applied"
        } else {
            "blank, using defaults"
        }
    );

    // AF response filters: the UV-21 stock init's own values (REG_44/45/74/75, fd6818b.rs init)
    // stand; the UV-5RH pushed its flat table entry here, which would undo them.

    // UV-21 Pro: the receive front end's band switch on PA13/PA14 (board/rxpath.rs), and the
    // UV-5RH's GPIO5/GPIO6 switch only in the modes that keep it
    board::set_rx_path_mode(board::RX_PATH_DEFAULT);
    rfic.set_rf_gpio56(&mut cp.SYST, board::rx_path_rf_gpio56(board::RX_PATH_DEFAULT));

    let cfg = ChannelConfig {
        freq_hz: TEST_FREQ_HZ,
        tx_freq_hz: TEST_FREQ_HZ,
        wide_band: WIDE_BAND,
        power: Power::Low,
        subaudio_tx: SubAudio::None,
        subaudio_rx: SubAudio::None,
        modulation: Modulation::Fm,
    };

    // FIXME: placeholder
    let ani = AniConfig::from_raw([0, 0, 0], 0);
    let radio = Radio::new(rfic, gpioa, gpiob, adc, cfg, ani);

    let keypad = device::keypad::Keypad::new(gpioa, gpiob);
    app::App::new(radio, keypad, &mut storage)
}

#[entry]
fn main() -> ! {
    let dp = unsafe { pac::Peripherals::steal() };
    let mut cp = cortex_m::Peripherals::take().unwrap();

    cp.SYST.disable_interrupt();
    cp.SYST.disable_counter();
    let nvic = unsafe { &*cortex_m::peripheral::NVIC::PTR };
    for reg in nvic.icer.iter() {
        unsafe { reg.write(0xFFFF_FFFF) };
    }
    for reg in nvic.icpr.iter() {
        unsafe { reg.write(0xFFFF_FFFF) };
    }

    unsafe { cp.SCB.vtor.write(0x0800_1000) };

    // UV-21 Pro: GPIOA clock for PA8 (power self-latch); GPIOF for the keypad row and backlight
    dp.crm
        .ahben()
        .modify(|_, w| w.gpioaen().set_bit().gpiofen().set_bit());
    let _ = dp.crm.ahben().read();

    // PA8 = power self-latch, driven high
    board::init_power_latch_pin(&dp.gpiof);
    board::set_power_latch(&dp.gpiof, true);

    // UV-21: one line on the bootloader's own serial setup (115200 at its 48 MHz) before the
    // clock changes, so an image that never reaches the banner still shows that it started
    if dp.crm.apb2en().read().usart1en().bit_is_set()
        && dp.usart1.ctrl1().read().uen().bit_is_set()
        && dp.usart1.ctrl1().read().ten().bit_is_set()
    {
        hal::con::puts("blowfang uv21 port start\r\n");
        hal::uart::flush();
    }

    clock::configure_system_clock(&dp.crm, &dp.flash);

    // Remaining clocks: GPIOA/B/C + USART1 + SPI1 (calibration flash)
    dp.crm.ahben().modify(|_, w| {
        w.gpioaen()
            .set_bit()
            .gpioben()
            .set_bit()
            .gpiocen()
            .set_bit()
    });
    dp.crm.apb2en().modify(|_, w| {
        w.usart1en().set_bit().spi1en().set_bit().adcen().set_bit()
    });
    dp.crm
        .apb1en()
        .modify(|_, w| w.spi2en().set_bit().tmr14en().set_bit());
    let _ = dp.crm.ahben().read();

    // PA0/PA1 analog mode for the VOX mic level / battery voltage ADC
    dp.gpioa
        .cfgr()
        .modify(|_, w| unsafe { w.iomc0().bits(0b11).iomc1().bits(0b11) });
    dp.gpioa
        .pull()
        .modify(|_, w| unsafe { w.pull0().bits(0b00).pull1().bits(0b00) });

    // TMR14 free-running 100us tick, backing `device::keypad::Keypad`'s
    // debounce/long-press/repeat timing (`hal/uptime.rs`).
    hal::uptime::init();

    // PB0 = power key / detect input
    board::init_power_key_pin(&dp.gpiob);

    // PA9 = USART1_TX (AF mux 1).
    board::init_debug_uart_tx_pin(&dp.gpioa);

    // PA10 = PTT, input with pull-up.
    board::init_ptt_rxd_pin(&dp.gpioa);

    // (the UV-5RH's TX/RX LEDs on PA13/PA14; the UV-21 has none: board/rxpath.rs takes them)
    board::init_tx_rx_led_pins(&dp.gpioa);
    board::set_tx_led(&dp.gpioa, false);
    board::set_rx_led(&dp.gpioa, false);

    // PB7 = audio amp enable, push-pull output, driven high (amp on) to start.
    board::init_speaker_switch_pin(&dp.gpiob);
    board::set_speaker_switch(&dp.gpiob, true);

    // RFIC bus pins.
    board::init_fd6818_pins(&dp.gpioa, &dp.gpioc);

    // Calibration flash: SPI1 master, mode 0, software CS, 8-bit frames,
    // ~3.4 MHz (SCLK_HZ / 32) -- plenty slow/safe for a JEDEC SPI-NOR read.
    board::init_norflash_pins(&dp.gpioa);
    dp.spi1.ctrl2().write(|w| w.mdiv3().clear_bit());
    dp.spi1.ctrl1().write(|w| unsafe {
        w.msten()
            .set_bit()
            .clkpha()
            .clear_bit()
            .clkpol()
            .clear_bit()
            .mdiv2_0()
            .bits(0b100)
            .ltf()
            .clear_bit()
            .swcsen()
            .set_bit()
            .swcsil()
            .set_bit()
            .fbn()
            .clear_bit()
            .spien()
            .set_bit()
    });

    board::init_lcd_control_pins(&dp.gpiob);
    board::init_lcd_backlight_pin(&dp.gpioa);
    // LCD: SPI2 at PCLK / 4 = 15 MHz (port 17; ports 1-16 ran /8, 7.5 MHz). The ST7735S's
    // serial write cycle is 66 ns at least, so 15 MHz is its rated top speed.
    dp.spi2.ctrl1().write(|w| unsafe {
        w.msten()
            .set_bit()
            .mdiv2_0()
            .bits(1)
            .swcsen()
            .set_bit()
            .swcsil()
            .set_bit()
    });
    dp.spi2.ctrl1().modify(|_, w| w.spien().set_bit());

    // Keypad matrix
    board::init_keypad_pins(&dp.gpiob);
    board::init_side_key2_pin(&dp.gpioa);

    // uart
    // UV-21: from the clock this port runs at (the 5RH's 1042 was for 120 MHz: 57,600 baud here)
    let div: u16 = ((SCLK_HZ + BAUD / 2) / BAUD) as u16;
    dp.usart1.baudr().write(|w| unsafe { w.div().bits(div) });
    dp.usart1.ctrl1().write(|w| {
        w.uen()
            .set_bit()
            .rdbfien()
            .set_bit()
            .ten()
            .set_bit()
            .ren()
            .set_bit()
    });

    unsafe { cortex_m::peripheral::NVIC::unmask(pac::Interrupt::USART1) };
    unsafe { cortex_m::interrupt::enable() };

    let serial = Serial { usart1: dp.usart1 };
    hal::con::puts("blowfang uv21 port 20 build=");
    hal::con::puts(app::FIRMWARE_VERSION);
    hal::con::kv_hex("sclk_mhz", SCLK_HZ / 1_000_000, 2);
    hal::con::kv_hex("power_sense_ok", board::power_sense_ok() as u32, 1);
    hal::con::puts(" rx_only=1");
    if drivers::norflash::READ_ONLY {
        hal::con::puts(" nor_read_only=1");
    }
    hal::con::nl();
    dbg_println!(
        serial,
        "sclk={SCLK_HZ}Hz (HICK/2 PLL) baud={BAUD} div={div}"
    );

    let mut display = device::display::Display::new(&dp.gpiob, &dp.spi2);
    display.init(&mut cp.SYST);
    ui::boot::clear(display.as_draw_target());
    let backlight = device::display::Backlight::new(&dp.gpioa);
    dbg_println!(serial, "lcd: init done");

    let mut rfic = Fd6818::new(&dp.gpioa, &dp.gpioc);
    let chip_id = rfic.chip_id(&mut cp.SYST);
    dbg_println!(serial, "fd6818 chip id = {chip_id:#06x} (expect 0x4829)");
    hal::con::puts("rf");
    hal::con::kv_hex("id", chip_id as u32, 4);
    hal::con::nl();

    rfic.init(&mut cp.SYST);
    dbg_println!(serial, "fd6818 init done");

    let mut app = boot_build_app(&dp.gpioa, &dp.gpiob, &dp.spi1, &dp.adc, &mut cp, rfic, &serial);
    app.poll_battery();

    // UV-21: no splash, boot tune or wait (the operator asked for none): straight to SURF, the
    // only screen (port 14: no VFO). SURF returns only if its housekeeping asks; it starts again
    // where it was.
    backlight.on();

    hal::con::puts("boot done");
    hal::con::nl();

    let mut start = TEST_FREQ_HZ;
    loop {
        let cen = surf::run(&mut app, &mut cp.SYST, &mut display, start);
        if cen != 0 {
            start = cen;
        }
    }
}

