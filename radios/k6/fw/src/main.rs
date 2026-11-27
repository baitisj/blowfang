#![no_std]
#![no_main]

#[macro_use]
mod out;
mod app;

mod board;
mod cps;
mod device;
mod drivers;
mod flash_map;
mod hal;
mod ui;

#[cfg(not(feature = "pager-only"))]
use core::fmt::Write as _;
use cortex_m_rt::entry;
use kd32f328_pac::Peripherals;
// panics: app::pager reports them on the serial cable and reboots (was panic_halt)

#[cfg(not(feature = "pager-only"))]
use app::satellite;
use device::display::{Backlight, Display};
use device::flashlight::Flashlight;
use device::fm_radio::FmRadio;
use device::keypad::{KeyId, Keypad};
use device::power::Power;
use device::radio::{AniConfig, ChannelConfig, Modulation, Power as TxPower, Radio, SubAudio};
use device::storage::Storage;
use drivers::{display_spec, fd6818, norflash};
use hal::{clock, delay, hal_shim, scheduler, spi, uart};
use hal_shim::{ClosurePin, SystDelay};

const DEFAULT_FREQ_HZ: u32 = 439_500_000;

// in the bootloader's idle RAM (hal::ram0): its 2.1 KB went to the shared block. Not in all of
// Aura (the size report build), whose App is bigger than the room left there
#[cfg_attr(feature = "pager-only", link_section = ".ram0")]
static mut APP_SLOT: core::mem::MaybeUninit<app::App> = core::mem::MaybeUninit::uninit();

#[inline(never)]
#[allow(clippy::too_many_arguments)]
fn place_app(
    slot: &'static mut core::mem::MaybeUninit<app::App>,
    radio: Radio,
    chip_id: u16,
    keypad: Keypad,
    storage: Storage,
    flashlight: Flashlight,
    fm_radio: FmRadio<'static>,
    cfg: ChannelConfig,
    syst: &mut cortex_m::peripheral::SYST,
) -> &'static mut app::App {
    slot.write(app::App::new(radio, chip_id, keypad, storage, flashlight, fm_radio, cfg, syst))
}

/// The boot logo's 1 KB buffer exists only during this call, instead of in main's stack frame
/// for good (main never returns, and the pager's decoder needs that stack).
#[cfg(not(feature = "pager-only"))]
#[inline(never)]
fn show_boot_logo<D>(lcd: &mut D, storage: &mut Storage)
where
    D: embedded_graphics::draw_target::DrawTarget<Color = embedded_graphics::pixelcolor::BinaryColor>,
{
    let logo = storage.read_boot_logo();
    ui::boot::draw_logo(lcd, &logo);
}

