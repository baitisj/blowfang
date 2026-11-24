//! UV-21 SURF, the port's main screen (Jeff, 2026-11-24): radios/uv21/surf/uv21_surf.c (MIT C,
//! built by build.rs) runs its own loop and reaches the radio through `UvApi`, filled here. The
//! main loop calls `run` at boot, and again if it ever returns: SURF is the only screen (port 14,
//! no VFO).

use crate::app::App;
use crate::board;
use at32f421_pac as pac;
use crate::device::display::Display;
use crate::device::keypad::{KeyEventKind, KeyId};
use crate::hal::{con, delay, uptime};
use crate::ui::font55;
use cortex_m::peripheral::SYST;
use embedded_graphics::mono_font::{ascii::{FONT_10X20, FONT_6X10}, MonoTextStyleBuilder};
use embedded_graphics::pixelcolor::{raw::RawU16, Rgb565};
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::Rectangle;
use embedded_graphics::text::{Baseline, Text};

/// radios/uv21/surf/uv21_surf.h's uv_api_t
#[repr(C)]
struct UvApi {
    fd_read: extern "C" fn(u8) -> u16,
    fd_write: extern "C" fn(u8, u16),
    tune: extern "C" fn(u32, i32),
    delay_us: extern "C" fn(u32),
    ms: extern "C" fn() -> u32,
    key: extern "C" fn() -> i32,
    speaker: extern "C" fn(i32),
    say: extern "C" fn(*const u8),
    fill: extern "C" fn(i32, i32, i32, i32, u16),
    text: extern "C" fn(i32, i32, *const u8, u16, u16, i32) -> i32,
    poll: extern "C" fn() -> i32,
    wake: extern "C" fn(u32, i32, i32),
    front_end: extern "C" fn(i32) -> i32,
    wake_hz: u32,
    start_hz: u32,
}

extern "C" {
    fn uv21_surf(api: *mut UvApi, state: *mut u8, room: u32) -> u32;
    fn uv21_surf_state_size() -> u32;
}

/// The CW wake's frequency (Jeff, 2026-11-24: "at this point on 440.850"); a long # in SURF
/// moves it, until the radio is switched off
const WAKE_HZ: u32 = 440_850_000;

/// SURF's state (uv21_surf_state_size(), 1,272 B in port 17) and its remembered peaks
/// (NPM = 128 x 8 B), 8-aligned, with a word to spare. Port 17 trimmed it from 480 words
/// (3,840 B; 1,544 B never used); the start line says `surf ram need= room=`, and
/// tests/test_uv21_surf.py fails if the room no longer holds all 128 peaks.
const STATE_WORDS: usize = 288;
const NPM: u32 = 128;
static mut STATE: [u64; STATE_WORDS] = [0; STATE_WORDS];

/// What the callbacks reach while SURF runs: set by `run`, cleared after
struct Ctx {
    app: *mut App<'static>,
    syst: *mut SYST,
    display: *mut Display<'static>,
    t_house: u32,
    t_alive: u32,
}

static mut CTX: Option<Ctx> = None;
static mut WAKE: u32 = WAKE_HZ;

fn ctx() -> &'static mut Ctx {
    unsafe { (*core::ptr::addr_of_mut!(CTX)).as_mut().unwrap() }
}
fn app() -> &'static mut App<'static> {
    unsafe { &mut *ctx().app }
}
fn syst() -> &'static mut SYST {
    unsafe { &mut *ctx().syst }
}
fn display() -> &'static mut Display<'static> {
    unsafe { &mut *ctx().display }
}

extern "C" fn fd_read(reg: u8) -> u16 {
    let s = syst();
    app().radio_mut().fd6818.reg_read(s, reg)
}

extern "C" fn fd_write(reg: u8, v: u16) {
    let s = syst();
    app().radio_mut().fd6818.reg_write(s, reg, v);
}