#[inline(never)]
#[allow(clippy::too_many_arguments)]
fn boot_init_app<DI: display_interface::WriteOnlyDataCommand>(
    cp: &mut cortex_m::Peripherals,
    gpioa: &'static kd32f328_pac::gpioa::RegisterBlock,
    gpiob: &'static kd32f328_pac::gpiob::RegisterBlock,
    spi1: &'static kd32f328_pac::spi1::RegisterBlock,
    adc_regs: &'static kd32f328_pac::adc::RegisterBlock,
    mut keypad: Keypad,
    flashlight: Flashlight,
    display: &mut Display<'_, DI>,
    dbg: &mut uart::DebugUart<'_>,
) -> &'static mut app::App {
    // rfic
    let mut rfic = fd6818::Fd6818::new(gpiob);
    let chip_id = rfic.chip_id(&mut cp.SYST);
    dln!(dbg, "fd6818 chip id = {:#06x}", chip_id);
    rfic.init(&mut cp.SYST);
    dln!(dbg, "fd6818 init done");

    let flash_spi = spi::SpiBus::new(spi1, spi::ClockMode::Mode3, 4);
    let mut storage = Storage::new(norflash::NorFlash::new(flash_spi, gpioa));
    // text is drawn from the stock 5x7 font in the SPI flash (ui::glyph): is it there? (VOICE
    // MSG's first entry says, K6 FONT)
    ui::glyph::set_flash(&mut storage.norflash, true);

    // First-boot format gate
    if !storage.is_first_boot_done() {
        dln!(dbg, "first boot: awaiting MENU press or CPS dump");
        screen!(display, |lcd| ui::boot::draw_format_prompt(lcd));

        let mut cps_handshake = cps::HandshakeDetector::new();
        while !keypad.is_pressed(&mut cp.SYST, KeyId::Menu) {
            if cps_handshake.poll() {
                screen!(display, |lcd| ui::cps::draw_programming(lcd));
                cps::run_preboot_dump_session(&mut storage, &mut cp.SYST, dbg);
                screen!(display, |lcd| ui::boot::draw_format_prompt(lcd));
            }
            delay::ms(&mut cp.SYST, 10);
        }

        dln!(
            dbg,
            "formatting: settings/VFO/contacts/satellites/FM/logo/SSTV"
        );
        storage.first_boot_format();
        storage.mark_first_boot_done();
    }

    let cal_buf = storage.read_calibration();
    // (Debug formatting of slices costs 1 KB of flash the pager-only image hasn't got)
    #[cfg(not(feature = "pager-only"))]
    writeln!(dbg, "norflash cal block @0xf210: {:02x?}", cal_buf).ok();
    #[cfg(feature = "pager-only")]
    let _ = &cal_buf;

    let mut sql_flag = [0u8; 1];
    storage.read_raw(flash_map::addr::PA_TABLE_BASE_HIGH, &mut sql_flag);
    if sql_flag[0] != 0xFF {
        let sql_cal = storage.read_squelch_cal();
        #[cfg(not(feature = "pager-only"))]
        writeln!(
            dbg,
            "squelch cal @0xf0c0: th_in={:?} th_out={:?}",
            sql_cal.th_in, sql_cal.th_out
        )
        .ok();
        rfic.set_squelch_cal(sql_cal);
    } else {
        dln!(dbg, "squelch cal block blank, using placeholder table");
    }

    rfic.set_xtal_adjust(cal_buf[6]);
    dln!(dbg, "xtal_adjust = {}", cal_buf[6]);

    rfic.set_audio_calibration(cal_buf[0], cal_buf[1], cal_buf[2], cal_buf[3], cal_buf[4]);
    rfic.apply_af_calibration(
        &mut cp.SYST,
        cal_buf[11],
        cal_buf[12],
        cal_buf[13],
        cal_buf[14],
    );
    dln!(
        dbg,
        "audio cal: mic_depth={} cts_depth={} vol_wide={} vol_narrow={} af_rx=({},{}) af_tx=({},{})",
        cal_buf[0], cal_buf[1], cal_buf[3], cal_buf[4],
        cal_buf[11], cal_buf[12], cal_buf[13], cal_buf[14]
    );

    if let Some(target) = storage.read_pa_calibration(DEFAULT_FREQ_HZ, TxPower::Low) {
        rfic.set_pa_calibration(target);
    }
    dln!(dbg, "pa calibration loaded");

    let ani_raw = storage.read_ani_raw();
    let ani = AniConfig::from_raw(
        [ani_raw[0], ani_raw[1], ani_raw[2]],
        ani_raw[9],
        ani_raw[10],
    );

    // radio class
    let radio = Radio::new(
        rfic,
        gpioa,
        gpiob,
        adc_regs,
        ChannelConfig {
            freq_hz: DEFAULT_FREQ_HZ,
            tx_freq_hz: DEFAULT_FREQ_HZ,
            wide_band: true,
            power: TxPower::Low,
            subaudio_tx: SubAudio::None,
            subaudio_rx: SubAudio::None,
            modulation: Modulation::Fm,
        },
        ani,
    );

    // FM broadcast chip (RDA5807)
    let fm_radio = FmRadio::new(gpioa);

    if radio.ptt_asserted() || keypad.any_pressed(&mut cp.SYST) {
        dln!(dbg, "PTT/key held at boot, waiting for release");
        screen!(display, |lcd| ui::boot::draw_release_keys(lcd));
        let mut released_ticks = 0u32;
        while released_ticks < 50 {
            delay::ms(&mut cp.SYST, 10);
            if radio.ptt_asserted() || keypad.any_pressed(&mut cp.SYST) {
                released_ticks = 0;
            } else {
                released_ticks += 1;
            }
        }
    }

    // app class
    // The App (2.7 KB) lives in a static, built by a function of its own: in main's frame it
    // sat there for good, with the temporary it was built in, and the pager's decoder ran out
    // of stack on the radio.
    hal::ram0::bt(5, hal::ram0::sp(), unsafe { core::ptr::addr_of!(APP_SLOT) as u32 });
    let mut app: &'static mut app::App = place_app(
        unsafe { &mut *core::ptr::addr_of_mut!(APP_SLOT) },
        radio,
        chip_id,
        keypad,
        storage,
        flashlight,
        fm_radio,
        ChannelConfig {
            freq_hz: DEFAULT_FREQ_HZ,
            tx_freq_hz: DEFAULT_FREQ_HZ,
            wide_band: true,
            power: TxPower::Low,
            subaudio_tx: SubAudio::None,
            subaudio_rx: SubAudio::None,
            modulation: Modulation::Fm,
        },
        &mut cp.SYST,
    );
    hal::ram0::bt(6, hal::ram0::sp(), 0);
    app
}