extern "C" fn tune(hz: u32, wide: i32) {
    let s = syst();
    app().radio_mut().retune_rx(s, hz, wide != 0);
}

/// board/rxpath.rs: the receive band switch's mode, stepped by `step`; the next tune applies it
extern "C" fn front_end(step: i32) -> i32 {
    let m = board::rx_path_mode();
    if step != 0 {
        let n = ((m as i32 + step).rem_euclid(board::RX_PATH_MODES as i32)) as u8;
        board::set_rx_path_mode(n);
        let s = syst();
        app().radio_mut().fd6818.set_rf_gpio56(s, board::rx_path_rf_gpio56(n));
        return n as i32;
    }
    m as i32
}

extern "C" fn delay_us(us: u32) {
    delay::us(syst(), us);
}

extern "C" fn ms() -> u32 {
    uptime::now32() / 10
}

/// uv21_surf.h's key codes; UP and DOWN on the press and its repeats (their release is no key)
fn code(key: KeyId) -> i32 {
    (match key {
        KeyId::Digit0 => b'0',
        KeyId::Digit1 => b'1',
        KeyId::Digit2 => b'2',
        KeyId::Digit3 => b'3',
        KeyId::Digit4 => b'4',
        KeyId::Digit5 => b'5',
        KeyId::Digit6 => b'6',
        KeyId::Digit7 => b'7',
        KeyId::Digit8 => b'8',
        KeyId::Digit9 => b'9',
        KeyId::Asterisk => b'*',
        KeyId::Pound => b'#',
        KeyId::Menu => b'M',
        KeyId::Exit => b'X',
        KeyId::Up => b'U',
        KeyId::Down => b'D',
        KeyId::Vm => b'V',
        KeyId::Ab => b'W',
        KeyId::Side1 => b'A', // top side key: the next peak up
        KeyId::Side2 => b'N', // middle: the next band
        KeyId::Band => b'B',  // bottom: the next peak down
    }) as i32
}

extern "C" fn key() -> i32 {
    let s = syst();
    while let Some(ev) = app().surf_key_event(s) {
        let ud = matches!(ev.key, KeyId::Up | KeyId::Down);
        match ev.kind {
            KeyEventKind::Press | KeyEventKind::Repeat if ud => return code(ev.key),
            KeyEventKind::Single if !ud => return code(ev.key),
            KeyEventKind::Long if !ud => return code(ev.key) | 0x80,
            _ => {}
        }
    }
    0
}

extern "C" fn speaker(on: i32) {
    app().radio_mut().set_speaker(on != 0);
}

/// a C string, at most 64 bytes of it
fn cstr<'a>(p: *const u8) -> &'a str {
    let mut n = 0;
    unsafe {
        while n < 64 && *p.add(n) != 0 {
            n += 1;
        }
        core::str::from_utf8(core::slice::from_raw_parts(p, n)).unwrap_or("?")
    }
}

extern "C" fn say(line: *const u8) {
    con::puts(cstr(line));
    con::nl();
}

fn rgb(c: u16) -> Rgb565 {
    Rgb565::from(RawU16::new(c))
}

extern "C" fn fill(x: i32, y: i32, w: i32, h: i32, c: u16) {
    if w <= 0 || h <= 0 {
        return;
    }
    let r = Rectangle::new(Point::new(x, y), Size::new(w as u32, h as u32));
    display().as_draw_target().fill_solid(&r, rgb(c)).ok();
}

extern "C" fn text(x: i32, y: i32, s: *const u8, fg: u16, bg: u16, font: i32) -> i32 {
    let s = cstr(s);
    let lcd = display().as_draw_target();
    if font == 0 {
        return font55::draw(lcd, x, y, s, rgb(fg), rgb(bg));
    }
    let style = MonoTextStyleBuilder::new()
        .font(if font == 2 { &FONT_10X20 } else { &FONT_6X10 })
        .text_color(rgb(fg))
        .background_color(rgb(bg))
        .build();
    Text::with_baseline(s, Point::new(x, y), style, Baseline::Top)
        .draw(lcd)
        .map(|p| p.x - x)
        .unwrap_or(0)
}

/// what a RAM module reaches (umod.rs): SURF's own callbacks, so a module runs inside SURF's
/// housekeeping with its context set
static UM_API: crate::umod::UmApi = crate::umod::UmApi {
    abi: crate::umod::ABI as u32,
    fd_read,
    fd_write,
    tune,
    delay_us,
    ms,
    say,
    front_end,
};

/// the main loop's housekeeping while SURF runs: the power switch (off for 200 ms straight, as
/// main.rs's check_power_off), and every 50 ms the battery and the backlight; the alive line
/// every 10 s, so the bench's logger sees the image running
extern "C" fn poll() -> i32 {
    let dp = unsafe { pac::Peripherals::steal() };
    let gpiob = &dp.gpiob;
    if board::read_power_key(gpiob) {
        for _ in 0..10 {
            delay::ms(syst(), 20);
            if !board::read_power_key(gpiob) {
                return 0;
            }
        }
        let s = syst();
        app().radio_mut().rf_off(s);
        board::set_power_latch(&dp.gpiof, false);
        loop {
            cortex_m::asm::wfi();
        }
    }
    crate::umod::service(&UM_API); // a module upload on the serial line (umod.rs)
    let now = ms();
    let c = ctx();
    if now.wrapping_sub(c.t_house) >= 50 {
        c.t_house = now;
        let a = app();
        a.poll_battery();
        a.poll_backlight();
        board::set_lcd_backlight(&dp.gpioa, a.backlight_should_be_on());
    }
    if now.wrapping_sub(c.t_alive) >= 10_000 {
        c.t_alive = now;
        con::puts("alive surf");
        con::kv_hex("s", now / 1000, 4);
        con::nl();
    }
    0
}

extern "C" fn wake(hz: u32, dbm: i32, floor: i32) {
    // nothing to wake into yet (the K6 opens its module receiver; the UV-21 has no antenna and
    // no module loader): the line for the bench, and SURF shows WAKE HEARD
    con::puts("uv21 wake");
    con::kv_hex("hz", hz, 8);
    con::kv_hex("dbm", dbm as u32 & 0xFFFF, 4);
    con::kv_hex("floor", floor as u32 & 0xFFFF, 4);
    con::nl();
}

/// SURF until its housekeeping asks to leave; returns its last listening frequency (0 if it
/// couldn't run)
pub fn run(app: &mut App<'_>, syst: &mut SYST, display: &mut Display<'_>, start_hz: u32) -> u32 {
    let now = ms();
    unsafe {
        CTX = Some(Ctx {
            app: app as *mut App<'_> as *mut App<'static>,
            syst,
            display: display as *mut Display<'_> as *mut Display<'static>,
            t_house: now,
            t_alive: now,
        });
    }
    let mut api = UvApi {
        fd_read,
        fd_write,
        tune,
        delay_us,
        ms,
        key,
        speaker,
        say,
        fill,
        text,
        poll,
        wake,
        front_end,
        wake_hz: unsafe { WAKE },
        start_hz,
    };
    let room = (STATE_WORDS * 8) as u32;
    let need = unsafe { uv21_surf_state_size() };
    con::puts("surf ram");
    con::kv_hex("need", need + 8 * NPM, 4);
    con::kv_hex("room", room, 4);
    con::nl();
    let cen = if need + 8 * 16 > room {
        con::puts("surf no room");
        con::kv_hex("need", need, 4);
        con::nl();
        0
    } else {
        unsafe { uv21_surf(&mut api, core::ptr::addr_of_mut!(STATE) as *mut u8, room) }
    };
    unsafe {
        WAKE = api.wake_hz;
        CTX = None;
    }
    cen
}