#[entry]
fn main() -> ! {
    // boottrace: what the bootloader handed over, before anything here changes it
    #[cfg(feature = "boottrace")]
    let snap = hal::ram0::snap();
    #[cfg(feature = "boottrace")]
    hal::ram0::dog_start();
    // first: the core buffers in the bootloader's idle RAM start out zero, as in .bss
    hal::ram0::clear();
    #[cfg(feature = "explore")]
    app::pager::explore_init(); // its state is in hal::ram0 too
    let _dp = unsafe { Peripherals::steal() };

    let rcc = unsafe { &*kd32f328_pac::Rcc::ptr() };
    let gpioa = unsafe { &*kd32f328_pac::Gpioa::ptr() };
    let gpiob = unsafe { &*kd32f328_pac::Gpiob::ptr() };
    let gpioc = unsafe { &*kd32f328_pac::Gpioc::ptr() };
    let gpiof = unsafe { &*kd32f328_pac::Gpiof::ptr() };
    let flash = unsafe { &*kd32f328_pac::Flash::ptr() };
    let usart1 = unsafe { &*kd32f328_pac::Usart1::ptr() };
    let spi1 = unsafe { &*kd32f328_pac::Spi1::ptr() };
    let spi2 = unsafe { &*kd32f328_pac::Spi2::ptr() };
    let adc_regs = unsafe { &*kd32f328_pac::Adc::ptr() };

    clock::setup_pll(rcc, flash);
    clock::enable_peripheral_clocks(rcc);
    hal::uptime::init();

    // board pin init
    board::init_power_pins(gpioa);
    let power = Power::new(gpioa);
    power.latch_on();

    board::init_ptt_rxd_pin(gpioa);
    let flashlight = Flashlight::new(gpiob);
    board::init_debug_uart_tx_pin(gpioa);
    board::init_lcd_control_pins(gpiob, gpioc);
    board::init_lcd_spi_pins(gpiob);
    board::init_lcd_backlight_pin(gpiof);
    let backlight = Backlight::new(gpiof);
    backlight.on();
    board::init_fd6818_pins(gpiob);
    board::init_norflash_pins(gpioa);
    board::init_speaker_switch_pin(gpiob);
    board::init_rx_band_pins(gpioa);
    board::init_i2c_pins(gpioa);
    board::init_battery_adc_pin(gpioa);
    board::init_vox_adc_pin(gpioa);
    board::init_keypad_pins(gpiob, gpioc, gpiof);
    board::init_rx_led_pin(gpioa);
    // Constructed here (rather than right before its first use, as before)
    // so it's available for the first-boot format-confirmation gate below.
    let mut keypad = Keypad::new(gpiob, gpioc, gpiof);

    let mut dbg = uart::DebugUart::new(usart1, clock::SYSCLK_HZ, 115_200);
    // boottrace: 1 = past the RAM0 clear and the UART set-up (sp, SysTick CSR)
    hal::ram0::bt(1, hal::ram0::sp(), unsafe { core::ptr::read_volatile(0xE000_E010 as *const u32) });
    #[cfg(feature = "boottrace")]
    hal::ram0::snap_print(&snap);
    uart::install_bootloader_trampoline();
    hal::ram0::bt(2, unsafe { core::ptr::read_volatile(0x2000_1000 as *const u32) }, unsafe {
        core::ptr::read_volatile(0x2000_1004 as *const u32)
    });
    unsafe { cortex_m::peripheral::NVIC::unmask(kd32f328_pac::Interrupt::USART1) };
    dln!(dbg, "bfk6-fw boot, sysclk={}Hz", clock::SYSCLK_HZ);
    dln!(dbg, "K6 BOOT build={}", app::pager::BUILD);
    // what the bootloader left at the bottom of RAM (the live vector table, it seems): the
    // initial SP and the 15 system exception vectors, then SysTick and the first IRQs
    dln!(dbg, "K6 VEC");
    for i in 0..20u32 {
        let w = unsafe { core::ptr::read_volatile((0x2000_0000 + 4 * i) as *const u32) };
        dln!(dbg, " {:08x}", w);
    }
    dln!(dbg, "");
    // crash and stuck-key-up backstop: a vector table in SRAM at 0 (hal::txdog); its boot line
    // goes on the serial report from VOICE MSG's first entry
    #[cfg(not(feature = "lean"))]
    hal::txdog::install();
    hal::ram0::bt(3, hal::ram0::sp(), hal::ram0::used() as u32);

    let mut cp = cortex_m::Peripherals::take().unwrap();
    // The bootloader leaves SysTick's interrupt on (CSR 0x00010006, sotto's fdirq module,
    // 2026-11-09): every delay::us then ended in an exception through its table, and an FD6818
    // register read took 193 us instead of 98 (fdtick: 196 us a pair with it off)
    cp.SYST.disable_interrupt();
    unsafe { cp.NVIC.set_priority(kd32f328_pac::Interrupt::USART1, 0) };
    // Enabled this early (rather than just before the main loop, as before)
    // so UART RX -- hence CPS handshake detection -- already works during
    // the first-boot format-confirmation gate below, before `App` exists.
    // USART1 is the only interrupt configured at this point.
    unsafe { cortex_m::interrupt::enable() };
    hal::ram0::bt(4, hal::ram0::sp(), 0);

    // display
    let spi_bus = spi::SpiBus::new(spi2, spi::ClockMode::Mode3, 8);
    let cs_pin = ClosurePin(|level| board::set_lcd_cs(gpiob, level));
    let dc_pin = ClosurePin(|level| board::set_lcd_dc(gpioc, level));
    let mut rst_pin = ClosurePin(|level| board::set_lcd_reset(gpiob, level));

    let spi_device = embedded_hal_bus::spi::ExclusiveDevice::new_no_delay(spi_bus, cs_pin)
        .unwrap_or_else(|_| unreachable!());
    let interface = display_interface_spi::SPIInterface::new(spi_device, dc_pin);

    // the 1 KB page buffer: in main's frame for good, or (images that play voice from SPI
    // codebooks) in the PWM ring only while the screen is drawn, device::display
    #[cfg(not(shared_lcd))]
    let mut page_buffer: st7565::GraphicsPageBuffer<128, 8> = st7565::GraphicsPageBuffer::new();
    #[cfg(not(shared_lcd))]
    let page_buffer = &mut page_buffer;
    #[cfg(all(shared_lcd, not(banded)))]
    let page_buffer = device::display::shared_page_buffer();
    #[cfg(not(banded))]
    let mut lcd = st7565::ST7565::new(interface, display_spec::Sc5260Spec)
        .into_graphics_mode(page_buffer);
    // the `banded` image: no page buffer anywhere, the driver in raw mode (device::display)
    #[cfg(banded)]
    let mut lcd = st7565::ST7565::new(interface, display_spec::Sc5260Spec).into_raw_mode();

    {
        let mut syst_delay = SystDelay(&mut cp.SYST);
        lcd.reset(&mut rst_pin, &mut syst_delay).ok();
    }
    #[cfg(not(banded))]
    lcd.flush().ok();
    lcd.set_display_on(true).ok();
    let mut display = Display::new(lcd);
    #[cfg(banded)]
    display.draw_pages(|_, _| ()); // a blank screen
    ui::hook_mod_screen(&mut display); // module API level 6: a module's own screen
    dln!(dbg, "lcd init done");

    let mut app: &'static mut app::App = boot_init_app(
        &mut cp,
        gpioa,
        gpiob,
        spi1,
        adc_regs,
        keypad,
        flashlight,
        &mut display,
        &mut dbg,
    );

    ui::glyph::set_flash(&mut app.storage_mut().norflash, false); // the Storage moved into the App
    app.init_battery_samples();
    // Aura's boot screen (voltage, its "UV-K6x Aura" text or the stored logo), boot tune and
    // the 1 s hold after it: not in the pager-only images, which go straight to VOICE MSG.
    // Their ProFont 14 font (4 KB) went with them (ui::boot), 2026-11-10
    #[cfg(not(feature = "pager-only"))]
    {
        let boot_mode = app.settings().boot_display_mode;
        match boot_mode {
            1 => {
                screen!(display, |lcd| ui::boot::draw_voltage(lcd, app.battery_voltage_cv()));
            }
            2 => {
                screen!(display, |lcd| ui::boot::draw_message(lcd, app.settings()));
            }
            3 => {
                screen!(display, |lcd| show_boot_logo(lcd, app.storage_mut()));
            }
            _ => {}
        }
        if boot_mode != 0 && app.settings().boot_sound_enabled {
            let tune = app.settings().boot_tune;
            let aborted = app
                .radio_mut()
                .play_boot_tune(&mut cp.SYST, &tune, |syst| power.debounced_off(syst));
            if aborted {
                power.shutdown(&mut cp.SYST);
            }
        }
        if boot_mode != 0 {
            let mut remaining_ms: u32 = 1000;
            while remaining_ms > 0 {
                let slice = remaining_ms.min(30);
                delay::ms(&mut cp.SYST, slice);
                remaining_ms -= slice;
                if power.debounced_off(&mut cp.SYST) {
                    power.shutdown(&mut cp.SYST);
                }
            }
        }
    }

    app.radio_mut().enter_rx(&mut cp.SYST);

    dln!(
        dbg,
        "RX ON  @ {} Hz, watching RSSI...",
        app.master_freq_hz()
    );

    // Pager-only image (feature "pager-only"): VOICE MSG is the whole firmware. Nothing here
    // writes flash: no channel-state save at power-off.
    #[cfg(feature = "pager-only")]
    {
        app.enter_pager(&mut cp.SYST);
        #[cfg(feature = "explore")]
        app.explore_boot();
        // the backlight's idle timeout (hal::light, blowfang 2026-11-17): Aura's saved ABR delay
        hal::light::init(app.settings().backlight_time);
        let mut scheduler = scheduler::Scheduler::new();
        // blowfang's cable session (cps::run_cable_session): the PC's handshake on the cable,
        // in EXPLORE or the arena, never while keyed
        #[cfg(feature = "spilib")]
        let mut cable = cps::HandshakeDetector::cable();
        hal::ram0::bt(7, hal::ram0::sp(), 1);
        loop {
            let due = scheduler.tick();
            #[cfg(not(feature = "lean"))]
            hal::txdog::poll(); // TIM14's wraps while the backstop is at rest
            // the backlight: on while keyed, out once its idle time is up
            if app.is_transmitting() {
                hal::light::touch();
            }
            hal::light::poll();
            #[cfg(feature = "spilib")]
            if cable.poll() && !app.is_transmitting() {
                cps::run_cable_session(&mut app, &mut cp.SYST, &mut display);
            }
            // EXPLORE (app/pager/explore.rs): its sweep a block at a time, the keys between, the
            // screen when it asks; the pager's receiver is off meanwhile
            if app.exploring() {
                app.poll_pager_keys(&mut cp.SYST);
                if app.poll_explore(&mut cp.SYST) {
                    ui::draw_pager_only(&mut display, &app);
                }
                if power.debounced_off(&mut cp.SYST) {
                    app.radio_mut().rf_off(&mut cp.SYST);
                    power.shutdown(&mut cp.SYST);
                }
                continue;
            }
            app.poll_pager_keys(&mut cp.SYST);
            #[cfg(feature = "explore")]
            app.poll_explore_idle(&mut cp.SYST);
            app.poll_pager(&mut cp.SYST);
            app.service_pager_stream(&mut cp.SYST);
            if app.pager_play_pending() {
                ui::draw_pager_only(&mut display, &app);
                app.play_pager_message(&mut cp.SYST, &power);
                ui::draw_pager_only(&mut display, &app);
            }
            if app.pager_cal_pending() {
                ui::draw_pager_only(&mut display, &app);
                app.transmit_pager_cal(&mut cp.SYST);
            }
            app.handle_pager_ptt(&mut cp.SYST);
            if power.debounced_off(&mut cp.SYST) {
                app.radio_mut().rf_off(&mut cp.SYST);
                power.shutdown(&mut cp.SYST);
            }
            // no redraw while a message streams: one takes 30-50 ms, most of the PWM ring
            if due.every_50ms && !app.pager_streaming() {
                let t = hal::uptime::now();
                ui::draw_pager_only(&mut display, &app);
                app.note_pager_draw(hal::uptime::now().wrapping_sub(t));
            }
            // check the RF chip every 0.5 ms through the 10 ms pause: a burst's first sync
            // noticed late made its timed reads slip (the rest are caught in poll's own loop)
            for _ in 0..20 {
                app.poll_pager(&mut cp.SYST);
                if !app.service_pager_stream(&mut cp.SYST) {
                    delay::us(&mut cp.SYST, 500);
                }
            }
        }
    }

    #[cfg(not(feature = "pager-only"))]
    {
    // scheduler + main loop
    let mut scheduler = scheduler::Scheduler::new();
    let mut applied_contrast: u8 = u8::MAX;
    let mut backlight_on = true;
    let mut cps_handshake = cps::HandshakeDetector::new();
    hal::ram0::bt(7, hal::ram0::sp(), 2);

    loop {
        let due = scheduler.tick();
        #[cfg(not(feature = "lean"))]
        hal::txdog::poll(); // TIM14's wraps while the backstop is at rest

        if cps_handshake.poll() {
            cps::run_session(&mut app, &mut cp.SYST, &mut display, &mut dbg);
        }

        app.poll_keys(&mut cp.SYST);

        // APRS beacon TX: draw overlay before blocking transmission
        if app.aprs_beacon_active() {
            ui::draw(&mut display, &mut app);
            display.flush();
            app.transmit_pending_aprs_beacon(&mut cp.SYST);
        }

        if app.sstv_tx_pending() {
            ui::draw(&mut display, &mut app);
            display.flush();
            app.transmit_pending_sstv(&mut cp.SYST, &power);
        }

        // Voice pager: packets in, then the blocking playback / test TX
        app.poll_pager(&mut cp.SYST);
        app.service_pager_stream(&mut cp.SYST);
        if app.pager_play_pending() {
            ui::draw(&mut display, &mut app);
            app.play_pager_message(&mut cp.SYST, &power);
            ui::draw(&mut display, &mut app);
        }
        if app.pager_cal_pending() {
            ui::draw(&mut display, &mut app);
            app.transmit_pager_cal(&mut cp.SYST);
        }

        // power sw
        if power.debounced_off(&mut cp.SYST) {
            dln!(dbg, "power switch off, shutting down");
            app.radio_mut().rf_off(&mut cp.SYST);
            app.save_channel_state();
            power.shutdown(&mut cp.SYST);
        }

        // battery
        if due.every_500ms {
            app.poll_battery();
            dln!(dbg, "batt bars: {}", app.battery_bars());
        }

        // Wall clock tick + Doppler update
        if due.every_100ms && app.poll_wall_clock() {
            app.poll_doppler_update(&mut cp.SYST);
        }

        // PTT / CW key. CW bypasses the voice-PTT debouncer entirely
        // Satellite tracking has its own dedicated PTT path.
        if app.mode() == app::Mode::SatelliteTracking {
            let ptt_level = app.radio_mut().poll_ptt();
            let tracking_tx = app.is_transmitting();
            if let Some(level) = ptt_level {
                let pressed = !level;
                if pressed && !tracking_tx {
                    let ui = app.satellite_ui();
                    let rx_freq = ui.active_rx_freq_hz;
                    let tx_freq = ui.active_tx_freq_hz;
                    let doppler_rx = ui.current_doppler_hz;
                    // Scale Doppler from RX to TX
                    let doppler_tx =
                        satellite::tracking::scale_doppler_to_tx(doppler_rx, rx_freq, tx_freq);
                    let tone_hz = app.sats()[app.satellite_ui().detail_index]
                        .map(|s| s.tx_tone_hz)
                        .unwrap_or(0);
                    let subaudio = if tone_hz > 0 {
                        SubAudio::Ctcss(tone_hz)
                    } else {
                        SubAudio::None
                    };
                    let wide = ui.tracking_wide;
                    if satellite::tracking::tracking_ptt_press(
                        app.radio_mut(),
                        &mut cp.SYST,
                        tx_freq,
                        doppler_tx,
                        subaudio,
                        wide,
                    ) {
                        app.set_transmitting_for_tracking();
                        dln!(dbg, "TRACKING TX ON @ {} Hz", tx_freq);
                    }
                } else if !pressed && tracking_tx {
                    let ui = app.satellite_ui();
                    let rx_freq = ui.active_rx_freq_hz;
                    let doppler_rx = ui.current_doppler_hz;
                    let wide = ui.tracking_wide;
                    satellite::tracking::tracking_ptt_release(
                        app.radio_mut(),
                        &mut cp.SYST,
                        rx_freq,
                        doppler_rx,
                        wide,
                    );
                    app.clear_transmitting_for_tracking();
                    dln!(dbg, "TRACKING RX ON @ {} Hz", rx_freq);
                }
            }
        } else if app.mode() == app::Mode::Sstv {
            app.handle_sstv_ptt(&mut cp.SYST);
        } else if app.mode() == app::Mode::Pager {
            app.handle_pager_ptt(&mut cp.SYST);
        } else if matches!(
            app.side_modulation(app.master_index()),
            Modulation::Cw | Modulation::Cwf
        ) {
            app.poll_cw_key(&mut cp.SYST);
        } else if let Some(level) = app.radio_mut().poll_ptt() {
            app.set_ptt(&mut cp.SYST, !level);
            if app.is_transmitting() {
                dln!(dbg, "TX ON  @ {} Hz (low power)", app.master_freq_hz());
            } else {
                dln!(dbg, "RX ON  @ {} Hz", app.watching_freq_hz());
            }
        }

        if due.ticks_10ms > 0 {
            app.poll_tot(&mut cp.SYST, due.ticks_10ms);
        }
        app.poll_cw_hang(&mut cp.SYST);
        app.poll_backlight(due.ticks_10ms);
        app.poll_chanmgr_name_timeout();
        app.poll_aprs_call_timeout();
        app.poll_aprs_dev_name_timeout();
        app.poll_aprs_comment_timeout();
        app.poll_contacts_name_timeout();
        app.poll_satellite_name_timeout();
        app.poll_sstv_name_timeout();

        // Scan / Search / ScanQt / Fm / Spectrum (each self-guards on the
        // current mode)
        app.poll_search(&mut cp.SYST);
        app.poll_scanqt(&mut cp.SYST);
        app.poll_fm(&mut cp.SYST);
        app.poll_spectrum(&mut cp.SYST);
        if due.every_50ms {
            app.poll_scan(&mut cp.SYST);
        }

        // VOX
        let mic_level = app.radio_mut().read_mic_level();
        app.set_mic_level(mic_level);
        let audio_open = app.radio_mut().audio_is_open();
        app.poll_vox(&mut cp.SYST, mic_level, audio_open);

        app.poll_auto_lock(&mut cp.SYST, audio_open, due.ticks_10ms);

        // LCD contrast
        if due.every_50ms && applied_contrast != app.contrast() {
            display.set_contrast(app.contrast());
            applied_contrast = app.contrast();
        }

        // Backlight auto-off
        if due.every_50ms {
            let want_on = app.backlight_should_be_on();
            if want_on != backlight_on {
                if want_on {
                    backlight.on();
                } else {
                    backlight.off();
                }
                backlight_on = want_on;
            }
        }

        // Spectrum and pager modes drive the FD6818 directly and would fight
        // with this normal-RX housekeeping
        if !app.is_transmitting()
            && !app.cw_key_down()
            && app.mode() != app::Mode::Spectrum
            && app.mode() != app::Mode::Pager
        {
            if !app.power_save_is_asleep() {
                app.poll_squelch(&mut cp.SYST, 3);
                app.poll_dual_standby(&mut cp.SYST, app.rssi_open());
                app.poll_dtmf(&mut cp.SYST);
                if due.ticks_10ms > 0 {
                    app.poll_no_channels_notice(due.ticks_10ms);
                }
            }
            app.poll_power_save(&mut cp.SYST);
        }

        // Screen: redraw on every_50ms regardless of TX/RX
        if due.every_50ms {
            if !app.is_transmitting() && !app.power_save_is_asleep() {
                let rssi = app.radio_mut().rssi(&mut cp.SYST);
                app.set_rssi_raw(rssi as u8);
                dln!(
                    dbg,
                    "RSSI: {} audio_open={} freq={} ch_mode={} ch_num={}",
                    rssi,
                    audio_open,
                    app.watching_freq_hz(),
                    app.watching_is_channel_mode(),
                    app.watching_channel_num()
                );
            }

            app.poll_blink();
            ui::draw(&mut display, &mut app);
        }
        delay::ms(&mut cp.SYST, 10);
    }
    }
}
