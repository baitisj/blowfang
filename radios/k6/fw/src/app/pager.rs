//! VOICE MSG: the sotto voice pager, receive side.
//!
//! A sotto voice message (2400 bit/s speech, 6 bytes per 20 ms) arrives as
//! FSK packets through the FD6818's own packet modem, is kept in RAM only (no
//! flash writes, ever), and plays through the MCU's PWM pin, the stock voice
//! prompts' path. Packet format: k6-fsk-packet-spec.md, version 1 (profile L,
//! 64-byte packets; profile S, 16 bytes). Packet checking, reassembly and the
//! decoder are sotto's C library (libsotto_k6.a, MIT, github.com/baitisj/sotto
//! c/k6/); this file is the radio side: FSK registers, keys, PWM playback, and
//! the calibration transmit.
//!
//! Keys: MENU play (again), EXIT leave (or stop playback), UP/DOWN volume, # frequency,
//! 4 pre-emphasis, 9 auto-play, 0 stay in VOICE MSG until EXIT (explore image: no fallback to EXPLORE), 6 raw packet bytes on the screen (hiss image: the hiss band off and on, an A/B on the
//! speaker), * clear the message, side key 1 run the last module again, PTT transmit the
//! calibration packet (twice). PHY F (FFSK 1200/1800 at 1800 baud) is the default and what the
//! K6 idles on; the old direct-FSK PHY D and its key 1 went because one stray press left the
//! radio deaf to the bench's F sends (Jeff, 2026-11-10), and the receive settings keys 2, 3, 5,
//! 7, 8 and 0 went the same way after a stray 7 broke an upload (2026-11-13). Control packets
//! still change frames and baud. D comes back at 2400 baud only, as the fast rung of a rate
//! ladder (c/k6/k6_rate.h, docs/k6-fsk24.md; Jeff 2026-11-16): a sender with a flat transmit
//! path asks for it per session (phy=D baud=2400), and the K6 drops back to F/1800 by itself
//! on CRC failures or silence, so it can never be left deaf.
//!
//! Serial report: every packet, setting change, playback and calibration
//! transmit also goes out as one text line on the programming cable (USART1
//! TX, 115200 8N1), for sotto's PC harness (`python -m modem.k6_harness`).
//! The radio never reads the cable: its other wire is the PTT line.

use super::{App, ChVfoMode, Mode, VFO_INPUT_DIGITS};
use crate::device::keypad::{KeyEvent, KeyEventKind, KeyId};
use crate::device::power::Power as PowerSwitch;
use crate::device::radio::{BandLock, Modulation, SubAudio};
use crate::drivers::fd6818::AfOutState;
use crate::hal::pwm_audio::{self, PwmAudio};
use crate::hal::{arena, delay, uptime};
use core::ptr::{addr_of, addr_of_mut, read_volatile, write_volatile};
use cortex_m::peripheral::SYST;

/// RAM for the message: 6.2 bytes per 20 ms frame, so 4 KB holds 640 frames
/// (12.8 s). Raise it if the RAM report on the pager screen ("FREE") shows
/// room; every 1 KB is 3.2 s more.
/// The firmware build: the sotto commit and UTC build time from sotto's
/// firmware/k6-aura/build.sh (SOTTO_BUILD), on the serial report at power-on
/// (`K6 BOOT`) and in every `K6 CFG` line, and its commit on the VOICE MSG
/// screen, so a test always knows which image it ran on.
pub const BUILD: &str = match option_env!("SOTTO_BUILD") {
    Some(b) => b,
    None => "local",
};

/// The commit part of `BUILD`, for the screen.
/// Hiss image: whether the hiss band is in the playback (key 6 takes it out and puts it back).
#[cfg(feature = "hiss")]
pub fn hiss_band_on() -> bool {
    unsafe { ffi::sp_hiss() != 0 }
}

pub fn build_short() -> &'static str {
    BUILD.get(..7).unwrap_or(BUILD)
}

/// The arena screen's third line (the release plan, Jeff 2026-11-13), only while it's busy
#[cfg(feature = "explore")]
pub enum Busy {
    Idle,
    /// a module coming in: chunks held, of
    Module(u8, u8),
    Playing,
    /// EXPLORE's code pages aren't installed (after a flash): the arena holds
    PagesMissing,
}

/// Key 0 turned the arena's fallback to EXPLORE off (it stays in VOICE MSG until EXIT)
#[cfg(feature = "explore")]
pub fn arena_stay() -> bool {
    explore::stay()
}

#[cfg(feature = "explore")]
pub fn arena_busy(app: &App) -> Busy {
    let st = unsafe { &*ffi::km_status() };
    if st.active != 0 {
        Busy::Module(st.have, st.count)
    } else if stream_on() || app.pager.play_pending {
        Busy::Playing
    } else if explore::pages_missing() {
        Busy::PagesMissing
    } else {
        Busy::Idle
    }
}

/// EXPLORE, the signal explorer's list of likely repeaters (pager/explore.rs): the explore
/// image (feature "explore": pager-only, no voice decoder) starts in it; EXIT from VOICE MSG
/// goes back
#[cfg(feature = "explore")]
mod explore;

#[cfg(feature = "explore")]
pub fn explore_on() -> bool {
    explore::on()
}
/// main, right after hal::ram0::clear: EXPLORE's state (in hal::ram0) as it starts
#[cfg(feature = "explore")]
pub fn explore_init() {
    explore::init()
}
#[cfg(feature = "explore")]
pub fn explore_boot() {
    explore::boot()
}
#[cfg(feature = "explore")]
pub fn explore_voice_poll(app: &mut App, syst: &mut SYST) {
    explore::voice_poll(app, syst)
}
#[cfg(not(feature = "explore"))]
pub fn explore_on() -> bool {
    false
}
/// EXPLORE's turn in the main loop: true when the screen wants drawing
#[cfg(feature = "explore")]
pub fn explore_poll(app: &mut App, syst: &mut SYST) -> bool {
    explore::poll(app, syst)
}
#[cfg(not(feature = "explore"))]
pub fn explore_poll(_app: &mut App, _syst: &mut SYST) -> bool {
    false
}
#[cfg(feature = "explore")]
pub fn explore_draw<D>(lcd: &mut D, app: &App)
where
    D: embedded_graphics::prelude::DrawTarget<Color = embedded_graphics::pixelcolor::BinaryColor>,
{
    explore::draw(lcd, app)
}
#[cfg(not(feature = "explore"))]
pub fn explore_draw<D>(_lcd: &mut D, _app: &App)
where
    D: embedded_graphics::prelude::DrawTarget<Color = embedded_graphics::pixelcolor::BinaryColor>,
{
}

/// 4 KB in every image since the RAM above Aura's old region was found (2026-11-07, memory.x);
/// the hiss band image had 2.5 KB before that (413 slots: two XL groups at depth 8, so sotto
/// 1200 messages over 13 s streamed only at depth 4). The first part of the shared RAM block
/// (hal::arena), which RAM modules take whole while nothing plays.
// (The RAM below UserVectors, 0x2000_0000-0x2000_0FFF, is not free: images that put the store
// there, even from 0x2000_0100, hung at boot on the radio on 2026-11-04.)
pub(super) const STORE_BYTES: usize = arena::STORE_BYTES;

/// The FD6818 (BK4819 family) needs CHIPID 0x4819; the FD6818B (0x4829) has
/// other registers.
const FD6818_CHIP_ID: u16 = 0x4819;

/// Pager-only image: the frequency VOICE MSG starts on at power-on, instead of the VFO stock
/// Aura saved: 440.850 MHz, in WWARA's packet simplex sub-band (440.800-440.900). `#` still
/// retunes in RAM.
#[cfg(feature = "pager-only")]
const DEFAULT_FREQ_HZ: u32 = 440_850_000;

mod ffi {
    #[repr(C)]
    #[derive(Clone, Copy)]
    pub struct SpStatus {
        pub have: i16,
        pub msg_id: u8,
        pub profile: u8,
        pub total: u16,
        pub stored: u16,
        pub frames_got: u16,
        pub packets_got: u16,
        pub packets_total: u16,
        pub good_packets: u16,
        pub bad_packets: u16,
        pub complete: i16,
    }
    pub const EMPTY: SpStatus = SpStatus {
        have: 0,
        msg_id: 0,
        profile: 0,
        total: 0,
        stored: 0,
        frames_got: 0,
        packets_got: 0,
        packets_total: 0,
        good_packets: 0,
        bad_packets: 0,
        complete: 0,
    };
    /// sp_test_errors' result (sotto_pager.h)
    #[repr(C)]
    #[derive(Clone, Copy)]
    pub struct SpBiterr {
        pub errors: i16,
        pub seq: i16,
        pub swap: u8,
        pub invert: u8,
        pub block: [u8; 8],
    }
    pub const NO_BITERR: SpBiterr = SpBiterr { errors: -1, seq: 0, swap: 0, invert: 0, block: [0; 8] };
    extern "C" {
        pub fn sp_init(mem: *mut u8, bytes: u32) -> i32;
        pub fn sp_put(mem: *mut u8, bytes: u32, sf: *const u8, n: u32, magic: i32) -> i32;
        #[cfg(not(feature = "lean"))]
        pub fn sp_dec_pool(mem: *mut u8, bytes: u32);
        #[cfg(not(feature = "lean"))]
        pub fn sp_dec_need() -> u32;
        pub fn sp_packet(payload: *const u8, len: i32) -> i32;
        pub fn sp_check(payload: *const u8, len: i32) -> i32;
        pub fn sp_test_errors(payload: *const u8, len: i32, e: *mut SpBiterr) -> i32;
        pub fn sp_last_packet() -> *const u8;
        pub fn sp_status(st: *mut SpStatus);
        pub fn sp_missing(out: *mut u16, max: i32) -> i32;
        pub fn sp_rebuilt() -> i32;
        pub fn sp_play_start();
        pub fn sp_play_pwm(out: *mut u16, top: u32, gain_q8: i32, emphasis: i32) -> i32;
        pub fn sp_frame_samples() -> i32;
        pub fn sp_play_pos() -> i32;
        pub fn sp_pcm_hash(samples: *mut u32) -> u32;
        pub fn sp_set_stream(on: i32);
        pub fn sp_stream() -> i32;
        #[cfg(feature = "hiss")]
        pub fn sp_hiss() -> i32;
        #[cfg(feature = "hiss")]
        pub fn sp_hiss_on(on: i32);
        pub fn sp_stream_ready() -> i32;
        pub fn sp_stream_end();
        pub fn sp_stream_info(overruns: *mut i32) -> i32;
        pub fn sp_set_yield(f: Option<unsafe extern "C" fn()>);
        pub fn sp_auto_gain_q8() -> i32;
        pub fn sp_test_packet(out: *mut u8);
        pub fn sp_control(payload: *const u8, len: i32, rec: *mut u8) -> i32;
        pub fn km_packet(payload: *const u8, len: i32, arena: *mut u8, bytes: u32) -> i32;
        pub fn km_run(arena: *mut u8, api: *const K6Api) -> i32;
        pub fn km_reset();
        pub fn km_again(a: *const u8, cap: u32) -> i32;
        pub fn km_status() -> *const KmStatus;
        #[cfg(feature = "spilib")]
        pub fn kl_install(img: *const u8, fl: *const KlFlash) -> i32;
        #[cfg(feature = "spilib")]
        pub fn kl_load(slot: i32, fl: *const KlFlash, dst: *mut u8, cap: u32) -> i32;
        #[cfg(feature = "spilib")]
        pub fn kl_info(slot: i32, fl: *const KlFlash, out: *mut KlInfo) -> i32;
        #[cfg(feature = "voice-explore")]
        pub fn kl_page(slot: i32, fl: *const KlFlash, dst: *mut u8, cap: u32) -> i32;
        #[cfg(any(banded, feature = "voice-explore"))]
        pub fn lz4d(src: *const u8, n: u32, dst: *mut u8, cap: u32) -> i32;
        pub fn sp_cb_setup() -> i32;
        pub fn sp_cb_mode() -> i32;
        pub fn sp_cb_bench(n: u32) -> u32;
        pub fn sp_cb_expected() -> u32;
        pub fn sp_cb_reads() -> u32;
        pub fn km_report_status(out: *mut u8, seq: u8, parts: u8, state: u8, call: *const u8, ret: i32, ms: u32, stack_free: u16);
        pub fn km_report_text(out: *mut u8, seq: u8, part: u8, parts: u8, text: *const u8, n: u32);
        pub fn sp_pn9() -> *const u8;
        pub fn sp_crc16(p: *const u8, n: i32) -> u32;
        pub fn kr_control(cur: i32, mask: u32, val: u32) -> i32;
        pub fn kr_enter();
        pub fn kr_packet(ok: i32) -> i32;
        pub fn kr_tick(dt: u32) -> i32;
        pub fn kr_take_why() -> i32;
        pub fn kr_link(ok: i32, rssi_dbm: i32);
        pub fn kr_rssi_median() -> i32;
        pub fn kr_link_reset();
        pub fn kr_state() -> *const KrState;
        pub fn sp_vlr_setup() -> i32;
        pub fn sp_vlr_expected() -> u32;
        #[cfg(not(feature = "lean"))]
        pub fn ifp_session(api: *const K6Api, ring: *mut u8, wait_ms: u32, max_ms: u32, r23: i32, res: *mut IfpResult) -> i32;
        pub fn ifp_session_in(api: *const K6Api, ring: *mut u8, wait_ms: u32, max_ms: u32, r23: i32, res: *mut IfpResult, work: *mut u8) -> i32;
        pub fn ifp_work_bytes() -> u32;
    }
    /// c/k6/k6_rate.h kr_t: the rate ladder's state and the link's numbers
    #[repr(C)]
    pub struct KrState {
        pub hist: u8,
        pub n: u8,
        pub good: u8,
        pub why: u8,
        pub quiet: u32,
        pub syncs: u16,
        pub bad: u16,
        pub rssi_min: i16,
        pub bins: [u8; 32],
    }
    /// c/k6/ifpager.h: a coded 4FSK receive session's results
    #[repr(C)]
    pub struct IfpResult {
        pub ret: i32,
        pub ms: u32,
        pub syncs: u32,
        pub syms: u32,
        pub lost: u32,
        pub acq_q: i32,
        pub acq_rej: i32,
        pub afc_hz: i32,
        pub idle_pct: i32,
        pub ks: i32,
        pub nres: i32,
        pub res: [i8; 64],
        pub miss: [i8; 64],
        pub ram: i32,
        pub gap_us: i32,
        pub gap_i: i32,
        pub lag: i32,
        pub lead_ser: i32,
    }
    /// c/k6/k6_mod.h: what a RAM module can call (append-only, ABI 1)
    #[repr(C)]
    pub struct K6Api {
        pub abi: u16,
        pub level: u16,
        pub say: unsafe extern "C" fn(*const u8),
        pub ticks: unsafe extern "C" fn() -> u32,
        // level 2: receive only
        pub fd_read: unsafe extern "C" fn(u8) -> u16,
        pub fd_write: unsafe extern "C" fn(u8, u16) -> i32,
        pub delay_us: unsafe extern "C" fn(u32),
        pub tune: unsafe extern "C" fn(u32, i32),
        pub rx_hz: unsafe extern "C" fn() -> u32,
        pub ms: unsafe extern "C" fn() -> u32,
        pub key_down: unsafe extern "C" fn() -> i32,
        pub scratch: unsafe extern "C" fn(*mut u32) -> *mut u8,
        // level 3: transmit (Jeff OK'd a module TX API, 2026-11-06)
        pub tx_on: unsafe extern "C" fn(u32) -> i32,
        pub tx_off: unsafe extern "C" fn(),
        pub tx_pa: unsafe extern "C" fn(i32) -> i32,
        // level 4: a cheap key check, the calibration record
        pub key_poll: unsafe extern "C" fn() -> i32,
        pub cal_read: unsafe extern "C" fn(*mut u8, u32) -> i32,
        pub cal_write: unsafe extern "C" fn(*const u8, u32) -> i32,
        // level 5: the SPI flash, for the repeater table (program and erase in its 64 KB only)
        pub fl_read: unsafe extern "C" fn(u32, *mut u8, u32) -> i32,
        pub fl_prog: unsafe extern "C" fn(u32, *const u8, u32) -> i32,
        pub fl_erase: unsafe extern "C" fn(u32) -> i32,
        // level 6: the screen, for a module's own display (text lines, bars, a waterfall)
        pub lcd_text: unsafe extern "C" fn(i32, *const u8),
        pub lcd_bar: unsafe extern "C" fn(i32, u32, u32),
        pub lcd_wf: unsafe extern "C" fn(*const u8, i32, i32),
        pub lcd_flush: unsafe extern "C" fn() -> i32,
        // level 7: the green RX LED (PA3)
        pub led: unsafe extern "C" fn(i32),
        // level 8: the radio's UTC clock
        pub clock_set: unsafe extern "C" fn(u32, u32, u32) -> i32,
        pub clock_get: unsafe extern "C" fn(*mut u32, *mut u32, *mut u32) -> i32,
        // level 9: a module's own glyphs on its screen (bytes 0x80 + k of a text line)
        pub lcd_glyphs: unsafe extern "C" fn(*const u16, i32),
        // level 10: the radio's settings (the SETTINGS module): the backlight's idle time
        pub setting: unsafe extern "C" fn(i32, i32, i32) -> i32,
        // level 11: a sotto receiver's message played once it returns, and the buffer that
        // holds it outside the arena
        pub sotto_play: unsafe extern "C" fn(*const u8, u32, u32, u32, *const u8) -> i32,
        pub msg_mem: unsafe extern "C" fn(*mut u32) -> *mut u8,
        // level 15: a module's help cards (and packed images, k6_lib.h KM_F_LZ4)
        pub help: unsafe extern "C" fn(*const u8) -> i32,
        // level 17: the screen tap (a hook that sees each page sent) and a redraw of the core's screen
        pub screen_tap: unsafe extern "C" fn(Option<crate::device::display::Hook>, *mut u8) -> i32,
        pub redraw: unsafe extern "C" fn() -> i32,
    }
    /// c/k6/k6_lib.h kl_flash_t and kl_info_t
    #[repr(C)]
    pub struct KlFlash {
        pub read: unsafe extern "C" fn(u32, *mut u8, u32) -> i32,
        pub prog: unsafe extern "C" fn(u32, *const u8, u32) -> i32,
        pub erase: unsafe extern "C" fn(u32) -> i32,
    }
    #[repr(C)]
    #[derive(Default)]
    pub struct KlInfo {
        pub code_len: u16,
        pub entry: u16,
        pub serial: u32,
        pub level: u8,
        pub flags: u8,
        pub name: [u8; 12],
        /// level 15: the packed body's bytes in the slot (0: not packed)
        pub packed: u16,
    }
    #[repr(C)]
    pub struct KmStatus {
        pub xfer: u8,
        pub have: u8,
        pub count: u8,
        pub active: u8,
        pub last: i8,
        pub chunk: u8,
        pub code_len: u16,
        pub serial: u32,
        pub reply: u8,
        pub tlm: u8,
        pub missing: u8,
        pub pad: u8,
    }
}
pub use ffi::{SpBiterr, SpStatus};

const KM_READY: i32 = 3;

/// The table a RAM module gets (c/k6/k6_mod.h): the serial report, (level 2) the RF chip
/// for receiving, and (level 3) a guarded transmitter. Level 2 and 3 calls reach the app
/// through MOD_CTX, set only while a module runs.
static MOD_API: ffi::K6Api = ffi::K6Api {
    abi: 1,
    level: 17,
    say: mod_say,
    ticks: mod_ticks,
    fd_read: mod_fd_read,
    fd_write: mod_fd_write,
    delay_us: mod_delay_us,
    tune: mod_tune,
    rx_hz: mod_rx_hz,
    ms: mod_ms,
    key_down: mod_key_down,
    scratch: mod_scratch,
    tx_on: mod_tx_on,
    tx_off: mod_tx_off,
    tx_pa: mod_tx_pa,
    key_poll: mod_key_poll,
    cal_read: mod_cal_read,
    cal_write: mod_cal_write,
    fl_read: mod_fl_read,
    fl_prog: mod_fl_prog,
    fl_erase: mod_fl_erase,
    lcd_text: mod_lcd_text,
    lcd_bar: mod_lcd_bar,
    lcd_wf: mod_lcd_wf,
    lcd_flush: mod_lcd_flush,
    led: mod_led,
    clock_set: mod_clock_set,
    clock_get: mod_clock_get,
    lcd_glyphs: mod_lcd_glyphs,
    setting: mod_setting,
    sotto_play: mod_sotto_play,
    msg_mem: mod_msg_mem,
    help: mod_help,
    screen_tap: mod_screen_tap,
    redraw: mod_redraw,
};

// ---- level 11: a sotto receiver's message played (Jeff 2026-11-19, "proceed with playback") ----
//
// sotto_play(sf, n, codec, flags, from) only notes the message: the store it goes into overlaps
// the module's code, so run_module moves it there (sp_put) once the module returns, and the
// main loop plays it as any message (play_pending). KM_PLAY_RERUN: the library slot the module
// ran from runs again after playback (not a RAM module from the air: the store took its place).
// msg_mem: where a receiver kept the message while it listened, outside the arena, at level 11:
// SRAM 0x2000_0100-0x2000_0FFF. From level 12 (2026-11-19) the core's own buffers live there
// (hal::ram0) and their 3.2 KB went to the arena, so msg_mem returns nothing (null, 0 bytes) and
// a receiver keeps its message in its scratch, which is that much bigger (ysfrx: 21 s of sotto
// 1200, up from 18.4 s in msg_mem).

/// sotto_play's flag: listen again after playback (c/k6/k6_mod.h KM_PLAY_RERUN)
const KM_PLAY_RERUN: u32 = 1;
/// characters of the caller's callsign kept (KM_FROM_MAX)
pub const FROM_MAX: usize = 10;

struct Play {
    sf: usize,
    n: u16,
    codec: u8,
    flags: u8,
    from: [u8; FROM_MAX],
}
#[link_section = ".ram0"] // hal::ram0
static mut PLAY: Play = Play { sf: 0, n: 0, codec: 0, flags: 0, from: [0; FROM_MAX] };
/// the caller of the message in the store (from a sotto_play), for the screen; empty otherwise
#[link_section = ".ram0"] // hal::ram0
static mut PLAY_FROM: [u8; FROM_MAX] = [0; FROM_MAX];
/// the library slot the running module came from (255: sent over the air), and the slot to run
/// again after playback (255: none)
static mut RUN_SLOT: u8 = 255;
static mut RERUN: u8 = 255;

unsafe extern "C" fn mod_sotto_play(sf: *const u8, n: u32, codec: u32, flags: u32, from: *const u8) -> i32 {
    let p = unsafe { &mut *addr_of_mut!(PLAY) };
    p.n = 0;
    if n == 0 {
        return 0;
    }
    let (a, len) = (sf as usize, 6 * n as usize);
    let c = unsafe { &*addr_of!(MOD_CTX) };
    let inside = |lo: usize, hi: usize| a >= lo && a <= hi && len <= hi - a;
    if !inside(c.scratch, scratch_end()) {
        return -1;
    }
    let (app, _) = unsafe { mod_ctx() };
    if n > app.pager.store_frames as u32 {
        return -1;
    }
    if !matches!(codec, 0xA9 | 0xAC | 0xAD) {
        return -2;
    }
    p.from = [0; FROM_MAX];
    if !from.is_null() {
        for i in 0..FROM_MAX {
            let b = unsafe { *from.add(i) };
            if b == 0 {
                break;
            }
            p.from[i] = b;
        }
    }
    (p.sf, p.n, p.codec, p.flags) = (a, n as u16, codec as u8, flags as u8);
    0
}

unsafe extern "C" fn mod_msg_mem(bytes: *mut u32) -> *mut u8 {
    if !bytes.is_null() {
        unsafe { *bytes = 0 };
    }
    core::ptr::null_mut()
}

/// After a module returned: its sotto_play's message into the store, to play (true), or false
fn play_put(app: &mut App) -> bool {
    let p = unsafe { &mut *addr_of_mut!(PLAY) };
    if p.n == 0 {
        return false;
    }
    let n = core::mem::take(&mut p.n);
    let crc = unsafe { ffi::sp_crc16(p.sf as *const u8, 6 * n as i32) };
    let r = unsafe { ffi::sp_put(arena::base(), STORE_BYTES as u32, p.sf as *const u8, n as u32, p.codec as i32) };
    unsafe {
        PLAY_FROM = p.from;
        KEPT = false;
        RERUN = if p.flags as u32 & KM_PLAY_RERUN != 0 { RUN_SLOT } else { 255 };
    }
    let f = &p.from[..p.from.iter().position(|&b| b == 0).unwrap_or(FROM_MAX)];
    rl!(
        "K6 PLAY put r={} n={} codec={:02x} crc={:04x} from={} rerun={}",
        r, n, p.codec, crc, crate::out::ascii(f), unsafe { RERUN }
    );
    if r <= 0 {
        unsafe { RERUN = 255 };
        return false;
    }
    app.pager.played_id = -1;
    app.pager.play_pending = true;
    refresh_status(app);
    true
}

/// The caller of the message in the store, if a receiver module named one
pub fn play_from() -> &'static [u8] {
    let f = unsafe { &*addr_of!(PLAY_FROM) };
    &f[..f.iter().position(|&b| b == 0).unwrap_or(FROM_MAX)]
}

// ---- level 10: the radio's settings, for the SETTINGS module (Jeff 2026-11-17) ----
//
// setting(id, value, save): value -1 reads, 0.. sets it now (for this power-on); save 1 also
// writes Aura's settings record (its own wear-levelled region in SPI flash, where Aura's menu
// keeps them), so the value is there at the next power-on. Returns the value now, or -1: an id
// it doesn't know, a value out of range, or a save refused (while keyed, past SET_SAVES this
// run, a lean image). Ids (c/k6/k6_mod.h KM_SET_*): 0 the backlight's idle time (hal::light,
// 0 always on, 1-7 = 5, 10, 15, 20, 30, 60, 120 s).
const SET_LIGHT: i32 = 0;
/// settings-record writes a module run at most
const SET_SAVES: u8 = 4;
static mut SET_SAVED: u8 = 0;

#[cfg(feature = "lean")]
unsafe extern "C" fn mod_setting(_id: i32, _value: i32, _save: i32) -> i32 {
    -1
}

#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_setting(id: i32, value: i32, save: i32) -> i32 {
    mod_tx_guard();
    let c = unsafe { &*addr_of!(MOD_CTX) };
    if c.app.is_null() || id != SET_LIGHT || value > crate::hal::light::MAX as i32 {
        return -1;
    }
    let (app, _) = unsafe { mod_ctx() };
    if value >= 0 {
        crate::hal::light::set(value as u8);
        app.settings.backlight_time = value as u8;
    }
    if save != 0 {
        let n = unsafe { &mut *addr_of_mut!(SET_SAVED) };
        if c.tx || *n >= SET_SAVES {
            return -1;
        }
        *n += 1;
        app.save_settings();
        rl!("K6 SET saved bl={}", app.settings.backlight_time);
    }
    crate::hal::light::code() as i32
}

// ---- level 8: the radio's UTC clock (Jeff OK'd 2026-11-09; docs/k6-aprs-clock.md) ----
//
// A module that hears the time (aisclk, aprsclk) hands the core a fix: UTC ms of day now, the
// date if it has one, and a bound w (UTC is within w ms of it). The core runs it on from
// txdog::ticks, which never wraps, and every module after can read it, until the next reboot.
// The fix's bound grows by CLK_AGE: the MCU runs on its own RC clock, not the radio's TCXO
// (AIS run 3, 2026-11-09: 134 ppm fast, wandering +-100), so the RF calibration's ppb doesn't
// apply. A new fix replaces the old one only if its bound is no wider than the old one's now;
// date parts it lacks (0) come from the old one. Images without the TIM14 wrap interrupt (lean,
// or the txdog table not taken) have no clock: clock_set and clock_get return -1.

const DAY_MS: u32 = 86_400_000;
/// a fix's bound grows 1 ms every CLK_AGE ms: 250 ppm
const CLK_AGE: u32 = 4_000;

/// The fix: txdog::ticks at it (wraps, CNT), UTC ms of day, the date (day | month << 8 |
/// year << 16) and the bound (ms); w u32::MAX while there is none
struct Clk {
    wraps: u32,
    cnt: u32,
    ms: u32,
    date: u32,
    w: u32,
}
static mut CLK: Clk = Clk { wraps: 0, cnt: 0, ms: 0, date: 0, w: u32::MAX };

/// The clock now: (UTC ms of day, date, bound ms); None if never set, this image has no clock,
/// or the fix is 32,768 wraps (59.6 h) old. The VOICE MSG screen shows it (ui/pager.rs).
pub(crate) fn clk_now() -> Option<(u32, u32, u32)> {
    let (wraps, cnt) = crate::hal::txdog::ticks()?;
    let c = unsafe { &*addr_of!(CLK) };
    let dw = wraps.wrapping_sub(c.wraps);
    if c.w == u32::MAX || dw >= 0x8000 {
        return None;
    }
    let e = (dw << 16).wrapping_add(cnt).wrapping_sub(c.cnt) / 10;
    let (mut ms, mut date) = (c.ms + e, c.date);
    while ms >= DAY_MS {
        ms -= DAY_MS;
        date = next_day(date);
    }
    Some((ms, date, c.w.saturating_add(e / CLK_AGE)))
}

/// The day after `d` (day | month << 8 | year << 16, 0 for a part not known). February has 29
/// days in a year divisible by 4 or not known (2100 is decades off), an unknown month 31.
fn next_day(d: u32) -> u32 {
    let (day, mon, yr) = (d & 0xFF, d >> 8 & 0xFF, d >> 16);
    let len = match mon {
        2 => 29 - (yr & 3 != 0) as u32,
        _ => 30 | (mon ^ mon >> 3) & 1 | (mon == 0) as u32,
    };
    if day == 0 {
        d
    } else if day < len {
        d + 1
    } else if mon == 0 {
        d & !0xFF | 1
    } else if mon == 12 {
        1 | 1 << 8 | (yr + (yr != 0) as u32) << 16
    } else {
        (d & !0xFF | 1) + 0x100
    }
}

/// A fix: UTC `ms` of day now, `date` (day | month << 8 | year << 16, 0 for a part not known),
/// UTC within `w` ms. 1 taken, 0 the clock already had a fix as good, -1 refused (no clock in
/// this image, or ms past the day).
unsafe extern "C" fn mod_clock_set(ms: u32, mut date: u32, w: u32) -> i32 {
    let Some((wraps, cnt)) = crate::hal::txdog::ticks() else {
        return -1;
    };
    if ms >= DAY_MS || w == u32::MAX {
        return -1;
    }
    if let Some((_, old, ow)) = clk_now() {
        if w > ow {
            return 0;
        }
        for m in [0xFF, 0xFF00, 0xFFFF_0000] {
            if date & m == 0 {
                date |= old & m;
            }
        }
    }
    unsafe { *addr_of_mut!(CLK) = Clk { wraps, cnt, ms, date, w } };
    1
}

/// The clock: UTC ms of day, date and bound (ms) into whichever pointers aren't null; 0, or -1
/// with no clock (never set since boot, lost, or none in this image)
unsafe extern "C" fn mod_clock_get(ms: *mut u32, date: *mut u32, w: *mut u32) -> i32 {
    let Some((m, d, b)) = clk_now() else {
        return -1;
    };
    unsafe {
        if !ms.is_null() {
            *ms = m;
        }
        if !date.is_null() {
            *date = d;
        }
        if !w.is_null() {
            *w = b;
        }
    }
    0
}

/// Level 7: the green RX LED, for a module's own receive activity (and the coded 4FSK session's
/// one blink a packet). Off again when the module returns.
unsafe extern "C" fn mod_led(on: i32) {
    rx_led(on != 0);
}

// ---- the green RX LED by the volume knob (PA3, Aura's G_LED; Jeff asked 2026-11-08) ----
//
// One blink a packet while the modem receives: lit at each packet's sync (or its first
// FIFO-full), dark RX_LED_TICKS later, so a burst of packets (391 ms apart at PHY F) blinks
// about 2.5 times a second and the coded 4FSK session's (0.92 s apart) about once. Normal-RX
// Aura lights it while the squelch is open; the pager has no squelch, and main skips that
// housekeeping in pager mode, so the pin is the pager's here.

#[cfg(not(feature = "lean"))]
const RX_LED_TICKS: u16 = 1200; // 120 ms

/// When the LED was lit (uptime ticks), None while dark.
#[cfg(not(feature = "lean"))]
static mut RX_LED_T: Option<u16> = None;

#[cfg(not(feature = "lean"))]
fn rx_led(on: bool) {
    let gpioa = unsafe { &*kd32f328_pac::Gpioa::ptr() };
    crate::board::set_rx_led(gpioa, on);
    if !on {
        unsafe { *addr_of_mut!(RX_LED_T) = None };
    }
}

/// A packet's sync: the LED on for RX_LED_TICKS.
#[cfg(not(feature = "lean"))]
fn rx_blink(now: u16) {
    rx_led(true);
    unsafe { *addr_of_mut!(RX_LED_T) = Some(now) };
}

/// The LED off once its time is up (cheap: called from the receive loops' waits).
#[cfg(not(feature = "lean"))]
fn rx_led_poll(now: u16) {
    if let Some(t) = unsafe { *addr_of!(RX_LED_T) } {
        let ticks = if unsafe { *addr_of!(MOD_LED) } != 0 { MOD_LED_TICKS } else { RX_LED_TICKS };
        if now.wrapping_sub(t) >= ticks {
            rx_led(false);
        }
    }
}

// Jeff, 2026-11-21: while RAM ARENA takes a module over the air, the LED alternates green and
// orange. Green stays lit through the transfer and the red TX LED (the FD6818's GPIO5) joins it
// on every other module packet. Both go dark when the transfer ends (run, kept or refused), and
// MOD_LED_TICKS after the last packet when the sender stops partway; red is never left lit, as
// it means transmit.
#[cfg(not(feature = "lean"))]
const MOD_LED_TICKS: u16 = 15_000; // 1.5 s

/// 0 when the module LED is off, else 1 + the packets counted (bit 0 picks the colour)
#[cfg(not(feature = "lean"))]
static mut MOD_LED: u8 = 0;

/// A module packet partway through a transfer (on), or the transfer over (off)
#[cfg(not(feature = "lean"))]
fn upload_led(app: &mut App, syst: &mut SYST, on: bool) {
    let n = unsafe { &mut *addr_of_mut!(MOD_LED) };
    if on {
        *n = (*n % 2) + 1;
        rx_led(true);
        unsafe { *addr_of_mut!(RX_LED_T) = Some(uptime::now()) };
        app.radio.fd6818.set_tx_led(syst, *n == 2);
    } else if *n != 0 {
        *n = 0;
        rx_led(false);
        app.radio.fd6818.set_tx_led(syst, false);
    }
}

// The lean images (codebook install, MCU-flash table) leave the LED dark: aura-pager-cbi had
// 96 B of flash to spare, and the LED took 184 (CI, 07bb380). Their led() is a no-op.
#[cfg(feature = "lean")]
fn rx_led(_on: bool) {}
#[cfg(feature = "lean")]
fn rx_blink(_now: u16) {}
#[cfg(feature = "lean")]
fn rx_led_poll(_now: u16) {}

struct ModCtx {
    app: *mut App,
    syst: *mut SYST,
    rx_hz: u32,
    t_last: u16,
    ticks: u32,
    scratch: usize,
    /// while a module transmits: the tick (mod_tick's count) at which the TX ends by itself
    tx_until: u32,
    tx: bool,
    /// level 4: the calibration record was written this run (once a run)
    cal_written: bool,
    /// level 5: sector erases this module run or EXPLORE session (at most FL_ERASES)
    fl_erases: u8,
    /// bytes of say lines kept for the over-the-air report (0: none)
    tlm_cap: u16,
}
static mut MOD_CTX: ModCtx = ModCtx {
    app: core::ptr::null_mut(),
    syst: core::ptr::null_mut(),
    rx_hz: 0,
    t_last: 0,
    ticks: 0,
    scratch: 0,
    tx_until: 0,
    tx: false,
    cal_written: false,
    fl_erases: 0,
    tlm_cap: 0,
};

/// The registers a module may not write: the chip's TX/RX state, its GPIO lines, the PA and
/// the power switches. Receive only; `tune` is the way to move. One exception in REG_33 (GPIO
/// output enable-bar [15:8], value [7:0]): GP0's two bits, 8 and 0, may change. GP0 is the
/// line to the MCU's PB1 (net CTCSS, an input), so the I2S probe can drive it; every other
/// bit (GP2/GP3 band switching, GP5 the TX LED) must be written as it reads. REG_33 is put
/// back as it was when the module returns.
///
/// Level 14, one exception in REG_30 (the block enables): the three audio blocks, bit 9 the
/// AF DAC, bit 2 the MICIN ADC and bit 1 the TX DSP (the tone oscillator's), may change, for
/// the mic probe (Jeff, 2026-11-19). The RF blocks (VCO, PLL, RX link, PA gain, RX DSP) must
/// be written as they read, so this keys nothing: tx_on is still the only way onto the air.
/// A module never puts the mic on the air: the MICIN bit is refused while the PA is on
/// (REG_36's PACTL), and tx_pa(1) clears it first. tx_on, tx_off and the retune after the
/// module write REG_30 whole again.
const MOD_NO_WRITE: [u8; 4] = [0x30, 0x33, 0x36, 0x37];
const MOD_GPIO: u8 = 0x33;
const MOD_GP0_BITS: u16 = 0x0101;
const MOD_STATE: u8 = 0x30;
const MOD_AUDIO_BITS: u16 = 0x0206;
const MOD_MIC_BIT: u16 = 0x0004;
const MOD_PA: u8 = 0x36;
const MOD_PACTL: u16 = 0x0080;

/// The app and SysTick while a module runs (km_run is called with both borrowed, and nothing
/// else touches them until it returns).
unsafe fn mod_ctx() -> (&'static mut App, &'static mut SYST) {
    let c = unsafe { &*addr_of!(MOD_CTX) };
    unsafe { (&mut *c.app, &mut *c.syst) }
}

unsafe extern "C" fn mod_fd_read(reg: u8) -> u16 {
    let (app, syst) = unsafe { mod_ctx() };
    app.radio.fd6818.reg_read(syst, reg)
}

unsafe extern "C" fn mod_fd_write(reg: u8, value: u16) -> i32 {
    mod_tx_guard();
    let (app, syst) = unsafe { mod_ctx() };
    if reg == MOD_GPIO || reg == MOD_STATE {
        let free = if reg == MOD_GPIO { MOD_GP0_BITS } else { MOD_AUDIO_BITS };
        let now = app.radio.fd6818.reg_read(syst, reg);
        if (now ^ value) & !free != 0 {
            return -1;
        }
        if reg == MOD_STATE
            && value & MOD_MIC_BIT != 0
            && app.radio.fd6818.reg_read(syst, MOD_PA) & MOD_PACTL != 0
        {
            return -1;
        }
    } else if MOD_NO_WRITE.contains(&reg) {
        return -1;
    }
    app.radio.fd6818.reg_write(syst, reg, value);
    0
}

unsafe extern "C" fn mod_delay_us(mut us: u32) {
    let (_, syst) = unsafe { mod_ctx() };
    while us > 0 {
        let n = us.min(100_000); // SysTick's reload is 24 bits: 174 ms at 96 MHz
        delay::us(syst, n);
        us -= n;
        mod_tick();
        mod_tx_guard();
    }
}

unsafe extern "C" fn mod_tune(hz: u32, wide: i32) {
    let (app, syst) = unsafe { mod_ctx() };
    app.radio.retune_rx(syst, hz, wide != 0);
}

unsafe extern "C" fn mod_rx_hz() -> u32 {
    unsafe { (*addr_of!(MOD_CTX)).rx_hz }
}

/// Adds the 100 us ticks since the last call: the counter wraps every 6.5 s, so ms and
/// delay_us both call this
fn mod_tick() -> u32 {
    let c = unsafe { &mut *addr_of_mut!(MOD_CTX) };
    let now = uptime::now();
    c.ticks = c.ticks.wrapping_add(now.wrapping_sub(c.t_last) as u32);
    c.t_last = now;
    c.ticks
}

/// ms since the module started
unsafe extern "C" fn mod_ms() -> u32 {
    mod_tx_guard();
    mod_tick() / 10
}

/// The shared RAM block (hal::arena) past the module's code and tag, up to the telemetry text
/// if the module keeps one: free while the module runs (a module has no statics; this is where
/// its tables and buffers go). Word aligned; its size into *bytes.
unsafe extern "C" fn mod_scratch(bytes: *mut u32) -> *mut u8 {
    let at = unsafe { (*addr_of!(MOD_CTX)).scratch };
    let end = scratch_end();
    if !bytes.is_null() {
        unsafe { *bytes = end.saturating_sub(at) as u32 };
    }
    at as *mut u8
}

/// Where a module's scratch ends: the block's end, or the telemetry text's start
#[cfg(not(feature = "lean"))]
fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).tlm_cap } != 0 {
        arena::tlm() as usize
    } else {
        arena::end()
    }
}

#[cfg(feature = "lean")] // no telemetry
fn scratch_end() -> usize {
    arena::end()
}

unsafe extern "C" fn mod_key_down() -> i32 {
    mod_tx_guard();
    let (app, syst) = unsafe { mod_ctx() };
    mod_light(app.keypad.any_pressed(syst))
}

/// A module's key check also runs the backlight (hal::light): a key held lights it, and the
/// idle time runs out while the module runs (SURF runs for as long as it is used).
fn mod_light(any: bool) -> i32 {
    if any {
        crate::hal::light::touch();
    }
    crate::hal::light::poll();
    any as i32
}

unsafe extern "C" fn mod_say(s: *const u8) {
    let mut n = 0;
    while n < 60 && unsafe { *s.add(n) } != 0 {
        n += 1;
    }
    let t = crate::out::ascii(unsafe { core::slice::from_raw_parts(s, n) });
    rl!("K6 MOD say {}", t);
    tlm_add(t.as_bytes());
}

unsafe extern "C" fn mod_ticks() -> u32 {
    mod_tx_guard();
    uptime::now() as u32
}

// ---- level 3: a guarded transmitter for modules ----
//
// tx_on keys up on the pager's own frequency only (MOD_CTX.rx_hz; Aura's TX band lock,
// 144-148 and 420-450 MHz, still applies), FM, low power, no CTCSS, mic ADC off, and for at
// most MOD_TX_MAX_MS. Every module call (fd_write, delay_us, ms, ticks, key_down) checks the
// time and keys down once it is up; the core keys down when the module returns, too. The
// module makes its own modulation (REG_3B, the tone oscillator REG_70/71) and its own CW ID;
// REG_30/33/36/37 stay refused (tx_pa is the PA switch, for CW keying).

const MOD_TX_MAX_MS: u32 = 120_000; // 20 s until 2026-11-07: a WSPR frame is 110.6 s
/// REG_30 while a module transmits: Aura's STATE_TX_ON (0xC1FE) with the MICIN ADC (bit 2) off
const MOD_TX_STATE: u16 = 0xC1FA;

/// Key down if the module's time is up
fn mod_tx_guard() {
    crate::hal::txdog::poll(); // TIM14's wraps, for a module that runs past one (6.5 s)
    let (tx, live, until) = unsafe {
        let c = &*addr_of!(MOD_CTX);
        (c.tx, !c.app.is_null(), c.tx_until)
    };
    if tx && live && mod_tick().wrapping_sub(until) < 0x8000_0000 {
        rl!("K6 MOD tx timeout");
        tlm_add(b"(tx timeout)");
        unsafe { mod_tx_off() };
    }
}

/// The `lean` images (the codebook install, the -table fallbacks) have no room: always refused
#[cfg(feature = "lean")]
unsafe extern "C" fn mod_tx_on(_max_ms: u32) -> i32 {
    -1
}

/// 0: on the air; -1 refused (band lock, or no module running); -2 already on
#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_tx_on(max_ms: u32) -> i32 {
    let (live, tx, hz) = unsafe {
        let c = &*addr_of!(MOD_CTX);
        (!c.app.is_null(), c.tx, c.rx_hz)
    };
    if !live {
        return -1;
    }
    if tx {
        return -2;
    }
    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);
    let until = mod_tick().wrapping_add(ms * 10);
    let (app, syst) = unsafe { mod_ctx() };
    disarm(app, syst);
    app.radio.set_frequency(hz);
    app.radio.set_tx_frequency(hz);
    app.radio.set_power(crate::drivers::fd6818::Power::Low);
    app.radio.set_subaudio_tx(SubAudio::None);
    app.radio.set_modulation(Modulation::Fm);
    app.radio.apply_pa_calibration(&mut app.storage, hz, crate::drivers::fd6818::Power::Low);
    // The module transmitter's own band lock, chosen by Jeff (licensee AG7EW) on 2026-11-06:
    // the US amateur 2 m and 70 cm bands (FCC HAM, 144-148 and 420-450 MHz), whatever the
    // radio's FLOCK says, since the pager image has no menu to change it. Only for this
    // key-up: the radio's own lock is put back straight after.
    app.radio.set_tx_allowed(BandLock::Fcc.tx_ranges());
    let ok = app.radio.enter_tx(syst);
    app.radio.set_tx_allowed(BandLock::from_u8(app.settings.band_lock).tx_ranges());
    if !ok {
        rl!("K6 MOD tx refused hz={}", hz);
        tlm_add(b"(tx refused)");
        app.radio.enter_rx(syst);
        return -1;
    }
    // the module's own limit is checked on its calls; a module that hangs keyed is not
    crate::hal::txdog::limit(ms + 10_000);
    app.radio.fd6818.reg_write(syst, 0x30, MOD_TX_STATE);
    unsafe {
        let c = &mut *addr_of_mut!(MOD_CTX);
        c.tx = true;
        c.tx_until = until;
    }
    rl!("K6 MOD tx on hz={} max={} ms", hz, ms);
    0
}

unsafe extern "C" fn mod_tx_off() {
    unsafe {
        let c = &mut *addr_of_mut!(MOD_CTX);
        if !c.tx || c.app.is_null() {
            return;
        }
        c.tx = false;
    }
    let (app, syst) = unsafe { mod_ctx() };
    app.radio.enter_rx(syst); // not end_tx: no roger tone
    let power = app.sides[app.master].cfg.power;
    app.radio.set_power(power);
    rl!("K6 MOD tx off");
}

/// The PA on (1) or off (0) while keyed: CW keying with the PLL locked. -1 if not keyed.
unsafe extern "C" fn mod_tx_pa(on: i32) -> i32 {
    mod_tx_guard();
    if !unsafe { (*addr_of!(MOD_CTX)).tx } {
        return -1;
    }
    let (app, syst) = unsafe { mod_ctx() };
    if on != 0 {
        let state = app.radio.fd6818.reg_read(syst, MOD_STATE);
        if state & MOD_MIC_BIT != 0 {
            app.radio.fd6818.reg_write(syst, MOD_STATE, state & !MOD_MIC_BIT);
        }
        app.radio.cw_carrier_on(syst);
    } else {
        app.radio.cw_carrier_off(syst);
    }
    0
}

// ---- level 4: a cheap key check and the calibration record ----

/// 1 while any key is held, in about 5 us: all five keypad rows driven low at once (Aura's
/// scan drives one at a time with a 5 us SysTick wait each, ~60 us in all), the four column
/// inputs read (PC13, PC14, PF6, PB14; the side keys read low with no row driven), rows idle again.
#[cfg(feature = "lean")]
unsafe extern "C" fn mod_key_poll() -> i32 {
    unsafe { mod_key_down() }
}

#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_key_poll() -> i32 {
    mod_tx_guard();
    let gb = unsafe { &*kd32f328_pac::Gpiob::ptr() };
    let gc = unsafe { &*kd32f328_pac::Gpioc::ptr() };
    let gf = unsafe { &*kd32f328_pac::Gpiof::ptr() };
    gb.brr()
        .write(|w| w.br0().set_bit().br4().set_bit().br9().set_bit().br10().set_bit().br11().set_bit());
    cortex_m::asm::delay(300); // ~3 us at 96 MHz for the lines to settle
    let c = gc.idr().read();
    let any = c.idr13().bit_is_clear()
        || c.idr14().bit_is_clear()
        || gf.idr().read().idr6().bit_is_clear()
        || gb.idr().read().idr14().bit_is_clear();
    crate::board::set_keypad_rows_idle(gb);
    mod_light(any)
}

/// The radio's calibration record (c/k6/mod/k6_cal.h): a 4 KB SPI flash sector of its own,
/// after sotto's codebooks (0x100000 v3.7, 0x108000 sotto 600 / 400)
const CAL_ADDR: u32 = 0x10_F000;
const CAL_BYTES: u32 = 64;

#[cfg(feature = "lean")]
unsafe extern "C" fn mod_cal_read(_buf: *mut u8, _n: u32) -> i32 {
    -1
}

#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_cal_read(buf: *mut u8, n: u32) -> i32 {
    mod_tx_guard();
    if buf.is_null() || n > CAL_BYTES {
        return -1;
    }
    let (app, _) = unsafe { mod_ctx() };
    app.storage
        .norflash
        .read_bytes(CAL_ADDR, unsafe { core::slice::from_raw_parts_mut(buf, n as usize) });
    n as i32
}

#[cfg(feature = "lean")]
unsafe extern "C" fn mod_cal_write(_buf: *const u8, _n: u32) -> i32 {
    -1
}

/// Erase the sector and write n bytes: once a module run, never while keyed. 0 or -1.
#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_cal_write(buf: *const u8, n: u32) -> i32 {
    mod_tx_guard();
    let c = unsafe { &mut *addr_of_mut!(MOD_CTX) };
    if buf.is_null() || n == 0 || n > CAL_BYTES || c.tx || c.cal_written || c.app.is_null() {
        return -1;
    }
    c.cal_written = true;
    let (app, _) = unsafe { mod_ctx() };
    app.storage.norflash.erase_sector(CAL_ADDR);
    app.storage
        .norflash
        .write_bytes(CAL_ADDR, unsafe { core::slice::from_raw_parts(buf, n as usize) });
    rl!("K6 MOD cal write n={} at {:#x}", n, CAL_ADDR);
    0
}

// ---- level 5: the SPI flash, for the repeater table (Jeff approved it, 2026-11-07) ----
//
// fl_read reads anywhere in 0x0F0000-0x1FFFFF (the table, sotto's codebooks, the calibration
// record). fl_prog (up to 256 bytes inside one page) and fl_erase (one 4 KB sector) work in the
// table's 64 KB only, 0x0F0000-0x0FFFFF: Aura's own data ends at 0xED200, sotto's codebooks
// start at 0x100000. Neither works while keyed (but see level 15), and a module run (or an EXPLORE session) gets
// at most FL_ERASES erases: a compaction takes one, a mount repair two, so 32 stops a runaway
// loop long before it wears a sector (about 100,000 erases each). The same three calls are the
// EXPLORE app's rt_flash_t (explore.rs), so the app and a module go through one set of checks.

const FL_LO: u32 = 0x0F_0000;
const FL_HI: u32 = 0x10_0000;
// level 15 (Jeff, 2026-11-20: "Core SPI outbox"): fl_prog and fl_erase also work in the FuSotto
// outbox, 64 KB after the module library (KL_END), where FM SOTTO writes what it codes and a
// sender module reads it back. Nothing else in the K6 uses 0x190000 up. fl_prog works there
// while keyed too when REG_36 says the PA is off (Jeff, 2026-11-21: "record to SPI"; the mic is
// live only keyed with the PA off, micsotto): a page takes about a millisecond. Erases stay
// refused while keyed (tens of ms or more): a module erases before it keys.
const OB_LO: u32 = 0x19_0000;
const OB_HI: u32 = 0x1A_0000;
const FL_READ_HI: u32 = 0x20_0000;
const FL_PAGE: u32 = 256;
const FL_SECTOR: u32 = 4096;
const FL_ERASES: u8 = 32;

#[cfg(feature = "lean")]
unsafe extern "C" fn mod_fl_read(_addr: u32, _buf: *mut u8, _n: u32) -> i32 {
    -1
}
#[cfg(feature = "lean")]
unsafe extern "C" fn mod_fl_prog(_addr: u32, _buf: *const u8, _n: u32) -> i32 {
    -1
}
#[cfg(feature = "lean")]
unsafe extern "C" fn mod_fl_erase(_addr: u32) -> i32 {
    -1
}

#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_fl_read(addr: u32, buf: *mut u8, n: u32) -> i32 {
    mod_tx_guard();
    let live = unsafe { !(*addr_of!(MOD_CTX)).app.is_null() };
    if buf.is_null() || !live || addr < FL_LO || addr >= FL_READ_HI || n > FL_READ_HI - addr {
        return -1;
    }
    let (app, _) = unsafe { mod_ctx() };
    app.storage
        .norflash
        .read_bytes(addr, unsafe { core::slice::from_raw_parts_mut(buf, n as usize) });
    0
}

/// The repeater table or the outbox
#[cfg(not(feature = "lean"))]
fn fl_writable(addr: u32) -> bool {
    (FL_LO..FL_HI).contains(&addr) || (OB_LO..OB_HI).contains(&addr)
}

/// Program n bytes (1-256) inside one page of the table or the outbox: 0, or -1
#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_fl_prog(addr: u32, buf: *const u8, n: u32) -> i32 {
    mod_tx_guard();
    let c = unsafe { &*addr_of!(MOD_CTX) };
    if buf.is_null() || c.app.is_null() || n == 0 || n > FL_PAGE || !fl_writable(addr)
        || (addr % FL_PAGE) + n > FL_PAGE
    {
        return -1;
    }
    let (app, syst) = unsafe { mod_ctx() };
    // level 15: keyed with the PA off is fine for a page (the mic is live only then, micsotto)
    if c.tx && (!(OB_LO..OB_HI).contains(&addr) || app.radio.fd6818.reg_read(syst, MOD_PA) & MOD_PACTL != 0) {
        return -1;
    }
    app.storage
        .norflash
        .write_bytes(addr, unsafe { core::slice::from_raw_parts(buf, n as usize) });
    0
}

/// Erase the 4 KB sector at addr (the table's or the outbox's): 0, or -1
#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_fl_erase(addr: u32) -> i32 {
    mod_tx_guard();
    let c = unsafe { &mut *addr_of_mut!(MOD_CTX) };
    if c.app.is_null() || c.tx || !fl_writable(addr) || addr % FL_SECTOR != 0 || c.fl_erases >= FL_ERASES {
        rl!("K6 FL erase {:#x} refused (n={})", addr, c.fl_erases);
        return -1;
    }
    c.fl_erases += 1;
    let n = c.fl_erases;
    let (app, _) = unsafe { mod_ctx() };
    app.storage.norflash.erase_sector(addr);
    rl!("K6 FL erase {:#x} n={}", addr, n);
    0
}

// ---- level 6: the module's screen ----
//
// The calls fill MOD_SCREEN (six lines of 21 characters, a bar per line, and a pointer to the
// module's waterfall rows); lcd_flush draws it all through the hook main.rs sets
// (ui::hook_mod_screen) and returns 0, or -1 when there is none. Nothing draws it but
// lcd_flush, so the main loop's screen comes back when the module returns. Byte loops, no
// struct copies: a memcpy would pull in compiler_builtins' mem functions.

pub const MOD_LINES: usize = 6;
pub const MOD_COLS: usize = 21;
/// the waterfall: up to WF_ROWS rows of 128 pixels (16 bytes, bit 7 of byte 0 leftmost) from
/// y = WF_TOP down, over text lines 2-4
pub const WF_TOP: i32 = 21;
pub const WF_ROWS: i32 = 30;

pub struct ModScreen {
    pub text: [[u8; MOD_COLS]; MOD_LINES],
    pub bar: [(u32, u32); MOD_LINES],
    pub wf: *const u8,
    pub wf_rows: i32,
    pub wf_head: i32,
    /// level 9: the module's glyphs, MOD_GLYPH_COLS columns each (bit r the row y - 8 + r of a
    /// line whose baseline is y), in the module's memory; gl_n of them
    pub gl: *const u16,
    pub gl_n: i32,
}
#[link_section = ".ram0"] // hal::ram0
pub static mut MOD_SCREEN: ModScreen = ModScreen {
    text: [[0; MOD_COLS]; MOD_LINES],
    bar: [(0, 0); MOD_LINES],
    wf: core::ptr::null(),
    wf_rows: 0,
    wf_head: 0,
    gl: core::ptr::null(),
    gl_n: 0,
};
/// level 9: a module glyph's width (from its cell's x - 1) and the most a module may give
pub const MOD_GLYPH_COLS: usize = 6;
pub const MOD_GLYPHS: i32 = 8;
static mut MOD_LCD: Option<unsafe fn() -> i32> = None;

/// main.rs: the function that draws MOD_SCREEN and flushes the display
pub fn set_mod_lcd(f: unsafe fn() -> i32) {
    unsafe { *addr_of_mut!(MOD_LCD) = Some(f) };
}

fn mod_screen_clear() {
    let s = unsafe { &mut *addr_of_mut!(MOD_SCREEN) };
    for l in 0..MOD_LINES {
        for c in 0..MOD_COLS {
            s.text[l][c] = 0;
        }
        s.bar[l] = (0, 0);
    }
    s.wf = core::ptr::null();
    s.wf_rows = 0;
    s.wf_head = 0;
    s.gl = core::ptr::null();
    s.gl_n = 0;
}

/// line 0-5 (0 is the title bar), up to 21 characters; "" or null clears it. 0x80 + k (level 9)
/// draws the module's glyph k (lcd_glyphs); other bytes outside 0x20-0x7E show '?'
unsafe extern "C" fn mod_lcd_text(line: i32, t: *const u8) {
    if !(0..MOD_LINES as i32).contains(&line) {
        return;
    }
    let row = unsafe { &mut (*addr_of_mut!(MOD_SCREEN)).text[line as usize] };
    let mut end = t.is_null();
    for c in 0..MOD_COLS {
        let b = if end { 0 } else { unsafe { *t.add(c) } };
        end |= b == 0;
        row[c] = if (b >= 0x20 && b < 0x7F) || (b >= 0x80 && b < 0x80 + MOD_GLYPHS as u8) || b == 0 { b } else { b'?' };
    }
}

/// a bar after line's text, num of den full; den 0 takes it away
unsafe extern "C" fn mod_lcd_bar(line: i32, num: u32, den: u32) {
    if (0..MOD_LINES as i32).contains(&line) {
        unsafe { (*addr_of_mut!(MOD_SCREEN)).bar[line as usize] = (num.min(den), den) };
    }
}

/// the waterfall: rows x 16 bytes in the module's memory, ring[head] drawn at the top; 0 rows
/// takes it away (the module's memory: valid until it returns or calls again)
unsafe extern "C" fn mod_lcd_wf(ring: *const u8, rows: i32, head: i32) {
    let s = unsafe { &mut *addr_of_mut!(MOD_SCREEN) };
    let rows = if ring.is_null() { 0 } else { rows.clamp(0, WF_ROWS) };
    s.wf = ring;
    s.wf_rows = rows;
    s.wf_head = if rows > 0 { head.rem_euclid(rows) } else { 0 };
}

/// level 9: n glyphs of MOD_GLYPH_COLS u16 columns in the module's memory (valid until it
/// returns or calls again), drawn where a line has 0x80 + k; n 0 or a null takes them away
unsafe extern "C" fn mod_lcd_glyphs(cols: *const u16, n: i32) {
    let s = unsafe { &mut *addr_of_mut!(MOD_SCREEN) };
    s.gl_n = if cols.is_null() { 0 } else { n.clamp(0, MOD_GLYPHS) };
    s.gl = cols;
}

unsafe extern "C" fn mod_lcd_flush() -> i32 {
    mod_tx_guard();
    crate::hal::light::poll();
    match unsafe { *addr_of!(MOD_LCD) } {
        Some(f) if !unsafe { (*addr_of!(MOD_CTX)).app.is_null() } => unsafe { f() },
        _ => -1,
    }
}

// ---- telemetry: a module's say lines, kept for the over-the-air report ----
//
// They go in the last TLM_BYTES of the shared RAM block (hal::arena): past the store and the
// ring, so they outlast the module and clear_store, until a playback's decoder takes that RAM
// (it zeroes its pool, which overwrites the "TLM1" mark) or the next module. A report sends the
// text only while the mark is intact. A module that keeps telemetry gets that much less
// scratch. No RAM of its own: the pager's stack has little to spare.
// The text is a ring of tlm_cap bytes that keeps the newest, written a byte at a time (a
// memmove would pull in compiler_builtins' mem functions, 1.8 KB of flash).

const TLM_MARK: u32 = u32::from_le_bytes(*b"TLM1");
/// the mark (u32), bytes held (u16, up to the cap), the write position (u16)
const TLM_HDR: usize = 8;

#[cfg(not(feature = "lean"))]
fn tlm_hdr() -> (u32, usize, usize) {
    let b = arena::tlm();
    unsafe {
        let m = read_volatile(b as *const u32);
        (m, *b.add(4) as usize | (*b.add(5) as usize) << 8, *b.add(6) as usize | (*b.add(7) as usize) << 8)
    }
}

#[cfg(not(feature = "lean"))]
fn tlm_set(n: usize, w: usize) {
    let b = arena::tlm();
    unsafe {
        *b.add(4) = n as u8;
        *b.add(5) = (n >> 8) as u8;
        *b.add(6) = w as u8;
        *b.add(7) = (w >> 8) as u8;
    }
}

/// Start keeping say lines, at most cap bytes (the newest)
#[cfg(not(feature = "lean"))]
fn tlm_start(cap: usize) {
    unsafe { write_volatile(arena::tlm() as *mut u32, TLM_MARK) };
    tlm_set(0, 0);
    unsafe { (*addr_of_mut!(MOD_CTX)).tlm_cap = cap.min(arena::TLM_BYTES - TLM_HDR) as u16 };
}

/// A line, '\n' after it
#[cfg(not(feature = "lean"))]
fn tlm_add(t: &[u8]) {
    let cap = unsafe { (*addr_of!(MOD_CTX)).tlm_cap } as usize;
    let (m, mut n, mut w) = tlm_hdr();
    if cap == 0 || m != TLM_MARK {
        return;
    }
    let b = arena::tlm();
    for &c in t.iter().chain(b"\n".iter()) {
        unsafe { *b.add(TLM_HDR + w) = c };
        w += 1;
        if w == cap {
            w = 0;
        }
        if n < cap {
            n += 1;
        }
    }
    tlm_set(n, w);
}

#[cfg(feature = "lean")]
fn tlm_add(_t: &[u8]) {}

/// The text's length: 0 if a playback overwrote it
#[cfg(not(feature = "lean"))]
fn tlm_len() -> usize {
    let (m, n, _) = tlm_hdr();
    if m != TLM_MARK || n > arena::TLM_BYTES - TLM_HDR {
        0
    } else {
        n
    }
}

/// Bytes k0 .. k0 + out.len() of the text, oldest first (cap: as tlm_start had it)
#[cfg(not(feature = "lean"))]
fn tlm_read(k0: usize, out: &mut [u8], cap: usize) -> usize {
    let (_, n, w) = tlm_hdr();
    let b = arena::tlm();
    let mut i = if n < cap { k0 } else { w + k0 };
    let mut got = 0;
    while got < out.len() && k0 + got < n {
        while i >= cap {
            i -= cap;
        }
        out[got] = unsafe { *b.add(TLM_HDR + i) };
        i += 1;
        got += 1;
    }
    got
}

const SP_BAD_MAGIC: i32 = -2;
const SP_BAD_CRC: i32 = -1;
const SP_COMPLETE: i32 = 3;

// FD6818 / BK4819 registers (spec section 9)
const REG_IRQ_FLAGS: u8 = 0x02; // read: cause; write 0: clear
const REG_SYNC_STATUS: u8 = 0x0B; // <7> SyncNegative, <6> SyncPositive
const REG_STATUS: u8 = 0x0C; // <0> interrupt pending
const REG_IRQ_EN: u8 = 0x3F; // Aura's "work mode"
const REG_FSK_CFG: u8 = 0x58;
const REG_FSK_CTRL: u8 = 0x59;
const REG_SYNC01: u8 = 0x5A;
const REG_SYNC23: u8 = 0x5B;
const REG_FSK_5C: u8 = 0x5C;
const REG_FSK_LEN: u8 = 0x5D;
const REG_FIFO_THR: u8 = 0x5E;
const REG_FIFO: u8 = 0x5F;
const REG_TX_GAIN: u8 = 0x70;
const REG_BAUD: u8 = 0x72;

const IRQ_TX_DONE: u16 = 1 << 15;
const IRQ_RX_DONE: u16 = 1 << 13;
const IRQ_RX_FULL: u16 = 1 << 12;
const IRQ_RX_SYNC: u16 = 1 << 1;

const CTRL_BASE: u16 = 0x00F8; // 16 preamble bytes, 4-byte sync, no scrambler
const CTRL_CLR_TX: u16 = 0x8000;
const CTRL_CLR_RX: u16 = 0x4000;
const CTRL_RX_EN: u16 = 0x1000;
const CTRL_TX_EN: u16 = 0x0800;
const CTRL_RX_INVERT: u16 = 0x0400;

const CFG_F_1200: u16 = 0x3FE3; // FFSK 1200/1800
/// FFSK 1200/1800 tones at 1800 baud, the F default since 2026-11-05: REG_58 as the UV-K5 MCFW
/// messenger writes it (bits 7:6 clear). On air with interrupt reads (5e=0203) and DC
/// cancellation off: 12/12, 23/24 and 12/12, every decoded packet 0 bit errors
const CFG_F_1800: u16 = 0x3F01;
/// D/2400, the rate ladder's fast rung: plain 2-FSK (TX and RX mode 0, direct FSK) with the RX
/// bandwidth for FSK 2.4K (REG_58 <3:1> = 4; UV-K5 firmware notes), as the pager's old PHY D
/// wrote it before PR #43. From the Pluto's flat FM (fsk24 run 3, 2026-11-16) the chip read it
/// 234 of 234 packets with 0 bits wrong at 1.5-4.5 kHz deviation, and with REG_58 00e1 too, so
/// the chip isn't the limit; the IC-7100's USB audio is (runs 1 and 2), so only flat
/// transmitters ask for it. The FFSK 1200/2400 tone variants (0x73E9 and ten others, control
/// `var`) are gone: none ever synced on air (2026-11-04/05)
const CFG_D_2400: u16 = 0x00E9;
/// REG_2B <10>: the RX 300 Hz high-pass off, at D/2400 only (fsk24 run 3's setting 2, the
/// sensitivity sweep's D/2400, as the RT-950 note set the BK4829 up for its NRZ modes). <9> is
/// the RX 3 kHz low-pass, <8> RX de-emphasis; REG_2B reads 0xC000 on this K6
const AF_HPF_OFF: u16 = 0x0400;
const REG_AF_FILT: u8 = 0x2B;
/// REG_2B <14>: DC cancellation after the FM demodulator. Cleared for every PHY and baud: with it
/// on, direct FSK at 1200 got 7 of 28 packets (the last 16 bytes corrupt); with it off, 31 of 31
/// (2026-11-05, also with interrupt reads), and FFSK 1200 stayed clean (15/15). `--reg 2b=c000`
/// turns it back on for 60 s.
const AF_DC_CANCEL: u16 = 0x4000;
/// RF/AF receive bandwidth (Aura's wide/narrow, as saved for the VFO; the pager never sets it).
/// Writable by control packet to try other filter settings at 2400 baud
const REG_RX_BW: u8 = 0x43;
/// Gain after the FM demodulator, <12:11> (programming guide)
const REG_DEMOD_GAIN: u8 = 0x44;
/// AFC: <13:11> range 2.2 kHz down to 188 Hz (x2 wide), <4> 1 = AFC off
const REG_AFC: u8 = 0x73;
/// <6> RX DC filter bypass, among TX bits
const REG_DC_FILT: u8 = 0x7E;
/// <13:8> receive discriminator gain, 0.5 dB steps (FD6818 guide; may not exist on 0x4819)
const REG_DISC_GAIN: u8 = 0x96;
/// RX <9> 300 Hz HPF, <8> LPF1, <7> LPF2, <6> de-emphasis bypass (FD6818 guide, as 96)
const REG_AF_BYPASS: u8 = 0x9B;
/// Receive registers the pager doesn't set itself but control packets may write: each is read
/// once, then written back from that copy on every configure, so a revert undoes a write.
const BASED: [u8; 6] = [REG_RX_BW, REG_DEMOD_GAIN, REG_AFC, REG_DC_FILT, REG_DISC_GAIN, REG_AF_BYPASS];
const BAUD_1200: u16 = 0x3065;
const BAUD_1800: u16 = 0x4898;
/// 2400 x 10.32444 = 24779 (fsk24rx's; the old 0x60CA was 0.004% slow)
const BAUD_2400: u16 = 0x60CB;
/// the old PHY D's TX gain (its 2400 baud CAL burst decoded on the IC-7100, 2026-11-04)
const GAIN_D: u16 = 0x00E0;
const GAIN_F: u16 = 0x00B4;
const CRC_OFF: u16 = 0x5625;
const FIFO_THR: u16 = 0x0204; // TX almost empty 64 words, RX almost full 4 words
/// every rung's default: interrupt reads, 3 words an interrupt (F/1800 on air; D/2400 11-17)
const FIFO_THR_IRQ: u16 = 0x0203;

const TICKS_PER_S: u32 = 10_000; // uptime: 100 us
const GAP_TICKS: u32 = 3 * TICKS_PER_S; // play 3 s after the last packet
const PACKET_TIMEOUT_TICKS: u32 = 2 * TICKS_PER_S; // a 128-byte packet is 853 ms at 1200 baud
/// Timed FIFO reads: words taken right at the sync interrupt. On air (profile S), 4 good words
/// were already waiting 22-33 ms after it, so the interrupt is seen at least 2 words late;
/// one word of lead keeps 2-4 words in the 8-word FIFO, away from both empty and overflow.
const TIMED_LEAD_WORDS: usize = 1;
/// RX FIFO depth as measured on air (the FD6818 guide's 64 words is another chip's)
const FIFO_WORDS: usize = 8;
const TIMED_LEAD_WORDS_2400: usize = 3;
/// With timed reads, an RX-done sooner than 60% of the payload's air time after the sync
/// (67 ticks a byte at 1200 baud) is the last packet's, not this one's
const STRAY_DONE_TICKS_PER_BYTE: usize = 40;
/// the re-arm delays after a timed packet's end, ms (key 8 stepped them until 2026-11-13; the
/// first is used); after them, a sweep
const REARM_STEPS: [u8; 7] = [40, 50, 30, 60, 20, 0, 80];
/// After the earliest re-arm, wait this long more for the chip's RX-done (100 us ticks)
const REARM_WAIT_TICKS: u16 = 100;
/// A K6 CFG line this often while idle (100 us ticks; 10 s), so the state is on the serial
/// log without a key press (Jeff, 2026-11-04)
const CFG_EVERY_TICKS: u32 = 100_000;
/// ... but only this long after the last good packet (2 s), not inside a burst
const CFG_QUIET_TICKS: u32 = 20_000;
/// A control packet's change of PHY, profile, baud or variant is undone unless a good packet
/// (voice or control) arrives this soon after it (100 us ticks; 60 s), so a command can't
/// leave the radio deaf to the sender (Jeff can't press keys all day, 2026-11-04)
const CTL_REVERT_TICKS: u32 = 600_000;
// control record mask bits (c/k6/sotto_pager.h SP_CTL_*; the value is the same bit of byte 3)
// bit 0 is PHY (1 F, 0 D) and bit 2 baud (1 2400): k6_rate.c's kr_control reads those two (D
// only with 2400, from 2026-11-16; before, from PR #43, the PHY bit was ignored); bit 5 was the
// FFSK 2400 receive variant, gone with the variants
const CTL_FRAMES: u8 = 2;
// c/k6/k6_rate.h: kr_control's D/2400, kr_t's why
const KR_D24: i32 = 1;
const KR_WHY_CRC: u8 = 1;
const KR_WHY_QUIET: u8 = 2;
// c/k6/k6_mod.h: the ARQ status report's byte 9 above the state (KM_RATE_*)
#[cfg(not(feature = "lean"))]
const KM_RATE_D24: u8 = 0x10;
#[cfg(not(feature = "lean"))]
const KM_RATE_AT_D24: u8 = 0x20;
#[cfg(not(feature = "lean"))]
const KM_RATE_WHY_SHIFT: u8 = 6;
const CTL_AUTO: u8 = 8;
const CTL_EMPH: u8 = 16;
const CTL_VOL: u8 = 64;
const CTL_IDLE_KEEP: u8 = 128;
/// action 4: write one receive register (byte 2 the register, 3-4 the value, high byte first;
/// register 0 drops every write); action 5: print the FSK registers (`K6 REGS`)
const CTL_ACT_REG: u8 = 4;
const CTL_ACT_REGS: u8 = 5;
/// action 6: print the stock bootloader (flash 0x0800_0000, 8 KB) as `K6 BOOT off=.. hex` lines,
/// read only, so the PC can work out how it enters UPDATE mode. Stays on the user's machine
const CTL_ACT_BOOT: u8 = 6;
/// action 7: software reset (SYSRESETREQ). The stock bootloader listens on the cable for about
/// 200 ms after every reset for a 0x42 frame and enters UPDATE on it, so a PC that sends that
/// frame after the `K6 RESET` line (tools/k6_update.py) flashes without the side keys. No PC: the
/// window passes and the pager boots again. The K6 waits 30 ms after the line, ignoring PTT
const CTL_ACT_RESET: u8 = 7;
/// Actions 8 and 9: stream mode on / off (from the next new message; RAM only, no revert)
const CTL_ACT_STREAM_ON: u8 = 8;
const CTL_ACT_STREAM_OFF: u8 = 9;
/// Action 10 (images that aren't lean): a coded 4FSK receive session on the IF (PHY G,
/// c/k6/ifpager.c, docs/k6-if-fec.md) for up to byte 4 x 2 s: the K6 stops its FSK chip's
/// reads, takes GP0 code 13's stream, decodes each packet with the soft Viterbi decoder into the
/// message store, and plays the message when the burst is over. Receive only
const CTL_ACT_IFRX: u8 = 10;
/// c/k6/ifpager.h ifp_work_bytes() is about 2.7 KB: a shared block whose decoder pool holds this
/// lends it the pool (if_session)
#[cfg(not(feature = "lean"))]
const IFP_WORK_MIN: usize = 3072;
/// Action 11 (images with the spilib feature): run the module in SPI library slot byte 4 (0-11), or
/// with 255 print the slots (c/k6/k6_lib.h)
#[cfg(feature = "spilib")]
const CTL_ACT_LIB: u8 = 11;
/// c/k6/ifpager.h IFP_RING: the session's DMA ring, in the PWM ring (rounded up to a word)
#[cfg(not(feature = "lean"))]
const IFP_RING: usize = 1216;
#[cfg(not(feature = "lean"))]
const _: () = assert!(pwm_audio::RING_BYTES >= IFP_RING + 3);
/// the session waits this long for the lead (it follows the control packet by about 0.5 s)
const IFRX_WAIT_MS: u32 = 6000;
/// streaming: the sender has stopped after this long with no good packet
const STREAM_END_TICKS: u32 = 2 * TICKS_PER_S;
/// streaming: fills further apart than this probably let the PWM ring run dry (it holds 80 ms)
const LATE_TICKS: u16 = 700;
const STALL_TICKS: u16 = 3000;
const BOOT_BASE: usize = 0x0800_0000;
const BOOT_LEN: usize = 0x2000;
/// the registers action 4 may write: AF filters, FSK mode, FSK control (low byte only: preamble
/// and sync lengths; RX/TX enable stay ours), CRC/scrambler, baud. Nothing that transmits,
/// tunes or changes the payload length the timed read counts on
/// A write to REG_5E (FIFO thresholds) also switches the receiver from timed FIFO reads to
/// interrupt reads: on each FIFO-almost-full interrupt it takes as many words as the RX
/// threshold says are waiting (see `irq_words`). Since 11-17 every rung reads by interrupt
/// (0x0203) without a write, so the revert or `--reg 0=0` leaves interrupt reads on.
const POKE_OK: [u8; 12] = [
    REG_AF_FILT, REG_FSK_CFG, REG_FSK_CTRL, REG_FSK_5C, REG_BAUD, REG_RX_BW, REG_DEMOD_GAIN,
    REG_AFC, REG_DC_FILT, REG_DISC_GAIN, REG_AF_BYPASS, REG_FIFO_THR,
];
const POKE_MAX: usize = 4;
/// An RX-done this soon (ms) after a timed packet's last word, coming after our re-arm, is
/// that packet's and is dropped without another re-arm
const STRAY_IGNORE_MS: u16 = 300;
/// After a re-arm, how long poll waits in a tight loop for the next packet (100 us ticks)
const NEXT_SYNC_WAIT_TICKS: u16 = 2500;
/// streaming: no frame is decoded in a packet's last HOLD_WORDS words (see receive_packet)
const HOLD_WORDS: usize = 2;
/// poll's burst loop goes back to the main loop after this many packets regardless
const MAX_BURST_PACKETS: u16 = 160;
const REARM_SWEEP_STEPS: u8 = 13; // 0, 10, ... 120 ms
/// 100 us ticks a byte takes at 1200 baud (66.7)
const TICKS_PER_BYTE: usize = 67;
/// The largest payload (profile XL)
const MAX_PAYLOAD: usize = 128;
/// Stream mode: the sync word, found in software at any bit offset, with up to this many
/// wrong bits (its other bit offsets, next to a 0x55 preamble or random payload, are at least
/// 5 bits away)
const STREAM_SYNC: u32 = 0x076B_3E36;
const STREAM_SYNC_ERRORS: u32 = 2;
/// Stream mode: re-arm if the FIFO interrupts stop for this long while a signal is on
/// (the chip may stop after REG_5D's length), 100 us ticks
const STREAM_STALL_TICKS: u16 = 3000;

/// Volume steps on top of the automatic level, 3 dB apart: -9 .. +12 dB.
const VOL_Q8: [i32; 8] = [91, 128, 181, 256, 362, 512, 724, 1024];
const VOL_0DB: u8 = 3;

pub struct PagerState {
    pub chip_ok: bool,
    /// sotto's codebook (c/common/sp_cb.h): sp_cb_setup's result at power-on; 1 or 2 = it plays,
    /// below 1 = no good codebook in the SPI flash, so messages are stored but not played
    pub cb: i8,
    /// typing a frequency (#), in kHz like Aura's VFO entry; RAM only, never saved
    pub freq_entry: bool,
    /// profile XL (128-byte packets, 20 frames, the power-on default) or L (64 bytes, 8 frames);
    /// only a control packet changes it (key 2 did, until a stray press broke uploads, 2026-11-13)
    pub xl: bool,
    /// D/2400, the rate ladder's fast rung (c/k6/k6_rate.h), for a session a control packet
    /// asked for (phy=D baud=2400); false: F/1800, the default. The policy in k6_rate.c brings
    /// it back to F/1800 (`K6 RATE` lines)
    pub d2400: bool,
    /// REG_2B as the chip had it (read once, restored at 1200)
    pub reg2b_base: Option<u16>,
    /// REG_43 as Aura set it for the VFO (read once, written back on every arm, so a
    /// control-packet write to it is undone by a revert)
    based: [Option<u16>; 6],
    /// the chip's RX invert bit (REG_59<10>): off (key 3 set it by hand until 2026-11-13)
    pub invert: bool,
    /// payload bits inverted in software (found from the magic byte / CRC)
    pub soft_inv: bool,
    /// FIFO words taken low byte first (found from the magic byte, spec section 9)
    pub swap: bool,
    /// the last packet's first 16 bytes as read (after the byte swap, before soft_inv)
    pub raw: [u8; 16],
    /// how many bytes the last packet gave
    pub raw_len: u8,
    /// key 6: the screen shows `raw`
    pub raw_view: bool,
    /// the last whole packet's bit errors against the spec's test message (its closest
    /// packet and reading); `swap` and `invert` here are the s and x that reading means
    pub ber: SpBiterr,
    /// sum and count of `ber.errors` since the last link change or `*`, for the mean
    pub ber_sum: u32,
    pub ber_n: u16,
    /// packets reported on the serial line, so the PC can spot a lost line
    pub reported: u16,
    /// stream mode's receiver: the last 32 bits off the air, bits seen since the search
    /// started, the payload byte being built and its bits, whether a payload is being
    /// collected (and inverted), bytes collected, and a whole payload waiting
    st_reg: u32,
    st_seen: u8,
    st_cur: u8,
    st_bits: u8,
    st_collect: bool,
    st_inv: bool,
    st_got: u8,
    st_ready: bool,
    st_buf: [u8; MAX_PAYLOAD],
    /// uptime at the last sign of a signal (a run of preamble, a sync, a good packet), the
    /// last FIFO interrupt, the last sync, and the last packet's end
    st_live: u16,
    st_last_irq: u16,
    st_t_sync: u16,
    st_t_end: u16,
    /// re-arms of the stream (the chip stopped, or an RX-done came) since entering it
    st_restarts: u16,
    /// what the chip did since the last arm, for `K6 SRST` (stream mode): uptime at the arm,
    /// bytes and FIFO interrupts, ms to its first sync interrupt (-1 none), the fewest wrong
    /// bits against the sync word in any bit position, bit positions that looked like
    /// preamble, and the first 8 bytes
    st_t_arm: u16,
    st_bytes: u16,
    st_nfull: u16,
    st_sync_irq_ms: i16,
    st_min_d: u8,
    st_pre: u16,
    st_head: [u8; 8],
    /// old key 8, bring-up: an RX-done before the whole packet came doesn't end it;
    /// keep reading on FIFO interrupts until the length or the timeout
    pub keep_after_done: bool,
    /// an RX-done with no sync while idle leaves the chip hunting instead of re-arming it:
    /// on 839924e that re-arm came 40-53 ms before every burst's first sync (a chain of them
    /// in the lead-in's 0x55), too little preamble to lock; inside a burst the late stray
    /// RX-done is already left alone and the next packet still syncs
    idle_keep: bool,
    /// idle no-sync RX-dones since the last K6 CFG line (`idlerd=`)
    idle_rd: u16,
    /// register writes from control packets, applied after configure() and undone by the 60 s
    /// revert or a key press (`poke=` in K6 REGS)
    poke: [(u8, u16); POKE_MAX],
    poke_n: u8,
    /// poke_n when ctl_prev was taken: a revert keeps the writes that were confirmed with it
    poke_prev: u8,
    /// old key 8 (with the old key 7's lengths): read the RX FIFO on a timer after the sync, one word per
    /// 13.3 ms (16 bits at 1200 baud), instead of at the chip's 0x1000 interrupt. On air that
    /// interrupt follows REG_5D's length (it comes about 14 bytes before the end of the
    /// packet), not the FIFO's fill, so a 64-byte packet overflowed the 8-word FIFO first.
    pub timed_read: bool,
    /// when to re-arm after a packet read on the timer, as an index into REARM_STEPS (ms after
    /// the payload's end); the last index sweeps 0-120 ms, a step per packet. Stays 0 (40 ms)
    /// since key 8 went (2026-11-13)
    pub rearm_mode: u8,
    rearm_sweep: u8,
    /// the delay (ms) of the re-arm after the last timed packet, and of the one before this
    /// packet (`rearm=` in K6 PKT; -1: not after a timed packet)
    rearm_last: i16,
    rearm_before: i16,
    /// the signal at the last packet's sync: RSSI in dBm (REG_67) and the noise meter
    /// (REG_65[7:0]), for `rssi=` / `noise=` in K6 PKT and K6 MOD (Jeff 2026-11-17: tell a weak
    /// signal from a garbled one on the serial log alone)
    sync_rssi: i16,
    sync_noise: u8,
    /// RSSI (dBm) on the pager's frequency at the last idle tick (`rssi=` in K6 CFG)
    idle_rssi: i16,
    /// key 9: play a message by itself once it's complete or 3 s after its last packet
    pub auto_play: bool,
    /// old key 0: a SysTick watchdog around each frame's decode that reports where the CPU is
    /// stuck (`K6 HANG pc=`); off by default until the bootloader's SysTick trampoline
    /// (UserVectors[0]) is known to work on this radio
    pub watchdog: bool,
    /// this packet's interrupts: (flags, 100 us ticks since the first, words read)
    ev: [(u16, u16, u8); 12],
    ev_n: u8,
    /// uptime at the last re-arm, for `gap=` (ms from re-arm to the next packet's first
    /// interrupt: back to back, about 145 ms; about 700 more for each packet missed)
    t_rearm: u16,
    /// the longest screen redraw so far, 100 us ticks (`draw=` ms in K6 CFG): while it runs,
    /// nothing watches the RF chip
    draw_max: u16,
    /// the last receive_packet got a packet that passed its CRC
    last_ok: bool,
    /// uptime when the last packet finished on the timer, while its own RX-done is still
    /// to come; `stray=` reports how long after that it came (ms), or -1 for not before
    /// the next packet
    t_timed_done: Option<u16>,
    stray_ms: i16,
    gap_ms: u16,
    pub emphasis: bool,
    pub vol: u8,
    /// b'-' none yet, b'P' SyncPositive, b'N' SyncNegative (then RX invert is set)
    pub last_sync: u8,
    /// last sp_packet result (2 new message, 1 added, 0 duplicate, 3 complete,
    /// -1 bad CRC, -2 bad magic), i8::MIN before the first packet
    pub last_result: i8,
    pub last_magic: u8,
    pub timeouts: u16,
    pub status: SpStatus,
    pub store_frames: u16,
    pub play_pending: bool,
    pub cal_pending: bool,
    /// a control record (magic, seq, mask, values, var|vol<<4, action) waiting for poll
    ctl: Option<[u8; 6]>,
    /// a coded 4FSK receive session asked for (control action 10): its longest, ms; 0 none
    ifrx_ms: u32,
    /// ... its flags (control byte 3: 1 rate 2/3, 2 no decoding, a bench test)
    #[cfg(not(feature = "lean"))]
    ifrx_flags: u8,
    /// seq of the last control record applied, -1 none
    ctl_seq: i16,
    /// the settings before a control packet's change (xl, d2400, idle_keep), until a good
    /// packet keeps the change; CTL_REVERT_TICKS without one puts them back (D/2400: k6_rate.c's
    /// 10 s, KR_REVERT_TICKS)
    ctl_prev: Option<(bool, bool, bool)>,
    ctl_wait: u32,
    pub cal_result: u8, // 0 none, b'K' sent, b'X' TX refused (band lock / not FM)
    /// played this message (auto-play only once per message)
    played_id: i16,
    idle_ticks: u32,
    /// 100 us ticks since the last K6 CFG line (one goes out every CFG_EVERY_TICKS when idle)
    cfg_ticks: u32,
    last_tick: u16,
    /// decoder time per 20 ms frame in the last playback, 100 us units
    pub dec_max: u16,
    pub dec_mean_x10: u16,
    pub played_frames: u16,
    pub aborted: bool,
    /// unused stack + heap RAM below the deepest stack use seen, bytes
    pub ram_free: u32,
    /// stream mode (sotto 1200 default; control actions 8 and 9): play while later groups
    /// arrive, through the store as a ring
    pub stream_play: bool,
    /// the first group is in: start streaming after this packet
    stream_start: bool,
    /// the last stream's frames whose group wasn't in yet, and fills more than 70 ms apart
    waits: u16,
    late: u16,
    /// fills the DMA caught up with: the speaker played stale or half-written samples
    under: u16,
    overruns: u16,
}

impl PagerState {
    /// The re-arm delay after this timed packet (ms after its payload's end): REARM_STEPS[rearm_mode],
    /// or the sweep's next. 40 ms measured best (2026-11-04).
    fn rearm_delay_ms(&mut self) -> u8 {
        match REARM_STEPS.get(self.rearm_mode as usize) {
            Some(&ms) => ms,
            None => {
                let ms = self.rearm_sweep * 10;
                self.rearm_sweep = (self.rearm_sweep + 1) % REARM_SWEEP_STEPS;
                ms
            }
        }
    }

    fn rearm_label(&self) -> i16 {
        // -1 in the CFG line: the sweep
        REARM_STEPS.get(self.rearm_mode as usize).map_or(-1, |&ms| ms as i16)
    }

    pub const fn new() -> Self {
        PagerState {
            chip_ok: false,
            cb: 0,
            xl: true, // XL from power-on: every sotto 1200/600/400 send uses it (2026-11-09)
            d2400: false,
            reg2b_base: None,
            based: [None; 6],
            invert: false,
            soft_inv: false,
            raw: [0; 16],
            raw_len: 0,
            raw_view: false,
            ber: ffi::NO_BITERR,
            ber_sum: 0,
            ber_n: 0,
            reported: 0,
            st_reg: 0,
            st_seen: 0,
            st_cur: 0,
            st_bits: 0,
            st_collect: false,
            st_inv: false,
            st_got: 0,
            st_ready: false,
            st_buf: [0; MAX_PAYLOAD],
            st_live: 0,
            st_last_irq: 0,
            st_t_sync: 0,
            st_t_end: 0,
            st_restarts: 0,
            st_t_arm: 0,
            st_bytes: 0,
            st_nfull: 0,
            st_sync_irq_ms: -1,
            st_min_d: 32,
            st_pre: 0,
            st_head: [0; 8],
            keep_after_done: false,
            idle_keep: true, // on air 2026-11-04 (0438507): first packet 20/20 vs 7/20
            idle_rd: 0,
            poke: [(0, 0); POKE_MAX],
            poke_n: 0,
            poke_prev: 0,
            timed_read: true,
            rearm_mode: 0,
            rearm_sweep: 0,
            rearm_last: -1,
            rearm_before: -1,
            sync_rssi: 0,
            sync_noise: 0,
            idle_rssi: 0,
            auto_play: true,
            watchdog: false,
            ev: [(0, 0, 0); 12],
            ev_n: 0,
            t_rearm: 0,
            draw_max: 0,
            last_ok: false,
            t_timed_done: None,
            stray_ms: -1,
            gap_ms: 0,
            // The FD6818's FIFO words go low byte first on air (Jeff's first test burst,
            // 2026-11-04: all four packets bit-exact once swapped), so RX swaps by default
            swap: true,
            emphasis: true,
            vol: VOL_0DB,
            last_sync: b'-',
            last_result: i8::MIN,
            last_magic: 0,
            timeouts: 0,
            status: ffi::EMPTY,
            store_frames: 0,
            play_pending: false,
            cal_pending: false,
            ctl: None,
            ctl_seq: -1,
            ctl_prev: None,
            ctl_wait: 0,
            freq_entry: false,
            cal_result: 0,
            played_id: -1,
            idle_ticks: 0,
            cfg_ticks: 0,
            last_tick: 0,
            dec_max: 0,
            dec_mean_x10: 0,
            played_frames: 0,
            aborted: false,
            ram_free: 0,
            stream_play: false,
            stream_start: false,
            ifrx_ms: 0,
            #[cfg(not(feature = "lean"))]
            ifrx_flags: 0,
            waits: 0,
            late: 0,
            under: 0,
            overruns: 0,
        }
    }

    pub fn vol_db(&self) -> i8 {
        (self.vol as i8 - VOL_0DB as i8) * 3
    }

    /// REG_5D's packet-length field: bytes - 1, the BK4819 way. Key 7 used to pick words - 1
    /// (and before that two stream-mode values); one stray press halved the length and the K6
    /// rejected every chunk of an upload (2026-11-13), so the key is gone and this is fixed.
    fn len_field(&self) -> u16 {
        (self.payload_len() as u16 - 1) << 8
    }

    /// bits ORed into REG_2B for the rung: the RX high-pass off at D/2400
    fn af_bits(&self) -> u16 {
        if self.d2400 {
            AF_HPF_OFF
        } else {
            0
        }
    }

    /// Stream mode (old key 7 settings 2-3) is compiled out: it never received on air (f190a46,
    /// 0/16), and its flash pays for REG_43 in the control-packet registers. The code stays
    /// for reference; the compiler drops it.
    fn stream(&self) -> bool {
        false
    }

    fn payload_len(&self) -> usize {
        if self.xl {
            MAX_PAYLOAD
        } else {
            64
        }
    }

    /// Log a receive event for the PKT line; when the list is full the last entry holds the
    /// latest event, so a packet's end is always shown
    fn ev_push(&mut self, e: (u16, u16, u8)) {
        let k = (self.ev_n as usize).min(self.ev.len() - 1);
        self.ev[k] = e;
        if (self.ev_n as usize) < self.ev.len() {
            self.ev_n += 1;
        }
    }

    /// The REG_5E write in force, if any (a later write to the same register wins)
    fn fifo_thr_poke(&self) -> Option<u16> {
        let mut v = None;
        for &(r, x) in &self.poke[..self.poke_n as usize] {
            if r == REG_FIFO_THR {
                v = Some(x);
            }
        }
        v
    }

    // D/2400 read by time until 11-17: those reads slipped after about 5 XL packets of a long
    // upload (stockfont, 9/25, then CRC fallback); with REG_5E 0x0203 the same upload took
    // 25/25 at the same level (docs/k6-fsk24.md), so D/2400 reads by interrupt as F/1800 does
    // (the timed-read code stays, unreached, for a later probe)

    /// The REG_5E value in force: a control-packet write, else 0x0203 (interrupt reads, on
    /// every rung; the timed reads with REG_5E = FIFO_THR are no longer reached)
    fn fifo_thr(&self) -> Option<u16> {
        self.fifo_thr_poke().or(Some(FIFO_THR_IRQ))
    }

    /// Timed FIFO reads: never now, as fifo_thr always gives a value (see above)
    fn timed(&self) -> bool {
        self.timed_read && self.fifo_thr().is_none()
    }

    /// Words to take on each FIFO-almost-full interrupt. Which REG_5E field is the RX threshold
    /// is unsettled (our 0x0204 has 4 in <2:0> and 64 in <9:3>; UV-K5 firmware writes
    /// (64 << 3) | 1 as "the almost full threshold"), so: <2:0> when it isn't 0, else <9:3>
    fn irq_words(&self) -> usize {
        match self.fifo_thr() {
            Some(v) if v & 7 != 0 => (v & 7) as usize,
            Some(v) => (((v >> 3) & 0x7F) as usize).max(1),
            None => 4,
        }
    }

    /// The REG_72 bit rate in force: a control-packet write wins over the PHY/baud setting
    /// (F 1800, or D 2400 by control packet; `--reg 72=3065` gets F/1200 back).
    /// The rate is REG_72 / 10.3244; 1800 (0x4898) and 2000 (0x50A9) were written on air
    /// (2026-11-05: F/1800 12/12, F/2000 10/12, every decoded packet 0 bit errors)
    fn reg72(&self) -> u16 {
        let mut v = if self.d2400 { BAUD_2400 } else { BAUD_1800 };
        for &(r, x) in &self.poke[..self.poke_n as usize] {
            if r == REG_BAUD && x != 0 {
                v = x;
            }
        }
        v
    }

    /// (REG_58, REG_72, TX gain) for the PHY and baud (before control-packet writes)
    fn fsk_cfg(&self) -> (u16, u16, u16) {
        if self.d2400 {
            (CFG_D_2400, BAUD_2400, GAIN_D)
        } else if self.reg72() == BAUD_1200 {
            (CFG_F_1200, BAUD_1200, GAIN_F) // a 72=3065 write: the old F/1200 recipe
        } else {
            (CFG_F_1800, BAUD_1800, GAIN_F)
        }
    }

    /// The bit rate on air, from REG_72
    pub fn baud(&self) -> u16 {
        ((self.reg72() as u32 * 1200 + BAUD_1200 as u32 / 2) / BAUD_1200 as u32) as u16
    }

    /// 100 us ticks per byte on air at the REG_72 rate (66.67 at 1200), rounded up, so every
    /// paced wait follows the rate and a read by time never takes a word that isn't in yet: at
    /// 2400 the truncated 33 (real 33.3) made the stalled-interrupt read take a word early, the
    /// empty FIFO repeated the last one, and the rest of the packet shifted (b1f0492, 9/9 garbled)
    fn tpb(&self) -> usize {
        let r = 3 * self.reg72() as u32;
        ((200 * BAUD_1200 as u32 + r - 1) / r) as usize
    }
}

// ---- RAM report: paint the free stack, look for the deepest use later ----

const PAINT: u32 = 0xA5A5_A5A5;
extern "C" {
    // cortex-m-rt: the end of .uninit, the last of the static RAM, where the free RAM (the
    // stack's) starts. Not __sheap: link.x sets that to wherever the location counter is after
    // .uninit, and memory.x inserts .ram0 there, so from 2026-11-19 it was the end of RAM0,
    // 0x20000E98. The first arena image (f541fbd) then painted from there up to the stack,
    // UserVectors and every static on the way, at VOICE MSG's first entry, and hung at every boot
    // (docs/k6-ram0.md)
    static __euninit: u32;
}

fn paint_free_ram() {
    cortex_m::interrupt::free(|_| {
        let start = (addr_of!(__euninit) as usize + 3) & !3;
        let end = cortex_m::register::msp::read() as usize - 128;
        let mut p = start;
        while p < end {
            unsafe { write_volatile(p as *mut u32, PAINT) };
            p += 4;
        }
    });
}

/// The stack reached (or passed) the bottom of free RAM: whatever lies below it, the App's statics
/// and the arena, may be overwritten, so nothing that trusts them may run, above all a key-up.
/// 2026-11-08 16:53Z: dmrrx returned with stack_free=0, the ARQ report that followed printed
/// garbage and the K6 hung with its transmitter keyed on 440.850 until Jeff switched it off. A
/// reset puts the radio right: boot soft-resets the FD6818 (transmitter off) before anything else.
const RAM_MIN_FREE: u32 = 64;

fn ram_check(sfree: u32) {
    if sfree >= RAM_MIN_FREE {
        return;
    }
    rl!("K6 RAM stack ran out (free={} B): rebooting, no report, no key-up", sfree);
    crate::hal::uart::flush();
    for _ in 0..200_000 {
        cortex_m::asm::nop();
    }
    crate::hal::txdog::reset();
}

fn free_ram() -> u32 {
    let start = (addr_of!(__euninit) as usize + 3) & !3;
    let sp = cortex_m::register::msp::read() as usize;
    let mut p = start;
    while p < sp && unsafe { read_volatile(p as *const u32) } == PAINT {
        p += 4;
    }
    (p - start) as u32
}

// ---- sotto's codebook in the SPI flash (c/common/sp_cb.h) ----
//
// Built with K6_CB=spi or install (tools/k6_build.sh), the decoder reads each 20-byte codebook
// vector from the SPI flash at 0x100000 through these three functions instead of a 30 KB table
// in the MCU's flash. They run in the main loop only (the decoder, sp_cb_setup), like all of
// Aura's other uses of the flash. The time spent is summed across calls (100 us ticks wrap
// every 6.5 s, and an install takes a few seconds).

static mut CB_NOR: *mut crate::drivers::norflash::NorFlash<'static> = core::ptr::null_mut();
static mut CB_T_LAST: u16 = 0;
static mut CB_TICKS: u32 = 0;

unsafe fn cb_clock() {
    let now = uptime::now();
    CB_TICKS = CB_TICKS.wrapping_add(now.wrapping_sub(CB_T_LAST) as u32);
    CB_T_LAST = now;
}

#[no_mangle]
unsafe extern "C" fn sp_flash_read(addr: u32, buf: *mut u8, n: u32) {
    let b = core::slice::from_raw_parts_mut(buf, n as usize);
    if CB_NOR.is_null() {
        b.fill(0xFF);
    } else {
        (*CB_NOR).read_bytes(addr, b);
    }
}

#[no_mangle]
unsafe extern "C" fn sp_flash_erase4k(addr: u32) {
    cb_clock();
    if !CB_NOR.is_null() {
        (*CB_NOR).erase_sector(addr);
    }
}

#[no_mangle]
unsafe extern "C" fn sp_flash_write(addr: u32, buf: *const u8, n: u32) {
    cb_clock();
    if !CB_NOR.is_null() {
        (*CB_NOR).write_bytes(addr, core::slice::from_raw_parts(buf, n as usize));
    }
}

/// Power-on: check (and with K6_CB=install, write once) the codebook image, then time 1000
/// random vector reads. Serial: `K6 CB r=.. mode=.. crc=.. ms=..` and `K6 CB bench ..`; the
/// table build (mode 0) says `K6 CB r=1 mode=0` and touches no flash. The sotto 600 / 400
/// images then add `K6 VLR r=.. crc=.. ms=..` for their own codebooks (same r codes).
fn cb_setup(app: &mut App) {
    unsafe {
        CB_NOR = &mut app.storage.norflash as *mut _;
        CB_T_LAST = uptime::now();
        CB_TICKS = 0;
    }
    let mode = unsafe { ffi::sp_cb_mode() };
    let r = unsafe { ffi::sp_cb_setup() };
    let dt = unsafe {
        cb_clock();
        CB_TICKS
    };
    app.pager.cb = r as i8;
    let crc = unsafe { ffi::sp_cb_expected() };
    rl!("K6 CB r={} mode={} crc={:08x} ms={}.{}", r, mode, crc, dt / 10, dt % 10);
    if mode != 0 && r > 0 {
        const N: u32 = 1000;
        let t0 = uptime::now();
        let sum = unsafe { ffi::sp_cb_bench(N) };
        let t = uptime::now().wrapping_sub(t0) as u32;
        rl!(
            "K6 CB bench n={} ms={}.{} us_per_read={} sum={:08x} reads={}",
            N,
            t / 10,
            t % 10,
            t * 100 / N,
            sum,
            unsafe { ffi::sp_cb_reads() }
        );
    }
    // sotto 600 / 400's codebooks (c/common/sp_vlr.h, SPI flash 0x108000): checked, or written once
    // by the install image, in the images that play them. 0 = this image doesn't: no line.
    let t0 = uptime::now();
    let v = unsafe { ffi::sp_vlr_setup() };
    if v != 0 {
        let t = uptime::now().wrapping_sub(t0) as u32;
        let crc = unsafe { ffi::sp_vlr_expected() };
        rl!("K6 VLR r={} crc={:08x} ms={}.{}", v, crc, t / 10, t % 10);
    }
}

// ---- mode entry and exit ----

#[inline(never)] // its locals only while it runs, not in main's frame for good
pub(super) fn enter(app: &mut App, syst: &mut SYST) {
    app.mode = Mode::Pager;
    app.input.clear();
    if app.pager.store_frames == 0 {
        // first entry since power-on
        default_freq(app);
        cb_setup(app);
        // the decoder's pool is the shared block's tail (hal::arena); the lean images' library
        // keeps its own (and they have no flash for the report)
        #[cfg(not(feature = "lean"))]
        unsafe {
            ffi::sp_dec_pool(arena::dec(), arena::DEC_BYTES as u32)
        };
        #[cfg(not(feature = "lean"))]
        let need = unsafe { ffi::sp_dec_need() };
        #[cfg(not(feature = "lean"))]
        rl!(
            "K6 ARENA bytes={} at {:#010x} ram0={} store={} ring={} dec={} need={}{}",
            arena::ARENA_BYTES,
            arena::base() as usize,
            crate::hal::ram0::used(),
            STORE_BYTES,
            arena::RING_BYTES,
            arena::DEC_BYTES,
            need,
            if need as usize > arena::DEC_BYTES { " SHORT" } else { "" }
        );
        #[cfg(not(feature = "lean"))]
        crate::hal::txdog::report();
        crate::ui::glyph::report();
        #[cfg(feature = "spilib")]
        lib_list();
        app.pager.store_frames =
            unsafe { ffi::sp_init(arena::base(), STORE_BYTES as u32) } as u16;
        app.pager.stream_play = unsafe { ffi::sp_stream() } != 0;
    }
    app.sync_watching_to_master(syst);
    app.pager.chip_ok = app.chip_id == FD6818_CHIP_ID;
    // back in VOICE MSG (from EXPLORE, say): on the default rung; a D/2400 session is asked for
    // again by its sender
    app.pager.d2400 = false;
    app.pager.ctl_prev = None;
    refresh_status(app);
    app.pager.last_tick = uptime::now();
    paint_free_ram();
    if app.pager.chip_ok {
        arm(app, syst);
    }
    report_cfg(app, "enter");
}

/// Pager-only image: start on DEFAULT_FREQ_HZ (RAM only, like `#`); the full image keeps the VFO.
#[cfg(feature = "pager-only")]
fn default_freq(app: &mut App) {
    let s = &mut app.sides[app.master];
    s.vfo_chan = ChVfoMode::Vfo;
    s.rx_freq_hz = DEFAULT_FREQ_HZ;
    s.refresh_cfg_freqs();
}
#[cfg(not(feature = "pager-only"))]
fn default_freq(_app: &mut App) {}

fn exit(app: &mut App, syst: &mut SYST) {
    disarm(app, syst);
    rx_led(false);
    app.mode = Mode::Standby;
    app.reset_key_idle();
    app.sync_watching_to_master(syst);
}

fn refresh_status(app: &mut App) {
    unsafe { ffi::sp_status(&mut app.pager.status) };
}

// ---- FSK modem ----

fn configure(app: &mut App, syst: &mut SYST) {
    let p = &app.pager;
    let var2b = p.af_bits();
    let (cfg, baud, gain) = p.fsk_cfg();
    let len_field = p.len_field();
    let fifo_thr = p.fifo_thr().unwrap_or(FIFO_THR);
    let filt_or = var2b;
    let known = app.pager.reg2b_base;
    let base = match known {
        Some(b) => b,
        None => {
            let b = app.radio.fd6818.reg_read(syst, REG_AF_FILT) & !AF_DC_CANCEL;
            app.pager.reg2b_base = Some(b);
            b
        }
    };
    for (i, &r) in BASED.iter().enumerate() {
        let v = match app.pager.based[i] {
            Some(b) => b,
            None => {
                let b = app.radio.fd6818.reg_read(syst, r);
                app.pager.based[i] = Some(b);
                b
            }
        };
        app.radio.fd6818.reg_write(syst, r, v);
    }
    let fd = &mut app.radio.fd6818;
    fd.reg_write(syst, REG_AF_FILT, base | filt_or);
    fd.reg_write(syst, REG_IRQ_EN, 0);
    fd.reg_write(syst, REG_FSK_CFG, cfg);
    fd.reg_write(syst, REG_BAUD, baud);
    fd.reg_write(syst, REG_SYNC01, 0x076B);
    fd.reg_write(syst, REG_SYNC23, 0x3E36);
    fd.reg_write(syst, REG_FSK_5C, CRC_OFF);
    fd.reg_write(syst, REG_FSK_LEN, len_field);
    fd.reg_write(syst, REG_FIFO_THR, fifo_thr);
    fd.reg_write(syst, REG_TX_GAIN, gain);
    let p = &app.pager;
    for &(r, v) in &p.poke[..p.poke_n as usize] {
        if r != REG_FSK_CTRL {
            app.radio.fd6818.reg_write(syst, r, v);
        }
    }
}

fn rx_ctrl(app: &App) -> u16 {
    let p = &app.pager;
    let mut base = CTRL_BASE;
    for &(r, v) in &p.poke[..p.poke_n as usize] {
        if r == REG_FSK_CTRL {
            base = v & 0x00FF;
        }
    }
    base | if p.invert { CTRL_RX_INVERT } else { 0 }
}

/// Clear the RX FIFO and listen for the next packet.
fn rearm_rx(app: &mut App, syst: &mut SYST) {
    let ctrl = rx_ctrl(app);
    let fd = &mut app.radio.fd6818;
    fd.reg_write(syst, REG_FSK_CTRL, ctrl | CTRL_CLR_RX);
    fd.reg_write(syst, REG_FSK_CTRL, ctrl | CTRL_RX_EN);
    fd.reg_write(syst, REG_IRQ_FLAGS, 0);
    fd.reg_write(syst, REG_IRQ_EN, IRQ_RX_DONE | IRQ_RX_FULL | IRQ_RX_SYNC);
    soft_clear();
    unsafe { Y_GUARD = (app.pager.irq_words() * app.pager.tpb()) as u16 }; // half of N words
    let p = &mut app.pager;
    p.t_rearm = uptime::now();
    p.st_last_irq = p.t_rearm;
    p.st_reg = 0;
    p.st_seen = 0;
    p.st_collect = false;
    p.st_ready = false;
    p.st_t_arm = p.t_rearm;
    p.st_bytes = 0;
    p.st_nfull = 0;
    p.st_sync_irq_ms = -1;
    p.st_min_d = 32;
    p.st_pre = 0;
}

fn arm(app: &mut App, syst: &mut SYST) {
    // the speaker stays off and the RF chip's audio muted: the FSK bursts are
    // data, and the PWM path shares the amplifier
    app.radio.set_speaker(false);
    app.radio
        .fd6818
        .set_af_out(syst, AfOutState::Mute, true, Modulation::Fm);
    configure(app, syst);
    rearm_rx(app, syst);
}

fn disarm(app: &mut App, syst: &mut SYST) {
    let fd = &mut app.radio.fd6818;
    fd.reg_write(syst, REG_IRQ_EN, 0);
    fd.reg_write(syst, REG_FSK_CTRL, CTRL_BASE);
    fd.reg_write(syst, REG_IRQ_FLAGS, 0);
    fd.reg_write(syst, REG_TX_GAIN, 0);
    fd.reg_write(syst, REG_FSK_CFG, 0); // as Aura's exit_fsk_mode / exit_mdc1200_mode
}

/// Pending interrupt causes, cleared, or 0.
///
/// The UV-K5 BK4819 drivers (and the FD6818 guide) write REG_02 first and then read it: the
/// write latches the pending causes for the read. Read first, as here since the start, the
/// causes may be the previous interrupt's, one event late, which fits the stale RX-done at the
/// start of every burst and a sync seen about two words late (on air, 2026-11-05). The timed
/// reads are tuned to that lateness (their lead words), so the new order goes with the
/// interrupt reads only, and a revert brings the old one back.
fn take_irq(app: &mut App, syst: &mut SYST) -> u16 {
    let clear_first = !app.pager.timed();
    let irq_n = app.pager.irq_words();
    let fd = &mut app.radio.fd6818;
    let mut flags = 0;
    if clear_first {
        flags = chip_irq(fd, syst);
    } else if fd.reg_read(syst, REG_STATUS) & 1 != 0 {
        flags = fd.reg_read(syst, REG_IRQ_FLAGS);
        fd.reg_write(syst, REG_IRQ_FLAGS, 0);
    }
    // what the decoder's yields took while a frame decoded (streaming): their flags, and the
    // words in the soft FIFO, which come before the chip's
    unsafe {
        if Y_PENDING == 0 && Y_SOFT_N == 0 {
            return flags;
        }
        if flags & IRQ_RX_FULL != 0 {
            soft_pull(fd, syst, irq_n); // behind the soft FIFO's words, in order
        }
        flags = (flags | Y_PENDING) & !IRQ_RX_FULL;
        Y_PENDING = 0;
        if Y_SOFT_N as usize >= irq_n {
            flags |= IRQ_RX_FULL;
        }
    }
    flags
}

// ---- the FIFO while a frame decodes (stream mode) ----
//
// A decode holds the CPU for 14-21 ms and the RF chip's FIFO holds 8 words (71 ms at 1800
// baud, its interrupt at 3): the decoder calls fifo_yield (sp_set_yield) after every ~0.3-1 ms
// of work, and every 2 ms that takes the chip's interrupt as take_irq would, moving the words
// of an almost-full interrupt into this soft FIFO. take_irq and read_words then see them as if
// they came off the chip just now.

static mut Y_FD: *mut crate::drivers::fd6818::Fd6818<'static> = core::ptr::null_mut();
static mut Y_SYST: *mut SYST = core::ptr::null_mut();
static mut Y_LAST: u16 = 0;
static mut Y_IRQN: u8 = 3;
static mut Y_PENDING: u16 = 0;
const SOFT_LEN: usize = 16;
#[link_section = ".ram0"] // hal::ram0
static mut Y_SOFT: [u16; SOFT_LEN] = [0; SOFT_LEN];
static mut Y_SOFT_R: u8 = 0;
static mut Y_SOFT_N: u8 = 0;
static mut Y_SERVICED: u16 = 0;
// How often a decode's yields look at the chip: each look is a bit-banged register read
// (about 0.1 ms), and the FIFO's almost-full comes 4 words (36 ms at 1800 baud) before it
// overflows, so every 6 ms is early enough. Every 2 ms, as until 2026-11-08, cost the hiss
// band image about 1 ms of each 20 ms frame.
const YIELD_TICKS: u16 = 60;

fn soft_clear() {
    unsafe {
        Y_SOFT_N = 0;
        Y_SOFT_R = 0;
        Y_PENDING = 0;
        Y_FULL_ON = false;
        Y_SYNC_ON = false;
    }
}

// A second FIFO-almost-full interrupt sometimes comes 1-2 ms after a real one (on air,
// 2026-11-05, f548d3e: 3 copies of the FIFO's last word read into the packet, which failed its
// CRC, and its true last 3 words left for the next packet, one word off). The next N words take
// N word times to come, so an almost-full within half of that of the last one is dropped; a
// real one dropped by mistake is caught by the stalled-interrupt read by time.
static mut Y_FULL_T: u16 = 0;
static mut Y_FULL_ON: bool = false;
static mut Y_GUARD: u16 = 0;
static mut Y_SPUR: u16 = 0;
// when a decoder yield took the sync interrupt (the packet's words count from there)
static mut Y_SYNC_T: u16 = 0;
static mut Y_SYNC_ON: bool = false;

#[inline(never)]
/// The chip's pending interrupt causes, cleared (interrupt reads' order), without a spurious
/// almost-full; 0 if none
fn chip_irq(fd: &mut crate::drivers::fd6818::Fd6818<'static>, syst: &mut SYST) -> u16 {
    if fd.reg_read(syst, REG_STATUS) & 1 == 0 {
        return 0;
    }
    fd.reg_write(syst, REG_IRQ_FLAGS, 0);
    let flags = fd.reg_read(syst, REG_IRQ_FLAGS);
    if flags & IRQ_RX_FULL == 0 {
        return flags;
    }
    let now = uptime::now();
    unsafe {
        if Y_FULL_ON && now.wrapping_sub(Y_FULL_T) < Y_GUARD {
            Y_SPUR = Y_SPUR.wrapping_add(1);
            return flags & !IRQ_RX_FULL;
        }
        Y_FULL_T = now;
        Y_FULL_ON = true;
    }
    flags
}

/// n words off the chip's FIFO into the soft FIFO (raw, before the byte swap)
fn soft_pull(fd: &mut crate::drivers::fd6818::Fd6818<'static>, syst: &mut SYST, n: usize) {
    for _ in 0..n {
        unsafe {
            if Y_SOFT_N as usize >= SOFT_LEN {
                return;
            }
            let w = fd.reg_read(syst, REG_FIFO);
            Y_SOFT[(Y_SOFT_R as usize + Y_SOFT_N as usize) % SOFT_LEN] = w;
            Y_SOFT_N += 1;
        }
    }
}

#[inline(never)] // the decoder's callback, also called once in stream_begin
unsafe extern "C" fn fifo_yield() {
    let now = uptime::now();
    if now.wrapping_sub(Y_LAST) < YIELD_TICKS || Y_FD.is_null() {
        return;
    }
    Y_LAST = now;
    let fd = &mut *Y_FD;
    let syst = &mut *Y_SYST;
    let flags = chip_irq(fd, syst);
    if flags == 0 {
        return;
    }
    Y_SERVICED = Y_SERVICED.wrapping_add(1);
    if flags & IRQ_RX_SYNC != 0 && !Y_SYNC_ON {
        Y_SYNC_T = now;
        Y_SYNC_ON = true;
    }
    Y_PENDING |= flags & !IRQ_RX_FULL;
    if flags & IRQ_RX_FULL != 0 {
        soft_pull(fd, syst, Y_IRQN as usize);
    }
}

fn read_words(app: &mut App, syst: &mut SYST, buf: &mut [u8], got: &mut usize, n: usize) {
    let swap = app.pager.swap;
    let fd = &mut app.radio.fd6818;
    for _ in 0..n {
        let w = unsafe {
            if Y_SOFT_N > 0 {
                let w = Y_SOFT[Y_SOFT_R as usize];
                Y_SOFT_R = (Y_SOFT_R + 1) % SOFT_LEN as u8;
                Y_SOFT_N -= 1;
                w
            } else {
                fd.reg_read(syst, REG_FIFO)
            }
        };
        let w = if swap { w.swap_bytes() } else { w };
        if *got + 2 <= buf.len() {
            buf[*got] = (w >> 8) as u8; // after the swap: the first byte on air
            buf[*got + 1] = w as u8;
        }
        *got += 2;
    }
}

// ---- stream mode (the old key 7's third and fourth lengths) ----
//
// With REG_5D at 0xFF00 the FD6818 passed everything it demodulated on air (2026-11-04):
// preamble, sync word and payload, with a FIFO interrupt every 4 words (53 ms at 1200 baud)
// and no sync or RX-done interrupt; one 64-byte packet came through with no bit errors. So
// here the chip is armed once and the firmware does the rest: it looks for the sync word in
// every bit position, takes the next 64 (or 128) bytes, and goes straight back to looking.
// Packets can then come back to back with only a sync word between them (`modem.k6
// --stream`), with no preamble and no re-arm between packets. Not yet tried on air: whether
// the chip keeps streaming past REG_5D's length (an RX-done or a stall here re-arms it and
// counts `restarts=`), and whether it streams in noise.

/// One byte off the air (first on air first, after the FIFO byte swap), a bit at a time:
/// through the sync search, or into the payload after a sync. A whole payload sets st_ready,
/// and the search starts again with the next bit.
fn stream_byte(p: &mut PagerState, byte: u8, len: usize, now: u16) {
    for i in (0..8).rev() {
        let bit = (byte >> i) & 1;
        if p.st_collect {
            p.st_cur = (p.st_cur << 1) | (bit ^ p.st_inv as u8);
            p.st_bits += 1;
            if p.st_bits == 8 {
                p.st_buf[p.st_got as usize] = p.st_cur;
                p.st_got += 1;
                p.st_bits = 0;
                if p.st_got as usize >= len {
                    p.st_collect = false;
                    p.st_ready = true;
                    p.st_reg = 0;
                    p.st_seen = 0;
                }
            }
            continue;
        }
        p.st_reg = (p.st_reg << 1) | bit as u32;
        if p.st_seen < 32 {
            p.st_seen += 1;
            if p.st_seen < 32 {
                continue;
            }
        }
        // wrong bits against the sync; 32 - d against the inverted sync
        let d = (p.st_reg ^ STREAM_SYNC).count_ones();
        p.st_min_d = p.st_min_d.min(d.min(32 - d) as u8);
        if d <= STREAM_SYNC_ERRORS || 32 - d <= STREAM_SYNC_ERRORS {
            p.st_collect = true;
            p.st_inv = d > STREAM_SYNC_ERRORS;
            p.st_got = 0;
            p.st_bits = 0;
            p.st_t_sync = now;
            p.st_live = now;
            p.ev_n = 0;
        } else if p.st_reg == 0x5555_5555 || p.st_reg == 0xAAAA_AAAA {
            p.st_live = now; // preamble: a packet is coming
            p.st_pre = p.st_pre.saturating_add(1);
        }
    }
}

/// A stream-mode interrupt: 4 FIFO words through the bit search, any payload they finish
/// to the pager. An RX-done means the chip stopped (REG_5D's length): listen again.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn stream_irq(app: &mut App, syst: &mut SYST, flags: u16) {
    let now = uptime::now();
    app.pager.st_last_irq = now;
    if flags & IRQ_RX_SYNC != 0 && app.pager.st_sync_irq_ms < 0 {
        app.pager.st_sync_irq_ms = (now.wrapping_sub(app.pager.st_t_arm) / 10) as i16;
    }
    if flags & IRQ_RX_FULL != 0 {
        let len = app.pager.payload_len();
        let p = &mut app.pager;
        p.st_nfull = p.st_nfull.saturating_add(1);
        let k = p.ev_n as usize;
        if p.st_collect && k < p.ev.len() {
            p.ev[k] = (flags, now.wrapping_sub(p.st_t_sync), 4);
            p.ev_n += 1;
        }
        for _ in 0..4 {
            let w = app.radio.fd6818.reg_read(syst, REG_FIFO);
            let w = if app.pager.swap { w.swap_bytes() } else { w };
            for byte in [(w >> 8) as u8, w as u8] {
                let p = &mut app.pager;
                if (p.st_bytes as usize) < p.st_head.len() {
                    p.st_head[p.st_bytes as usize] = byte;
                }
                p.st_bytes = p.st_bytes.saturating_add(1);
                stream_byte(&mut app.pager, byte, len, now);
                if app.pager.st_ready {
                    app.pager.st_ready = false;
                    stream_packet(app, len);
                }
            }
        }
    }
    if flags & IRQ_RX_DONE != 0 {
        app.pager.st_restarts = app.pager.st_restarts.saturating_add(1);
        report_srst(app, 'D');
        rearm_rx(app, syst);
    }
}

/// Stream mode, at each re-arm: what the chip gave since the last one (why: D = its RX-done,
/// S = a stall with a signal on, I = a stall while idle).
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn report_srst(app: &App, why: char) {
    let p = &app.pager;
    let h = &p.st_head;
    rl!(
        "K6 SRST why={} ms={} bytes={} full={} syncirq={} mind={} pre={} collect={} head={:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x} n={}",
        why,
        uptime::now().wrapping_sub(p.st_t_arm) / 10,
        p.st_bytes,
        p.st_nfull,
        p.st_sync_irq_ms,
        p.st_min_d,
        p.st_pre,
        if p.st_collect { p.st_got as i16 } else { -1 },
        h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7],
        p.st_restarts
    );
}

/// A whole payload from the stream.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn stream_packet(app: &mut App, len: usize) {
    let mut buf = [0u8; MAX_PAYLOAD];
    buf[..len].copy_from_slice(&app.pager.st_buf[..len]);
    let p = &mut app.pager;
    p.last_sync = if p.st_inv { b'w' } else { b'W' };
    let now = uptime::now();
    // ms from the last packet's end to this one's sync, as the interrupts saw them
    p.gap_ms = p.st_t_sync.wrapping_sub(p.st_t_end) / 10;
    p.st_t_end = now;
    p.stray_ms = -1;
    p.rearm_before = -1;
    let swap = p.swap;
    take_payload(app, None, &mut buf, len, len, swap, true);
    if app.pager.last_ok {
        app.pager.st_live = now;
    }
}

/// poll for stream mode. While a signal is on (a preamble, a sync or a good packet within the
/// last 1.5 packets, or a payload half in), it stays here and checks the chip every 0.5 ms:
/// the main loop's screen redraw (41 ms) would let the 8-word FIFO overflow at 2400 baud. In
/// noise it goes back after one look, so keys and the power switch keep working.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn poll_stream(app: &mut App, syst: &mut SYST) {
    let len = app.pager.payload_len();
    let live = ((len + 8) * app.pager.tpb() * 3 / 2) as u16;
    let t_in = uptime::now();
    let good_in = app.pager.status.good_packets;
    let mut n: u32 = 0;
    loop {
        let flags = take_irq(app, syst);
        if flags != 0 {
            stream_irq(app, syst, flags);
        }
        let now = uptime::now();
        let p = &app.pager;
        let on = p.st_collect || now.wrapping_sub(p.st_live) < live;
        // no FIFO interrupt for a while: the chip may have stopped streaming
        let stall = if on { STREAM_STALL_TICKS } else { TICKS_PER_S as u16 };
        if now.wrapping_sub(p.st_last_irq) > stall {
            if on {
                app.pager.st_restarts = app.pager.st_restarts.saturating_add(1);
            }
            report_srst(app, if on { 'S' } else { 'I' });
            rearm_rx(app, syst);
        }
        n += 1;
        // Only good packets keep it here for long: preamble-like bits or false syncs in noise
        // held it for good on e2411fe (keys dead while idle). Without a good packet it goes
        // back to the main loop after 1.5 packets' time, and comes back on the next pass.
        let no_good = app.pager.status.good_packets == good_in;
        if !on || (no_good && now.wrapping_sub(t_in) > live) || n > 200_000 {
            break;
        }
        delay::us(syst, 500);
    }
}

/// Called from the main loop in pager mode: picks up packets, and starts the
/// playback 3 s after the last packet of an incomplete message.
#[inline(never)] // its locals only while it runs, not in main's frame for good
pub(super) fn poll(app: &mut App, syst: &mut SYST) {
    if app.mode != Mode::Pager || !app.pager.chip_ok {
        return;
    }
    let now = uptime::now();
    rx_led_poll(now);
    #[cfg(not(feature = "lean"))]
    if unsafe { *addr_of!(MOD_LED) } != 0 && unsafe { (*addr_of!(RX_LED_T)).is_none() } {
        upload_led(app, syst, false); // the sender stopped partway: red off too
    }
    let dt = now.wrapping_sub(app.pager.last_tick) as u32;
    app.pager.idle_ticks = app.pager.idle_ticks.saturating_add(dt);
    app.pager.cfg_ticks = app.pager.cfg_ticks.saturating_add(dt);
    app.pager.last_tick = now;
    if app.pager.cfg_ticks >= CFG_EVERY_TICKS && app.pager.idle_ticks >= CFG_QUIET_TICKS {
        // the idle channel's level and the battery (Aura reads it only in its own main loop)
        app.pager.idle_rssi = app.radio.rssi(syst) as i16 - 174;
        app.poll_battery();
        report_cfg(app, "tick");
    }

    let mut flags = if app.pager.stream() {
        poll_stream(app, syst);
        0
    } else {
        take_irq(app, syst)
    };
    let mut packets = 0u16;
    let mut fails = 0u8;
    while flags & (IRQ_RX_SYNC | IRQ_RX_FULL | IRQ_RX_DONE) != 0 {
        app.pager.last_ok = false;
        unsafe { TOOK = false };
        receive_packet(app, syst, flags);
        rate_packet(app, syst);
        packets += 1;
        if app.pager.stream_start {
            app.pager.stream_start = false;
            stream_begin(app, syst);
        }
        fails = if app.pager.last_ok { 0 } else { fails + 1 };
        // a control packet asking for a coded 4FSK session: its lead is on the air now, so
        // out to the session at once (waiting out the burst cost a second of the lead)
        #[cfg(not(feature = "lean"))]
        if app.pager.ctl.map_or(false, |r| r[5] == CTL_ACT_IFRX) {
            break;
        }
        // stay in this loop through a burst: after two failed packets in a row (a false sync
        // on noise, no signal) go back to the main loop, or the keys and the power switch
        // would never be seen (6661d40 froze idle). Leaving after one failure (e2411fe) cost
        // the packets after it too: the main loop noticed the next sync late (11/16 on air)
        // (interrupt reads too, since 0cb0ff4 made them work: leaving here after each packet
        // left 144-201 ms between packets against the timed reads' 80)
        if fails >= 2 || packets >= MAX_BURST_PACKETS {
            break;
        }
        // Back to back, the next packet's sync comes about 90 ms after the re-arm. Wait for
        // it here, every 0.5 ms, not in the main loop: with the screen redraw in between,
        // the main loop noticed it 30-50 ms late, the timed reads started late and the
        // packet slipped (5e60d8f: 7-10/16). The screen catches up after the burst.
        flags = 0;
        while uptime::now().wrapping_sub(app.pager.t_rearm) < NEXT_SYNC_WAIT_TICKS {
            rx_led_poll(uptime::now());
            flags = take_irq(app, syst);
            if flags != 0 {
                break;
            }
            if !stream_fill(app, syst) {
                delay::us(syst, 500);
            }
        }
    }
    // the burst's time isn't idle time (auto-play waits GAP_TICKS after the last packet)
    app.pager.last_tick = uptime::now();

    #[cfg(not(feature = "lean"))]
    if arq().owe != 0 {
        arq_poll(app, syst, dt);
    }

    if let Some(rec) = app.pager.ctl.take() {
        apply_control(app, syst, rec);
        #[cfg(not(feature = "lean"))]
        if app.pager.ifrx_ms != 0 {
            if_session(app, syst);
        }
    } else if app.pager.d2400 && unsafe { ffi::kr_tick(dt) } != 0 {
        // D/2400: no good packet 10 s after the switch, or 30 s after the last one
        rate_down(app, syst);
    } else if let Some(prev) = app.pager.ctl_prev {
        app.pager.ctl_wait = app.pager.ctl_wait.saturating_add(dt);
        if app.pager.ctl_wait >= CTL_REVERT_TICKS {
            app.pager.ctl_prev = None;
            let p = &mut app.pager;
            (p.xl, p.d2400, p.idle_keep) = prev;
            p.poke_n = p.poke_prev; // the writes confirmed with those settings stay
            settings_changed(app, syst);
            rl!("K6 CTL revert: no good packet in 60 s");
            report_cfg(app, "revert");
        }
    }

    let st = &app.pager.status;
    if st.have != 0
        && app.pager.auto_play
        && !app.pager.stream_play
        && st.frames_got > 0
        && app.pager.played_id != st.msg_id as i16
        && app.pager.idle_ticks >= GAP_TICKS
    {
        app.pager.play_pending = true;
    }
}

/// take_payload ran for the packet receive_packet just read (a sync came: an RX-done alone,
/// the last packet's own late one, doesn't count), and the RSSI at its end. Statics, not
/// PagerState fields (whose initialiser costs flash a field)
static mut TOOK: bool = false;
static mut RSSI_PKT: i16 = 0;

/// After each packet: the link numbers for the PLAY line (any rung) and, on D/2400, the CRC
/// rule (KR_BAD_MAX of the last KR_WINDOW synced packets failed: back to F/1800 at once)
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn rate_packet(app: &mut App, syst: &mut SYST) {
    if !unsafe { TOOK } {
        return;
    }
    let ok = app.pager.last_ok;
    unsafe { ffi::kr_link(ok as i32, RSSI_PKT as i32) };
    if app.pager.d2400 && unsafe { ffi::kr_packet(ok as i32) } != 0 {
        rate_down(app, syst);
    }
}

/// k6_rate.c said fall back: F/1800 again (with whatever else the control packet changed, if
/// no good packet ever kept it), `K6 RATE D24>F18 why=crc|quiet|revert n= bad=`, and a CFG line.
/// The reason goes into the next ARQ report (kr_take_why), so the sender follows.
#[inline(never)]
fn rate_down(app: &mut App, syst: &mut SYST) {
    let st = unsafe { &*ffi::kr_state() };
    let why = match st.why {
        KR_WHY_CRC => "crc",
        KR_WHY_QUIET => "quiet",
        _ => "revert",
    };
    rl!("K6 RATE D24>F18 why={} n={} bad={}", why, st.n, st.hist.count_ones());
    let p = &mut app.pager;
    if let Some((xl, _, keep)) = p.ctl_prev.take() {
        (p.xl, p.idle_keep) = (xl, keep);
        p.poke_n = p.poke_prev;
    }
    p.d2400 = false;
    settings_changed(app, syst);
    report_cfg(app, "rate");
}

/// One packet: a sync was found (or the FIFO filled); collect the payload
/// 4 words at a time until RX finished, waiting here (at most ~0.5 s).
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn receive_packet(app: &mut App, syst: &mut SYST, mut flags: u16) {
    if flags & (IRQ_RX_SYNC | IRQ_RX_FULL) == 0 {
        // an RX-done with no sync: the end of a packet already finished on the timer, or
        // (interrupt reads, 2026-11-05) a stale one that a 32-word read here would turn
        // into repeats of the FIFO's last word, eating the next packet's first words
        if let Some(t) = app.pager.t_timed_done.take() {
            let ms = uptime::now().wrapping_sub(t) / 10;
            app.pager.stray_ms = ms as i16;
            if ms < STRAY_IGNORE_MS {
                // the last packet's own RX-done, after our re-arm: re-arming again now would
                // land on the next packet's preamble or sync (key 8 sweep, 2026-11-04)
                return;
            }
        }
        app.pager.idle_rd = app.pager.idle_rd.saturating_add(1);
        if !app.pager.idle_keep {
            rearm_rx(app, syst);
        }
        return;
    }
    let t_first = uptime::now();
    rx_blink(t_first);
    // the signal at its sync, while it's on the air (two register reads, ~0.1 ms: the FIFO holds
    // 128 bytes, so nothing is lost meanwhile)
    app.pager.sync_rssi = app.radio.rssi(syst) as i16 - 174;
    app.pager.sync_noise = app.radio.fd6818.reg_read(syst, 0x65) as u8;
    app.pager.gap_ms = t_first.wrapping_sub(app.pager.t_rearm) / 10;
    if app.pager.t_timed_done.is_some() {
        app.pager.stray_ms = -1;
    }
    app.pager.rearm_before = app.pager.rearm_last;
    app.pager.rearm_last = -1;
    let len = app.pager.payload_len();
    let words = len / 2;
    let swap_read = app.pager.swap;
    let mut buf = [0u8; MAX_PAYLOAD];
    let mut got = 0usize; // bytes
    let t0 = uptime::now();
    let mut waited: u32 = 0;
    let mut last = t0;
    let mut saw_sync = false;
    let timed = app.pager.timed();
    let irq_n = app.pager.irq_words();
    // timed reads: a 16-bit word every 13.3 ms at 1200 baud (3 words per 400 ticks), 6.7 at 2400
    let words_div: usize = 6 * app.pager.tpb();
    // the sync is noticed 22-33 ms late: 2 words at 1200 but 4-5 at 2400, where one word of
    // lead left the FIFO full and a word was lost at about bit 128 (0438507, 2026-11-04)
    let lead = if app.pager.d2400 { TIMED_LEAD_WORDS_2400 } else { TIMED_LEAD_WORDS };
    let tpb = app.pager.tpb();
    let mut t_sync = t0;
    let mut t_due = t0;
    // interrupt reads: when the last words were taken. The FIFO-almost-full interrupt is an
    // edge; with N or more words still waiting after a read it never comes again (5e=0201
    // and some 0202 packets on air, 2026-11-05), so a word is taken by time after N+1 word
    // times without one
    let mut t_read = t0;
    let word_ticks = 2 * tpb;
    // reads by time since the last almost-full read
    let mut stalls = 0usize;
    app.pager.ev_n = 0;
    loop {
        let words_before = got / 2;
        if flags & IRQ_RX_SYNC != 0 {
            if !saw_sync {
                t_sync = uptime::now();
                t_read = t_sync;
                // the read schedule: a decoder yield may have taken this sync well before
                // the receive loop came (t_sync stays the re-arm's clock, as tuned)
                t_due = unsafe { if Y_SYNC_ON { Y_SYNC_T } else { t_sync } };
            }
            saw_sync = true;
            let s = app.radio.fd6818.reg_read(syst, REG_SYNC_STATUS);
            app.pager.last_sync = if s & 0x80 != 0 {
                b'N'
            } else if s & 0x40 != 0 {
                b'P'
            } else {
                b'?'
            };
        }
        if flags & IRQ_RX_FULL != 0 && !timed && got / 2 < words {
            let n = (words - got / 2).min(irq_n);
            read_words(app, syst, &mut buf, &mut got, n);
            t_read = uptime::now();
            stalls = 0;
            // the sync was noticed late if more words were in than its time says: count from earlier
            let due = t_read.wrapping_sub(t_due) as usize / word_ticks;
            if saw_sync && got / 2 > due {
                t_due = t_due.wrapping_sub(((got / 2 - due) * word_ticks) as u16);
            }
        }
        let flags_seen = flags;
        if saw_sync
            && flags & IRQ_RX_DONE != 0
            && (uptime::now().wrapping_sub(t_sync) as usize) < len * STRAY_DONE_TICKS_PER_BYTE * tpb / TICKS_PER_BYTE
        {
            // far too soon for this packet's end: the last packet's own RX-done, late. Taken
            // as this one's end it would cut it short
            flags &= !IRQ_RX_DONE;
            if let Some(t) = app.pager.t_timed_done.take() {
                app.pager.stray_ms = (uptime::now().wrapping_sub(t) / 10) as i16;
            }
        }
        let early = flags & IRQ_RX_DONE != 0 && got / 2 < words && app.pager.keep_after_done;
        if flags & IRQ_RX_DONE != 0 && !early {
            if got / 2 < words {
                // interrupt reads: fewer than N words can be left (reading an empty FIFO
                // repeats its last word)
                // (all that is left, at most the 8-word FIFO; with fewer waiting the packet
                // already lost words, so it fails its CRC either way)
                let n = words - got / 2;
                let n = if timed { n } else { n.min(FIFO_WORDS) };
                read_words(app, syst, &mut buf, &mut got, n);
            }
        }
        let dt = last.wrapping_sub(t0);
        app.pager.ev_push((flags_seen, dt, (got / 2 - words_before) as u8));
        if (flags & IRQ_RX_DONE != 0 && !early) || got / 2 >= words {
            break;
        }
        // wait for the next interrupt (timed reads: taking each word as it falls due)
        loop {
            // streaming: decode a frame while the words come, but not in the packet's last
            // HOLD_WORDS, so its end and the re-arm aren't late. With the last irq_n (4) words
            // held, 36 ms a packet, the hiss band image's decoder (18.6 ms a 20 ms frame) ran
            // short by about 2 frames a packet (2026-11-08); with 2, a decode starts with 3
            // words (27 ms) to go, and the end is noticed late only by a decode's excess over
            // that (8 ms at the worst seen), well inside the 107 ms preamble before the next sync
            let filled = !timed && words > got / 2 + HOLD_WORDS && stream_fill(app, syst);
            if !filled {
                delay::us(syst, 500);
            }
            let now = uptime::now();
            rx_led_poll(now);
            waited += now.wrapping_sub(last) as u32;
            last = now;
            if filled {
                // the decode's interrupts first: the stall check's clock is older than them
                flags = take_irq(app, syst);
                if flags != 0 {
                    break;
                }
            }
            if timed && saw_sync {
                let due = (lead + now.wrapping_sub(t_sync) as usize * 3 / words_div).min(words);
                if due > got / 2 {
                    let n = due - got / 2;
                    read_words(app, syst, &mut buf, &mut got, n);
                }
                if got / 2 >= words {
                    flags = 0;
                    break;
                }
            }
            let since = now.wrapping_sub(t_read) as usize;
            // N + 2 word times: the interrupt is due after N, and polling it a little late must
            // not lose the race to a read by time
            if !timed && saw_sync && since > (irq_n + 2) * word_ticks {
                // the interrupt stalled: take the words that came in since the last read (the
                // FIFO was about empty then), which brings the level back under the threshold
                // so its edge comes again. One word at a time (876ba93) fell behind and
                // overflowed the FIFO
                // (the FIFO may not have been empty after the last read: a late sync, or a decode
                // that held the CPU at the start of a stream, left words behind and the FIFO ran
                // full, losing words, f548d3e/89d0bf2 on air)
                // A second read by time in a row means words were left behind: with N or more,
                // the edge never comes; with fewer, it comes inside the guard below and is
                // dropped. Either way each read took only what came since the last one, so the
                // backlog stayed (d77ae9d on air, 2026-11-06: 2 words, 10 reads of 5 in one
                // packet, FIFO peaking at 7 of 8). Each read after the first takes one more
                // word, until an almost-full read comes again.
                let due = (now.wrapping_sub(t_due) as usize / word_ticks).saturating_sub(got / 2);
                let soft = unsafe { Y_SOFT_N as usize };
                let n = (since / word_ticks + stalls.min(1)).max(due).min(FIFO_WORDS + soft).min(words - got / 2);
                stalls += 1;
                read_words(app, syst, &mut buf, &mut got, n);
                t_read = now;
                // (e) an almost-full latched before this read is for words it already took
                unsafe {
                    Y_FULL_T = now;
                    Y_FULL_ON = true;
                }
                // pseudo event 1: words taken by time
                app.pager.ev_push((1, now.wrapping_sub(t0), n as u8));
                if got / 2 >= words {
                    flags = 0;
                    break;
                }
            }
            flags = take_irq(app, syst);
            if flags != 0 || waited > PACKET_TIMEOUT_TICKS {
                break;
            }
        }
        if got / 2 >= words {
            // all words read on the timer: log it as a pseudo event 0, then re-arm once the
            // chip's own RX-done is in and at least REARM ms (key 8, 40 by default) after the
            // payload's end, or at REARM + 10 ms without it. Measured on air (2026-11-04,
            // key 8 sweep over VE9QRP): re-arming before that RX-done (f413547; 0-30 ms)
            // makes it come late, 110-175 ms after, and any re-arm then lands on the next
            // packet's preamble and false-syncs; 60 ms or later leaves too little preamble for
            // the chip's bit clock, and the packet syncs but slips (314a306 waited for the
            // RX-done, up to a 150 ms timeout that ran past the next sync). 40 ms: 3/3.
            app.pager.ev_push((0, last.wrapping_sub(t0), (got / 2 - words_before) as u8));
            let delay_ms = app.pager.rearm_delay_ms();
            app.pager.rearm_last = delay_ms as i16;
            let end = (len * tpb) as u16;
            // key 8's delays are for 1200 baud; at 2400 the preamble is half as long
            let rearm_min = end + (delay_ms as usize * 10 * tpb / TICKS_PER_BYTE) as u16;
            let rearm_max = rearm_min + (REARM_WAIT_TICKS as usize * tpb / TICKS_PER_BYTE) as u16;
            let t_done = uptime::now();
            app.pager.t_timed_done = Some(t_done);
            let mut done = false;
            loop {
                if take_irq(app, syst) & IRQ_RX_DONE != 0 && !done {
                    done = true;
                    app.pager.t_timed_done = None;
                    app.pager.stray_ms = (uptime::now().wrapping_sub(t_done) / 10) as i16;
                }
                let since = uptime::now().wrapping_sub(t_sync);
                rx_led_poll(uptime::now());
                if (done && since >= rearm_min) || since >= rearm_max {
                    break;
                }
                delay::us(syst, 500);
            }
            break;
        }
        if flags == 0 {
            app.pager.timeouts = app.pager.timeouts.saturating_add(1);
            break;
        }
    }

    if !saw_sync {
        // no sync interrupt seen (Jeff's FFSK test showed SYNC -): read the
        // polarity flags anyway, in lower case, before re-arming clears them
        let s = app.radio.fd6818.reg_read(syst, REG_SYNC_STATUS);
        app.pager.last_sync = if s & 0x80 != 0 {
            b'n'
        } else if s & 0x40 != 0 {
            b'p'
        } else {
            b'-'
        };
    }
    // the RSSI at the packet's end, before the re-arm (the PLAY line's rssi_min= and rssi_med=)
    let r = app.radio.fd6818.reg_read(syst, 0x67);
    unsafe { RSSI_PKT = ((r & 0x1FF) >> 1) as i16 - 174 };
    rearm_rx(app, syst);
    take_payload(app, Some(syst), &mut buf, got, len, swap_read, false);
}

/// D/2400's polarity: inverted as read (learned), and until the session's first good packet
/// (k6_rate's `good`), a packet that checks only upside down turns it over for good. "Checks":
/// a voice packet's CRC (sp_check), or a module chunk's magic and CRC (k6_mod.h). Control
/// packets need none of this: sp_control reads every polarity itself
static mut D_INV: bool = false;

#[inline(never)] // its 128-byte copy only while it runs
fn d24_polarity(buf: &mut [u8; MAX_PAYLOAD], len: usize) {
    fn flip(b: &mut [u8]) {
        for x in b.iter_mut() {
            *x = !*x;
        }
    }
    fn checks(b: &[u8; MAX_PAYLOAD], len: usize) -> bool {
        if unsafe { ffi::sp_check(b.as_ptr(), len as i32) } != 0 {
            return true;
        }
        let w = unsafe { core::slice::from_raw_parts(ffi::sp_pn9(), len) };
        let mut d = [0u8; MAX_PAYLOAD];
        for i in 0..len {
            d[i] = b[i] ^ w[i];
        }
        matches!(d[0], 0xAB | 0xAE)
            && unsafe { ffi::sp_crc16(d.as_ptr(), len as i32 - 2) } == (d[len - 2] as u32) << 8 | d[len - 1] as u32
    }
    if unsafe { D_INV } {
        flip(&mut buf[..len]);
    }
    if unsafe { (*ffi::kr_state()).good } == 0 && !checks(buf, len) {
        flip(&mut buf[..len]);
        if checks(buf, len) {
            unsafe { D_INV = !D_INV };
            rl!("K6 RATE D24 inv={}", unsafe { D_INV } as u8);
        } else {
            flip(&mut buf[..len]);
        }
    }
}

/// A payload read in packet or stream mode (`got` of `len` bytes): the raw view, bit errors
/// against the test message, the polarity and byte order tries before the first good packet
/// (packet mode; in stream mode the sync's polarity already set it, and a wrong byte order
/// finds no sync), then the pager, the serial report and auto-play.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn take_payload(
    app: &mut App,
    syst: Option<&mut SYST>,
    buf: &mut [u8; MAX_PAYLOAD],
    got: usize,
    len: usize,
    swap_read: bool,
    stream: bool,
) {
    arq().wait = 0; // the channel is busy: a report waits for it to go quiet
    unsafe { TOOK = true };
    let n = got.min(16);
    app.pager.raw[..n].copy_from_slice(&buf[..n]);
    app.pager.raw_len = got.min(255) as u8;
    let read = *buf;
    if got < len {
        report_packet(app, &read[..got.min(len)], swap_read, None);
        return;
    }
    // D/2400: its own polarity, learned from its first packets (F/1800's soft_inv is the FFSK
    // receiver's and says nothing about the plain FSK one), and set before the module loader
    // looks, so an upload at D/2400 needs no voice packet to teach it
    if app.pager.d2400 && !stream {
        d24_polarity(buf, len);
    }

    // a RAM module packet (magic 0xAB, c/k6/k6_mod.h)? Not while a message plays: the module
    // lands in the shared RAM block (hal::arena), over the store, the PWM ring and the decoder
    if let (false, false, Some(syst)) = (app.pager.play_pending, stream_on(), syst) {
        let r = unsafe { ffi::km_packet(buf.as_ptr(), len as i32, arena::base(), arena::ARENA_BYTES as u32) };
        if r != 0 {
            module_packet(app, syst, r);
            return;
        }
        // a good voice packet partway through a module: messages win, the module is dropped
        if unsafe { (*ffi::km_status()).active } != 0 && unsafe { ffi::sp_check(buf.as_ptr(), len as i32) } != 0 {
            unsafe { ffi::km_reset() };
            rl!("K6 MOD dropped: a voice packet came first");
            arq().owe = 0;
            clear_store(app);
        }
    }

    // bit errors against the test message (calibration packets), before any guessing
    let mut e = ffi::NO_BITERR;
    if unsafe { ffi::sp_test_errors(buf.as_ptr(), len as i32, &mut e) } >= 0 {
        e.swap = (app.pager.swap ^ (e.swap != 0)) as u8;
        app.pager.ber = e;
        app.pager.ber_sum = app.pager.ber_sum.saturating_add(e.errors as u32);
        app.pager.ber_n = app.pager.ber_n.saturating_add(1);
    }

    if app.pager.soft_inv && !stream && !app.pager.d2400 {
        for b in buf[..len].iter_mut() {
            *b = !*b;
        }
    }
    // Until the first good packet, try the other FIFO byte order and the other
    // polarity (the magic byte, or the CRC for profile S, tells), in software, so a
    // wrong guess about what the chip's SyncN means can't lock RX out. SyncN puts
    // the inverted tries first.
    if !stream && !app.pager.d2400 && app.pager.status.good_packets == 0 && unsafe { ffi::sp_check(buf.as_ptr(), len as i32) } == 0 {
        let tries: [(bool, bool); 3] = if matches!(app.pager.last_sync, b'N' | b'n') {
            [(false, true), (true, true), (true, false)]
        } else {
            [(true, false), (false, true), (true, true)]
        };
        for &(sw, inv) in tries.iter() {
            let mut t = *buf;
            if sw {
                for w in t[..len].chunks_exact_mut(2) {
                    w.swap(0, 1);
                }
            }
            if inv {
                for b in t[..len].iter_mut() {
                    *b = !*b;
                }
            }
            if unsafe { ffi::sp_check(t.as_ptr(), len as i32) } != 0 {
                *buf = t;
                if sw {
                    app.pager.swap = !app.pager.swap;
                }
                if inv {
                    app.pager.soft_inv = !app.pager.soft_inv;
                }
                break;
            }
        }
    }
    if AGAIN && unsafe { KEPT && ffi::sp_check(buf.as_ptr(), len as i32) != 0 } {
        clear_store(app); // a message wins over the kept module
    }
    let r = unsafe { ffi::sp_packet(buf.as_ptr(), len as i32) };
    if r == 2 {
        unsafe { PLAY_FROM = [0; FROM_MAX] }; // a new message over the air: no caller to show
    }
    app.pager.last_result = r as i8;
    app.pager.last_magic = unsafe { *ffi::sp_last_packet() };
    refresh_status(app);
    report_packet(app, &read[..len], swap_read, Some(r));
    if r != SP_BAD_CRC && r != SP_BAD_MAGIC && r >= 0 {
        crate::hal::light::touch(); // a message coming in lights the screen
        app.pager.idle_ticks = 0;
        app.pager.last_ok = true;
        app.pager.ctl_prev = None; // a good packet in these settings: keep them
    } else if !stream {
        // not a voice packet: a control packet? (any byte order or polarity; poll applies it)
        let mut rec = [0u8; 6];
        if unsafe { ffi::sp_control(read.as_ptr(), len as i32, rec.as_mut_ptr()) } != 0 {
            app.pager.last_ok = true;
            app.pager.ctl_prev = None;
            app.pager.ctl = Some(rec);
        }
    }
    if r == SP_COMPLETE
        && app.pager.auto_play
        && !app.pager.stream_play
        && app.pager.played_id != app.pager.status.msg_id as i16
    {
        app.pager.play_pending = true;
    }
    if r >= 0 && app.pager.stream_play {
        let id = app.pager.status.msg_id as i16;
        if stream_on() && id != app.pager.played_id {
            unsafe { (*addr_of_mut!(STREAM)).aborted = true }; // a new message: stop this one
        } else if !stream_on()
            && app.pager.auto_play
            && id != app.pager.played_id
            && unsafe { ffi::sp_stream_ready() } != 0
        {
            app.pager.stream_start = true; // poll starts it after this packet
        }
    }
}

/// A module packet: report it, and run the module once its last chunk checks. A module
/// transfer empties the message store, which is set up again when the transfer is over.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn module_packet(app: &mut App, syst: &mut SYST, r: i32) {
    let st = unsafe { &*ffi::km_status() };
    rl!(
        "K6 MOD r={} xfer={} chunk={} have={}/{} rssi={} noise={}",
        r, st.xfer, st.chunk, st.have, st.count, app.pager.sync_rssi, app.pager.sync_noise
    );
    #[cfg(not(feature = "lean"))]
    upload_led(app, syst, st.active != 0);
    if r != -1 {
        // its CRC checked: the link works in these settings
        app.pager.idle_ticks = 0;
        app.pager.last_ok = true;
        app.pager.ctl_prev = None;
    }
    if r == KM_READY {
        // an image for the SPI module library (k6_lib.h, header flag KM_F_LIB): kept, not run
        // (km_packet left km_run unarmed for it)
        #[cfg(feature = "spilib")]
        if unsafe { *arena::base().add(13) } & KM_F_LIB != 0 {
            let n = lib_install(app);
            if st.reply != 0 {
                arq_owe(KM_ST_RAN, n, 0, free_ram().min(0xFFFF) as u16);
            }
        } else {
            unsafe { RUN_SLOT = 255 };
            if run_module(app, syst) {
                return; // its message is in the store, to play
            }
        }
        #[cfg(not(feature = "spilib"))]
        if run_module(app, syst) {
            return;
        }
    }
    #[cfg(not(feature = "lean"))]
    if st.reply != 0 && r != KM_READY {
        if r <= -3 {
            arq_owe(KM_ST_REFUSED, r, 0, 0);
        } else if st.active != 0 {
            arq_owe(KM_ST_PARTWAY, 0, 0, 0); // the gaps, once the sender stops
        } else if r == 2 && arq().state != KM_ST_PARTWAY {
            arq().owe = 2; // a repeat of a finished transfer: the sender missed the result
            arq().wait = 0;
        }
    }
    if r <= -3 || (r == KM_READY && !AGAIN) {
        clear_store(app); // the transfer is over, run or refused
    } else {
        // the image stays in the block for side key 1, the store empty, until a message
        // wants it
        unsafe { KEPT |= r == KM_READY };
        refresh_status(app); // the store is empty while the chunks arrive
    }
}

/// Run the module that km_packet (over the air) or kl_load (from the SPI library) armed in
/// the shared block, and report how it went.
/// true when the module handed a message to sotto_play (level 11): it is in the store now, to play
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn run_module(app: &mut App, syst: &mut SYST) -> bool {
    let st = unsafe { &*ffi::km_status() };
    let at = (arena::base() as usize) + 16;
    // 8-byte tag after the code, then word aligned
    let scratch = (at + st.code_len as usize + 8 + 3) & !3;
    let room = cortex_m::register::msp::read() as usize - ((addr_of!(__euninit) as usize + 3) & !3);
    rl!(
        "K6 MOD run serial={} code={} B at {:#010x} stack={} B scratch={} B",
        st.serial,
        st.code_len,
        at,
        room,
        scratch_end().saturating_sub(scratch)
    );
    let gpio = app.radio.fd6818.reg_read(syst, MOD_GPIO);
    let t0 = uptime::now();
    crate::hal::light::touch(); // its screen comes up: lit
    unsafe {
        *addr_of_mut!(SET_SAVED) = 0;
        *addr_of_mut!(MOD_CTX) = ModCtx {
            app: app as *mut App,
            syst: syst as *mut SYST,
            rx_hz: app.master_freq_hz(),
            t_last: t0,
            ticks: 0,
            scratch,
            tx_until: 0,
            tx: false,
            cal_written: false,
            fl_erases: 0,
            tlm_cap: 0,
        };
    }
    // a reply-wanted upload whose header asks for telemetry: keep the say lines
    #[cfg(not(feature = "lean"))]
    if st.reply != 0 && st.tlm != 0 {
        tlm_start(st.tlm as usize * KM_TLM_PART);
    }
    mod_screen_clear();
    let ret = unsafe { ffi::km_run(arena::base(), &MOD_API) };
    #[cfg(banded)]
    crate::device::display::grab::set(None); // the tap was in the module's memory
    unsafe { mod_tx_off() }; // a module that left the transmitter on
    // a key held to stop the module: let it go first (up to 3 s), so its release isn't a press
    // here (side key 1 would run the module again; * would clear)
    let t1 = uptime::now();
    while unsafe { mod_key_poll() } != 0 && uptime::now().wrapping_sub(t1) < 30_000 {}
    rx_led(false); // or the LED lit
    mod_screen_clear(); // its waterfall rows were in its own memory
    let ms = unsafe { mod_ms() };
    unsafe {
        let c = &mut *addr_of_mut!(MOD_CTX);
        c.app = core::ptr::null_mut();
        c.tlm_cap = 0;
    }
    // free_ram: the stack never reached since VOICE MSG was entered (painted then)
    let sfree = free_ram();
    rl!("K6 MOD ret={} ({:#x}) in {} ms stack_free={} B", ret, ret, ms, sfree);
    ram_check(sfree);
    #[cfg(not(feature = "lean"))]
    if st.reply != 0 {
        arq_owe(KM_ST_RAN, ret, ms, sfree.min(0xFFFF) as u16);
    }
    // a level 2 module may have moved the radio: back to the pager's frequency and set-up,
    // and GP0 as it was
    app.radio.fd6818.reg_write(syst, MOD_GPIO, gpio);
    app.sync_watching_to_master(syst);
    arm(app, syst);
    play_put(app)
}

/// Side key 1 runs the last module again (Jeff, 2026-11-10), in the images with the SPI
/// library: lib_run's slot AGAIN_SLOT
const AGAIN: bool = cfg!(feature = "spilib");
#[cfg(feature = "spilib")]
const AGAIN_SLOT: u8 = 254;
/// lib_run's r while a message plays: refused, nothing read
#[cfg(feature = "spilib")]
const LIB_PLAYING: i32 = -9;
/// 1 while the last module (run, or sent to the library) is still in the shared block, the
/// store empty meanwhile
static mut KEPT: bool = false;
/// the library slot that ran last (255: none)
#[cfg(feature = "spilib")]
static mut LAST_SLOT: u8 = 255;

// ---- the SPI module library (c/k6/k6_lib.h; Jeff approved it, 2026-11-08) ----
//
// A module sent with header flag KM_F_LIB is checked as any module (km_packet) and written to
// its 16 KB slot at 0x110000 + slot * 0x4000 instead of being run; control action lib (11)
// reads a slot back into the shared block, checks its tag again and runs it as one sent over
// the air. The only SPI writes are an install's (its sectors, once), never while keyed, and
// only inside 0x110000-0x18FFFF (32 slots since the release plan); the message store never
// goes to SPI.

#[cfg(feature = "spilib")]
const KM_F_LIB: u8 = 1;
#[cfg(feature = "spilib")]
const KL_LO: u32 = 0x11_0000;
#[cfg(feature = "spilib")]
const KL_HI: u32 = 0x19_0000;
#[cfg(feature = "spilib")]
const KL_SLOTS: i32 = 32;
/// writes allowed: set only around kl_install, and only while not transmitting
#[cfg(feature = "spilib")]
static mut LIB_WRITE: bool = false;

#[cfg(feature = "spilib")]
unsafe extern "C" fn lib_fl_read(addr: u32, buf: *mut u8, n: u32) -> i32 {
    if CB_NOR.is_null() || addr < KL_LO || addr >= KL_HI || n > KL_HI - addr {
        return -1;
    }
    (*CB_NOR).read_bytes(addr, core::slice::from_raw_parts_mut(buf, n as usize));
    0
}
#[cfg(feature = "spilib")]
unsafe extern "C" fn lib_fl_prog(addr: u32, buf: *const u8, n: u32) -> i32 {
    if !LIB_WRITE || CB_NOR.is_null() || addr < KL_LO || addr >= KL_HI || n == 0 || n > FL_PAGE
        || (addr % FL_PAGE) + n > FL_PAGE
    {
        return -1;
    }
    (*CB_NOR).write_bytes(addr, core::slice::from_raw_parts(buf, n as usize));
    0
}
#[cfg(feature = "spilib")]
unsafe extern "C" fn lib_fl_erase(addr: u32) -> i32 {
    if !LIB_WRITE || CB_NOR.is_null() || addr < KL_LO || addr >= KL_HI || addr % FL_SECTOR != 0 {
        return -1;
    }
    (*CB_NOR).erase_sector(addr);
    0
}
#[cfg(feature = "spilib")]
static LIB_FL: ffi::KlFlash = ffi::KlFlash { read: lib_fl_read, prog: lib_fl_prog, erase: lib_fl_erase };

/// The checked image in the shared block into its slot: its length, or k6_lib.h KL_*
#[cfg(feature = "spilib")]
#[inline(never)]
fn lib_install(app: &mut App) -> i32 {
    let slot = unsafe { *arena::base().add(15) };
    unsafe { LIB_WRITE = !app.is_transmitting() };
    let r = unsafe { ffi::kl_install(arena::base(), &LIB_FL) };
    unsafe { LIB_WRITE = false };
    rl!("K6 LIB put slot={} r={}", slot, r);
    r
}

/// The cable session (cps::run_cable_session) starts: drop any transfer partway (its chunks
/// were in the block the session now uses)
#[cfg(feature = "spilib")]
pub(super) fn cable_begin() {
    unsafe { ffi::km_reset() };
}

/// The cable session: one module chunk, a whitened 64-byte packet as it would come over the
/// air, into km_packet (the same checks: CRC, header, the tag under this radio's key, the
/// serial). An image for the library (KM_F_LIB) goes into its slot through lib_install, as
/// over the air; any other image only checks (r=3 shows the PC its key is right) and is
/// dropped, never run. Nothing here transmits. (r, have, count, the install's result or 0)
#[cfg(feature = "spilib")]
#[inline(never)]
pub(super) fn cable_chunk(app: &mut App, pkt: &[u8]) -> (i8, u8, u8, i32) {
    let r = unsafe { ffi::km_packet(pkt.as_ptr(), pkt.len() as i32, arena::base(), arena::ARENA_BYTES as u32) };
    let mut put = 0;
    if r == KM_READY {
        if unsafe { *arena::base().add(13) } & KM_F_LIB != 0 {
            put = lib_install(app);
        }
        unsafe { ffi::km_reset() }; // never armed to run
    }
    let st = unsafe { &*ffi::km_status() };
    (r as i8, st.have, st.count, put)
}

/// The used slots, one `K6 LIB slot=` line each, then `K6 LIB used=N`
#[cfg(feature = "spilib")]
#[inline(never)]
fn lib_list() {
    let mut used = 0;
    for n in 0..KL_SLOTS {
        let mut i = ffi::KlInfo::default();
        let r = unsafe { ffi::kl_info(n, &LIB_FL, &mut i) };
        if r != -1 {
            used += 1;
            let nl = i.name.iter().position(|&c| c == 0).unwrap_or(i.name.len());
            rl!(
                "K6 LIB slot={} r={} code={} packed={} serial={} level={} flags={} name={}",
                n, r, i.code_len, i.packed, i.serial, i.level, i.flags,
                crate::out::ascii(&i.name[..nl])
            );
        }
    }
    rl!("K6 LIB used={}", used);
}

// ---- the module list (MENU, the release plan, Jeff 2026-11-13) ----
//
// MENU in EXPLORE or the arena opens it: EXPLORE, ARENA and STATIONS, then the library's modules by
// the names they carry (K6_MODULE_NAME; `SLOT n` without one), code pages and help decks left
// out. UP/DOWN pick (wrapping), MENU or # runs the row, EXIT closes it. A module runs from the
// arena, as side key 1's re-run does, so EXPLORE is left first.

#[cfg(all(feature = "explore", feature = "spilib"))]
const MENU_CORE: u8 = 3;
#[cfg(all(feature = "explore", feature = "spilib"))]
struct Menu {
    on: bool,
    sel: u8,
    n: u8,
    /// the modules' slots, in slot order
    slots: [u8; KL_SLOTS as usize],
}
#[cfg(all(feature = "explore", feature = "spilib"))]
#[link_section = ".ram0"] // hal::ram0
static mut MENU: Menu = Menu { on: false, sel: 0, n: 0, slots: [0; KL_SLOTS as usize] };

#[cfg(all(feature = "explore", feature = "spilib"))]
fn menu() -> &'static mut Menu {
    unsafe { &mut *addr_of_mut!(MENU) }
}

// ---- help cards (the release plan, Jeff 2026-11-13; tools/k6_cards.py) ----
//
// A held MENU shows the help card for the screen: a 128x64 picture made on the PC, in the
// display's page format, kept in a deck in library slots 13-15. The screen reads it from SPI a
// page (128 B) at a time straight into the band it draws through, so the viewer holds no buffer
// and no text. UP/DOWN turn the screen's pages, any other key goes back. Only the 8 kHz voice +
// EXPLORE image (`banded`) draws them.

/// the library slots that hold decks (c/k6/k6_lib.h)
#[cfg(banded)]
const HELP_SLOTS: core::ops::Range<i32> = 13..16;
/// pages a screen's help may have
#[cfg(banded)]
const HELP_PAGES: usize = 4;
/// cards a deck may hold (its index entries read)
#[cfg(banded)]
const HELP_CARDS: u32 = 40;

#[cfg(banded)]
struct Help {
    on: bool,
    page: u8,
    n: u8,
    /// each page's card: its SPI address
    addr: [u32; HELP_PAGES],
}

#[cfg(banded)]
#[link_section = ".ram0"] // hal::ram0
static mut HELP: Help = Help { on: false, page: 0, n: 0, addr: [0; HELP_PAGES] };

#[cfg(banded)]
fn help() -> &'static mut Help {
    unsafe { &mut *addr_of_mut!(HELP) }
}

/// Is a card up
#[cfg(banded)]
pub fn help_on() -> bool {
    help().on
}

/// The card's page p (8 pixel rows, bit 0 the top one) into buf, from SPI. A deck v2 card
/// (tools/k6_cards.py, docs/k6-lz4.md) is a table of 8 u16 band ends (from the card's start;
/// band 0 starts at 16), then the bands: 128 bytes as they are, or fewer, one LZ4 block of 128
/// (c/common/lz4d.h). A band that doesn't read or decode shows blank.
#[cfg(banded)]
pub fn help_band(p: u8, buf: &mut [u8; 128]) {
    let h = help();
    let a = h.addr[h.page as usize];
    let mut t = [0u8; 16];
    let mut z = [0u8; 128];
    let ok = unsafe { lib_fl_read(a, t.as_mut_ptr(), 16) } == 0 && {
        let end = |k: usize| u16::from_le_bytes([t[2 * k], t[2 * k + 1]]) as u32;
        let (lo, hi) = (if p == 0 { 16 } else { end(p as usize - 1) }, end(p as usize));
        let n = hi.wrapping_sub(lo);
        if n == 128 {
            unsafe { lib_fl_read(a + lo, buf.as_mut_ptr(), 128) == 0 }
        } else {
            n < 128
                && unsafe { lib_fl_read(a + lo, z.as_mut_ptr(), n) } == 0
                && unsafe { ffi::lz4d(z.as_ptr(), n, buf.as_mut_ptr(), 128) } == 128
        }
    };
    if !ok {
        buf.iter_mut().for_each(|b| *b = 0);
    }
}

/// Find screen `name`'s cards in the decks and show the first: true if it has any.
/// A deck (k6_cards.py): 'K6HC', count, version 2; 16 B entries (name 8 B, page, 3 zero bytes,
/// the card's offset u32); the cards (help_band). The slot was checked (its tag) when it was
/// installed; here every offset is checked against the image's length. A version 1 deck
/// (unpacked cards, before API level 15) is passed over: install the deck again.
#[cfg(banded)]
#[inline(never)]
fn help_open(name: &[u8]) -> bool {
    let h = help();
    h.n = 0;
    for slot in HELP_SLOTS {
        let mut i = ffi::KlInfo::default();
        if unsafe { ffi::kl_info(slot, &LIB_FL, &mut i) } != 0 || i.flags & 0x05 != 0x05 {
            continue; // not a deck: KM_F_LIB | KM_F_CARDS
        }
        let deck = KL_LO + slot as u32 * 0x4000 + 16; // after the module header
        let len = i.code_len as u32;
        let mut e = [0u8; 16];
        if unsafe { lib_fl_read(deck, e.as_mut_ptr(), 16) } != 0 || &e[..4] != b"K6HC" || e[5] != 2 {
            continue;
        }
        let count = (e[4] as u32).min(HELP_CARDS);
        for k in 0..count {
            if unsafe { lib_fl_read(deck + 16 + 16 * k, e.as_mut_ptr(), 16) } != 0 {
                break;
            }
            let n = e[..8].iter().position(|&c| c == 0).unwrap_or(8);
            let off = u32::from_le_bytes([e[12], e[13], e[14], e[15]]);
            let page = e[8] as usize;
            if &e[..n] == name && page < HELP_PAGES && off >= 16 && off + 16 <= len {
                h.addr[page] = deck + off;
                h.n = h.n.max(page as u8 + 1);
            }
        }
        if h.n > 0 {
            break;
        }
    }
    h.page = 0;
    h.on = h.n > 0;
    rl!("K6 HELP {} pages={}", crate::out::ascii(name), h.n);
    h.on
}

/// A key while a card is up: UP/DOWN turn its pages, any other press closes it
#[cfg(banded)]
fn help_turn(ev: KeyEvent) {
    let h = help();
    match (ev.key, ev.kind) {
        (KeyId::Up, KeyEventKind::Single | KeyEventKind::Repeat) => h.page = h.page.saturating_sub(1),
        (KeyId::Down, KeyEventKind::Single | KeyEventKind::Repeat) => h.page = (h.page + 1).min(h.n - 1),
        (_, KeyEventKind::Single | KeyEventKind::Long) => h.on = false,
        _ => {}
    }
}

// ---- level 15: a module's help (Jeff 2026-11-20, "add help API too") ----
//
// help(name): the cards of screen `name` (a module's own name, usually) over the module's
// screen, a key at a time, until a key other than UP/DOWN; the module's screen comes back at
// its next lcd_flush. The card draws through lcd_flush's hook (ui::mod_flush, which shows the
// card while one is up). Keys are polled here, the module waiting, as during playback.

#[cfg(banded)]
unsafe extern "C" fn mod_help(name: *const u8) -> i32 {
    mod_tx_guard();
    let c = unsafe { &*addr_of!(MOD_CTX) };
    if c.tx || c.app.is_null() || name.is_null() {
        return -1;
    }
    let mut nm = [0u8; 8];
    let mut n = 0;
    while n < 8 {
        let b = unsafe { *name.add(n) };
        if b == 0 {
            break;
        }
        nm[n] = b;
        n += 1;
    }
    if !help_open(&nm[..n]) {
        return 0;
    }
    let pages = help().n as i32;
    let (app, syst) = unsafe { mod_ctx() };
    while app.keypad.pop_event().is_some() {}
    while help().on {
        unsafe { mod_lcd_flush() };
        loop {
            delay::us(syst, 5_000);
            app.keypad.poll(syst);
            crate::hal::light::poll();
            if let Some(ev) = app.keypad.pop_event() {
                if matches!(ev.kind, KeyEventKind::Single | KeyEventKind::Repeat | KeyEventKind::Long) {
                    crate::hal::light::touch();
                    help_turn(ev);
                    break;
                }
            }
        }
    }
    pages
}

#[cfg(not(banded))]
unsafe extern "C" fn mod_help(_name: *const u8) -> i32 {
    -1
}

// level 17 (Jeff 2026-11-28: no framebuffer, "something super lightweight that provides hooks
// for a framebuffer if needed for testing"): screen_tap(f, ctx) has draw_pages hand f each page
// it sends (f(page, 128 bytes, ctx)); NULL removes it, and so does the module's return. redraw()
// draws the core's screen again (VOICE MSG, the MENU, a help card), as it will be when the
// module returns: 8 pages through the tap. -2 while EXPLORE is on (its list sits where the
// module is), -1 in an image that doesn't draw a page at a time.

#[cfg(banded)]
unsafe extern "C" fn mod_screen_tap(f: Option<crate::device::display::Hook>, ctx: *mut u8) -> i32 {
    crate::device::display::grab::set(f.map(|f| (f, ctx)));
    0
}

#[cfg(not(banded))]
unsafe extern "C" fn mod_screen_tap(_f: Option<crate::device::display::Hook>, _ctx: *mut u8) -> i32 {
    -1
}

static mut MOD_REDRAW: Option<unsafe fn(&App)> = None;

/// main.rs (ui::hook_mod_screen): the function that draws the core's screen
#[cfg(banded)]
pub fn set_mod_redraw(f: unsafe fn(&App)) {
    unsafe { *addr_of_mut!(MOD_REDRAW) = Some(f) };
}

unsafe extern "C" fn mod_redraw() -> i32 {
    mod_tx_guard();
    let app = unsafe { (*addr_of!(MOD_CTX)).app };
    match unsafe { *addr_of!(MOD_REDRAW) } {
        _ if app.is_null() || !cfg!(banded) => -1,
        _ if unsafe { (*app).exploring() } => -2,
        Some(f) => {
            unsafe { f(&*app) };
            8
        }
        None => -1,
    }
}

/// A key while a card is up, or MENU held to show one: true when help took the key
#[cfg(banded)]
#[inline(never)]
fn help_key(app: &mut App, ev: KeyEvent) -> bool {
    if !help().on {
        if ev.key != KeyId::Menu || ev.kind != KeyEventKind::Long || stream_on() || app.pager.freq_entry {
            return false;
        }
        // on a module's row in the list: its own cards, by its name (level 15), else the list's
        let mut nm = [0u8; 12];
        let m = menu();
        let own = m.on && m.sel >= MENU_CORE && {
            let n = menu_row(m.sel, &mut nm).min(8);
            for c in nm[..n].iter_mut().filter(|c| **c == b' ') {
                *c = b'_'; // a card's name: "AIS SHIPS" is AIS_SHIP
            }
            help_open(&nm[..n])
        };
        let name: &[u8] = if m.on {
            b"MENU"
        } else if explore::on() {
            explore::help_name()
        } else {
            b"ARENA"
        };
        if !own && !help_open(name) {
            explore::help_missing();
        }
    } else {
        help_turn(ev);
    }
    if explore::on() {
        explore::touch();
    } else {
        explore::voice_key();
    }
    true
}

/// (open, the selected row, rows) for the screen
#[cfg(all(feature = "explore", feature = "spilib"))]
pub fn menu_state() -> (bool, u8, u8) {
    let m = menu();
    (m.on, m.sel, MENU_CORE + m.n)
}

/// Row i's text into out: its length
#[cfg(all(feature = "explore", feature = "spilib"))]
pub fn menu_row(i: u8, out: &mut [u8; 12]) -> usize {
    let m = menu();
    let core: &[u8] = match i {
        0 => b"EXPLORE",
        1 => b"ARENA",
        2 => b"STATIONS",
        _ => b"",
    };
    if !core.is_empty() {
        out[..core.len()].copy_from_slice(core);
        return core.len();
    }
    let slot = m.slots[(i - MENU_CORE) as usize];
    let mut inf = ffi::KlInfo::default();
    if unsafe { ffi::kl_info(slot as i32, &LIB_FL, &mut inf) } == 0 && inf.name[0] != 0 {
        let n = inf.name.iter().position(|&c| c == 0).unwrap_or(inf.name.len());
        out[..n].copy_from_slice(&inf.name[..n]);
        return n;
    }
    out[..5].copy_from_slice(b"SLOT ");
    if slot >= 10 {
        out[5] = b'0' + slot / 10;
        out[6] = b'0' + slot % 10;
        7
    } else {
        out[5] = b'0' + slot;
        6
    }
}

#[cfg(all(feature = "explore", feature = "spilib"))]
#[inline(never)]
fn menu_open() {
    let m = menu();
    m.n = 0;
    for n in 0..KL_SLOTS {
        let mut i = ffi::KlInfo::default();
        // modules only: not a code page (2) or a help deck (4)
        if unsafe { ffi::kl_info(n, &LIB_FL, &mut i) } == 0 && i.flags & 0x06 == 0 {
            m.slots[m.n as usize] = n as u8;
            m.n += 1;
        }
    }
    m.sel = if explore::on() { 0 } else { 1 };
    m.on = true;
    rl!("K6 MENU open modules={}", m.n);
}

/// A key while the list is open, or MENU opening it: true when the list took the key
#[cfg(all(feature = "explore", feature = "spilib"))]
#[inline(never)]
fn menu_key(app: &mut App, syst: &mut SYST, ev: KeyEvent) -> bool {
    let m = menu();
    if !m.on {
        // MENU opens it; not while a message streams (MENU stops that) or a frequency is typed
        if ev.key != KeyId::Menu || !matches!(ev.kind, KeyEventKind::Single) || stream_on() || app.pager.freq_entry {
            return false;
        }
        menu_open();
    } else if matches!(ev.kind, KeyEventKind::Single | KeyEventKind::Repeat) {
        let rows = MENU_CORE + m.n;
        match ev.key {
            KeyId::Up => m.sel = if m.sel == 0 { rows - 1 } else { m.sel - 1 },
            KeyId::Down => m.sel = if m.sel + 1 >= rows { 0 } else { m.sel + 1 },
            KeyId::Exit if matches!(ev.kind, KeyEventKind::Single) => m.on = false,
            KeyId::Menu | KeyId::Pound if matches!(ev.kind, KeyEventKind::Single) => {
                m.on = false;
                let sel = m.sel;
                rl!("K6 MENU run row={}", sel);
                match sel {
                    0 => {
                        if !explore::on() {
                            explore::enter(app, syst);
                        }
                    }
                    1 => {
                        if explore::on() {
                            explore::leave(app, syst);
                        }
                    }
                    // STATIONS (Jeff 2026-11-20): the saved table, in EXPLORE
                    2 => {
                        if !explore::on() {
                            explore::enter(app, syst);
                        }
                        explore::stations(app, syst);
                    }
                    _ => {
                        if explore::on() {
                            explore::leave(app, syst);
                        }
                        lib_run(app, syst, m.slots[(sel - MENU_CORE) as usize]);
                    }
                }
            }
            _ => {}
        }
    }
    if explore::on() {
        explore::touch();
    } else {
        explore::voice_key(); // a key in the arena: its quiet minute starts again
    }
    true
}

/// Control action lib: run slot n (or 255: list the slots). Not while a message plays: the
/// slot is read into the shared block, over the store, as a module sent over the air.
/// AGAIN_SLOT (side key 1): the last module again, the image still in the block if it checks
/// (km_again: its header and tag, so a module that wrote over its own code, or anything that
/// used the block since, is refused), else the slot that ran last
#[cfg(feature = "spilib")]
#[inline(never)]
fn lib_run(app: &mut App, syst: &mut SYST, mut n: u8) {
    if n == 255 {
        lib_list();
        return;
    }
    let mut r = LIB_PLAYING;
    if !app.pager.play_pending && !stream_on() {
        r = 0;
        if n == AGAIN_SLOT {
            if unsafe { KEPT } {
                r = unsafe { ffi::km_again(arena::base(), arena::ARENA_BYTES as u32) };
            }
            if r <= 0 {
                n = unsafe { LAST_SLOT }; // 255: none, kl_load refuses it
            }
        }
        if r <= 0 {
            unsafe { ffi::km_reset() };
            r = unsafe { ffi::kl_load(n as i32, &LIB_FL, arena::base(), arena::ARENA_BYTES as u32) };
            unsafe { LAST_SLOT = n };
        }
    }
    rl!("K6 LIB run slot={} r={}", n, r);
    if r > 0 {
        if n != AGAIN_SLOT {
            unsafe { RUN_SLOT = n }; // (side key 1's rerun of the block: where it came from still)
        }
        let played = run_module(app, syst);
        unsafe { KEPT = !played };
    } else if r != LIB_PLAYING {
        clear_store(app); // the slot was read over it
    }
}

// ---- the coded 4FSK receiver (PHY G) ----
//
// A control packet (action 10) asks for it; the sender's 4FSK burst follows on the same
// carrier: a 2 s lead, then each XL packet as a 24-symbol sync word and 1032 coded symbols.
// c/k6/ifpager.c runs the IF matched filter (ifrx.c) on GP0 code 13's stream through DMA
// channel 1, as the iflive module does, decodes each packet (soft Viterbi, rate 1/2 K=7) and
// hands it to sp_packet, so the message, its parity rebuilds and its playback are the usual
// ones. It reaches the RF chip through the module API (receive only), with the PWM ring as its
// DMA ring: nothing plays meanwhile. Not in the lean images.

/// the session's packet results as one character each: sp_packet's code (0 dup, 1 added,
/// 2 new, 3 complete; C a bad CRC, M a bad magic), then the sync words' wrong symbols
#[cfg(not(feature = "lean"))]
fn ifp_code(v: i8) -> u8 {
    match v {
        0..=9 => b'0' + v as u8,
        -1 => b'C',
        -2 => b'M',
        _ => b'?',
    }
}

#[cfg(not(feature = "lean"))]
#[inline(never)] // its locals (the receiver's 2.3 KB) only while it runs
fn if_session(app: &mut App, syst: &mut SYST) {
    let max_ms = core::mem::take(&mut app.pager.ifrx_ms);
    if stream_on() || app.pager.play_pending {
        rl!("K6 IFP refused: playing");
        return;
    }
    if AGAIN && unsafe { KEPT } {
        clear_store(app); // the session's packets need the store
    }
    let gpio = app.radio.fd6818.reg_read(syst, MOD_GPIO);
    let t0 = uptime::now();
    unsafe {
        *addr_of_mut!(MOD_CTX) = ModCtx {
            app: app as *mut App,
            syst: syst as *mut SYST,
            rx_hz: app.master_freq_hz(),
            t_last: t0,
            ticks: 0,
            scratch: arena::end(), // none: the store and the ring are in use
            tx_until: 0,
            tx: false,
            cal_written: true, // no calibration writes
            fl_erases: FL_ERASES, // and no flash writes
            tlm_cap: 0,
        };
    }
    // the chip's packet engine off for the session (no FIFO, no interrupts); arm() after
    disarm(app, syst);
    paint_free_ram();
    let ring = (pwm_audio::idle_bytes() as usize + 3) & !3;
    let mut res: ffi::IfpResult = unsafe { core::mem::zeroed() };
    // the session's 2.7 KB of state and RAM code: in the decoder's pool where the shared block has
    // one (nothing decodes while the session stores; the decoder starts when the message plays),
    // so it isn't on the stack under main's frame; on the stack in the images without (EXPLORE)
    let fl = app.pager.ifrx_flags as i32;
    let r = if arena::DEC_BYTES >= IFP_WORK_MIN {
        if unsafe { ffi::ifp_work_bytes() } as usize <= arena::DEC_BYTES {
            unsafe { ffi::ifp_session_in(&MOD_API, ring as *mut u8, IFRX_WAIT_MS, max_ms, fl, &mut res, arena::dec()) }
        } else {
            -8 // the library's state outgrew the pool: refused, not run on a short stack
        }
    } else {
        unsafe { ffi::ifp_session(&MOD_API, ring as *mut u8, IFRX_WAIT_MS, max_ms, fl, &mut res) }
    };
    unsafe { (*addr_of_mut!(MOD_CTX)).app = core::ptr::null_mut() };
    let sfree = free_ram();
    rl!(
        "K6 IFP ret={} fl={} ms={} syncs={} syms={} lost={} q={} rej={} afc={} idle={} ks={} ram={} gap={}@{} lag={} ld={} stack_free={}",
        r, app.pager.ifrx_flags, res.ms, res.syncs, res.syms, res.lost, res.acq_q, res.acq_rej, res.afc_hz, res.idle_pct, res.ks,
        res.ram, res.gap_us, res.gap_i, res.lag, res.lead_ser, sfree
    );
    #[inline(never)]
    fn report_ifp_pkts(res: &ffi::IfpResult) {
        let n = (res.nres.max(0) as usize).min(64);
        let ns = (res.syncs as usize).min(64);
        let mut codes = [0u8; 64];
        let mut miss = [0u8; 64];
        for i in 0..n {
            codes[i] = ifp_code(res.res[i]);
        }
        for i in 0..ns {
            miss[i] = ifp_code(res.miss[i]);
        }
        rl!(
            "K6 IFP pkts={} r={} miss={}",
            res.nres,
            crate::out::ascii(&codes[..n]),
            crate::out::ascii(&miss[..ns])
        );
    }
    let n = (res.nres.max(0) as usize).min(64);
    report_ifp_pkts(&res);
    // back to the pager's set-up, GP0 as it was
    app.radio.fd6818.reg_write(syst, MOD_GPIO, gpio);
    app.sync_watching_to_master(syst);
    arm(app, syst);
    refresh_status(app);
    if res.res[..n].iter().any(|&v| v >= 0) {
        app.pager.last_ok = true;
        app.pager.ctl_prev = None;
    }
    // the sender has stopped: play now (auto-play and the stream's start wait this long)
    app.pager.idle_ticks = GAP_TICKS;
    app.pager.last_tick = uptime::now();
    report_cfg(app, "ifp");
}

// ---- keys ----

#[inline(never)] // its locals only while it runs, not in main's frame for good
pub(super) fn dispatch(app: &mut App, syst: &mut SYST, ev: KeyEvent) {
    #[cfg(banded)]
    if help_key(app, ev) {
        return;
    }
    #[cfg(all(feature = "explore", feature = "spilib"))]
    if menu_key(app, syst, ev) {
        return;
    }
    #[cfg(feature = "explore")]
    if explore::on() {
        explore::dispatch(app, syst, ev);
        return;
    }
    #[cfg(feature = "explore")]
    explore::voice_key();
    if !matches!(ev.kind, KeyEventKind::Single | KeyEventKind::Repeat) {
        return;
    }
    if app.pager.freq_entry {
        freq_key(app, syst, ev.key);
        return;
    }
    if stream_on() {
        // while a message streams: volume, and MENU stops it
        let s = unsafe { &mut *addr_of_mut!(STREAM) };
        let p = &mut app.pager;
        match ev.key {
            KeyId::Up => p.vol = (p.vol + 1).min(VOL_Q8.len() as u8 - 1),
            KeyId::Down => p.vol = p.vol.saturating_sub(1),
            KeyId::Menu | KeyId::Exit | KeyId::Digit5 => s.aborted = true,
            _ => {}
        }
        s.g = ((s.auto * VOL_Q8[p.vol as usize]) >> 8).min(32767);
        return;
    }
    let p = &mut app.pager;
    match ev.key {
        // In the pager-only image there is nothing to go back to
        #[cfg(not(feature = "pager-only"))]
        KeyId::Exit => {
            exit(app, syst);
            return;
        }
        // the explore image: EXIT opens EXPLORE again (EXIT twice there comes back here); a
        // press, not the repeat of the EXIT that just left it
        #[cfg(feature = "explore")]
        KeyId::Exit if matches!(ev.kind, KeyEventKind::Single) => {
            explore::enter(app, syst);
            return;
        }
        // play on 5 (the release plan, Jeff 2026-11-13: MENU is the module list)
        KeyId::Digit5 => {
            if p.status.have != 0 {
                p.play_pending = true;
            }
        }
        KeyId::Up => p.vol = (p.vol + 1).min(VOL_Q8.len() as u8 - 1),
        KeyId::Down => p.vol = p.vol.saturating_sub(1),
        KeyId::Digit4 => p.emphasis = !p.emphasis,
        KeyId::Digit9 => p.auto_play = !p.auto_play,
        // the arena stays (no fallback to EXPLORE) until EXIT
        #[cfg(feature = "explore")]
        KeyId::Digit0 => explore::stay_set(),
        #[cfg(feature = "hiss")]
        KeyId::Digit6 => {
            let on = unsafe { ffi::sp_hiss() } == 0;
            unsafe { ffi::sp_hiss_on(on as i32) };
            rl!("K6 HISS band={}", on as u8);
        }
        #[cfg(not(feature = "hiss"))]
        KeyId::Digit6 => p.raw_view = !p.raw_view,
        KeyId::Pound => {
            p.freq_entry = true;
            app.input.clear();
        }
        KeyId::Asterisk => clear_store(app),
        #[cfg(feature = "spilib")]
        KeyId::Side1 => lib_run(app, syst, AGAIN_SLOT),
        // A key with no job here does nothing and reports nothing. The receive settings keys
        // that can leave the K6 deaf (2 frames, 3 RX invert, 5 baud, 7 REG_5D length, 8 re-arm
        // delay, 0 receive variant) are gone (Jeff, 2026-11-13; 0 is the arena's stay key now): one stray 7 left the K6
        // rejecting every chunk of an upload. Control packets still set frames, baud and variant.
        _ => return,
    }
    if !matches!(ev.key, KeyId::Digit6 | KeyId::Pound) {
        report_cfg(app, key_name(ev.key));
    }
}

/// The receive settings changed: set the chip up again.
fn settings_changed(app: &mut App, syst: &mut SYST) {
    if app.pager.chip_ok {
        app.pager.last_sync = b'-';
        app.pager.ber = ffi::NO_BITERR;
        app.pager.ber_sum = 0;
        app.pager.ber_n = 0;
        arm(app, syst);
    }
}

/// Drop the stored message (key *, control action clear).
fn clear_store(app: &mut App) {
    unsafe { (KEPT, PLAY_FROM) = (false, [0; FROM_MAX]) };
    let p = &mut app.pager;
    p.store_frames = unsafe { ffi::sp_init(arena::base(), STORE_BYTES as u32) } as u16;
    p.played_id = -1;
    p.last_result = i8::MIN;
    p.ber = ffi::NO_BITERR;
    p.ber_sum = 0;
    p.ber_n = 0;
    refresh_status(app);
}

// ---- control packets (spec section 13) ----
//
// The PC changes the receive settings over the air: a control packet is a whitened L or XL
// payload of repeated 8-byte records (0xA8, seq, mask, values, var | vol << 4, action, CRC).
// Only receive-side settings and play/clear; nothing here transmits or touches the frequency.
// A change of PHY, profile, baud or variant is undone after CTL_REVERT_TICKS without a good
// packet, and a key press wins over it. A record with the seq just applied only confirms.

#[inline(never)] // its locals only while it runs, not in main's frame for good
fn apply_control(app: &mut App, syst: &mut SYST, rec: [u8; 6]) {
    let (seq, mask, val, vv, action) = (rec[1], rec[2], rec[3], rec[4], rec[5]);
    if app.pager.ctl_seq == seq as i16 {
        rl!("K6 CTL seq={} repeat", seq);
        return;
    }
    app.pager.ctl_seq = seq as i16;
    if action == CTL_ACT_BOOT {
        report_boot(seq);
        return;
    }
    if action == CTL_ACT_RESET {
        rl!("K6 RESET seq={}", seq);
        crate::hal::uart::flush();
        delay::ms(syst, 30);
        crate::hal::txdog::reset();
    }
    if action == CTL_ACT_IFRX {
        // a session, once this burst's handling is over (poll runs it)
        app.pager.ifrx_ms = (vv as u32).max(3) * 2000;
        #[cfg(not(feature = "lean"))]
        {
            app.pager.ifrx_flags = val & 7;
        }
        rl!("K6 CTL seq={} ifrx ms={}", seq, app.pager.ifrx_ms);
        app.pager.idle_ticks = 0;
        return;
    }
    #[cfg(feature = "spilib")]
    if action == CTL_ACT_LIB {
        rl!("K6 CTL seq={} lib slot={}", seq, vv);
        lib_run(app, syst, vv);
        return;
    }
    if action == CTL_ACT_REG || action == CTL_ACT_REGS {
        if action == CTL_ACT_REG {
            control_poke(app, syst, mask, (val as u16) << 8 | vv as u16);
        }
        report_regs(app, syst, seq);
        return;
    }
    let p = &mut app.pager;
    let before = (p.xl, p.d2400, p.idle_keep);
    if mask & CTL_FRAMES != 0 {
        p.xl = val & CTL_FRAMES != 0;
    }
    // phy=D with baud=2400: D/2400 for this session; phy=F or baud=1200: F/1800
    let d24 = unsafe { ffi::kr_control(p.d2400 as i32, mask as u32, val as u32) } == KR_D24;
    if d24 && !p.d2400 {
        unsafe { ffi::kr_enter() };
    }
    p.d2400 = d24;
    if mask & CTL_AUTO != 0 {
        p.auto_play = val & CTL_AUTO != 0;
    }
    if mask & CTL_EMPH != 0 {
        p.emphasis = val & CTL_EMPH != 0;
    }
    if mask & CTL_VOL != 0 {
        p.vol = (vv >> 4).min(VOL_Q8.len() as u8 - 1);
    }
    if mask & CTL_IDLE_KEEP != 0 {
        p.idle_keep = val & CTL_IDLE_KEEP != 0;
    }
    let changed = (p.xl, p.d2400, p.idle_keep) != before;
    if changed {
        // keep the oldest settings that worked if a second change comes before a good packet
        if p.ctl_prev.is_none() {
            p.ctl_prev = Some(before);
            p.poke_prev = p.poke_n;
        }
        p.ctl_wait = 0;
    }
    match action {
        1 if app.pager.status.have != 0 => app.pager.play_pending = true,
        2 => clear_store(app),
        CTL_ACT_STREAM_ON | CTL_ACT_STREAM_OFF => {
            let on = action == CTL_ACT_STREAM_ON;
            app.pager.stream_play = on;
            unsafe { ffi::sp_set_stream(on as i32) }; // from the next new message
        }
        _ => {}
    }
    rl!(
        "K6 CTL seq={} mask={:02x} val={:02x} var_vol={:02x} action={} changed={}",
        seq, mask, val, vv, action, changed as u8
    );
    if changed {
        settings_changed(app, syst);
    }
    report_cfg(app, "ctl");
}

/// Action 4: record a register write (or with register 0, drop them all) and re-arm with it.
/// It counts as a change: without a good packet within 60 s every write is undone.
#[inline(never)]
fn control_poke(app: &mut App, syst: &mut SYST, reg: u8, val: u16) {
    let p = &mut app.pager;
    if reg == 0 {
        p.poke_n = 0;
    } else if POKE_OK.contains(&reg) && (p.poke_n as usize) < POKE_MAX {
        if p.ctl_prev.is_none() {
            p.ctl_prev = Some((p.xl, p.d2400, p.idle_keep));
            p.poke_prev = p.poke_n;
        }
        // in order, so a later write to the same register wins
        p.poke[p.poke_n as usize] = (reg, val);
        p.poke_n += 1;
        p.ctl_wait = 0;
    } else {
        return; // not a receive register, or POKE_MAX held: refused (poke= unchanged)
    }
    settings_changed(app, syst);
}

/// `K6 REGS seq=N 43=.. 44=.. 73=.. 7e=.. 96=.. 9b=.. 5e=.. 2b=.. 58=.. 59=.. 5c=.. 5d=.. 72=.. poke=N`: the FSK registers as read.
#[inline(never)]
fn report_regs(app: &mut App, syst: &mut SYST, seq: u8) {
    let mut s = Serial;
    out!(s, "K6 REGS seq={}", seq);
    for a in [REG_RX_BW, REG_DEMOD_GAIN, REG_AFC, REG_DC_FILT, REG_DISC_GAIN, REG_AF_BYPASS, REG_FIFO_THR, REG_AF_FILT, REG_FSK_CFG, REG_FSK_CTRL, REG_FSK_5C, REG_FSK_LEN, REG_BAUD] {
        let v = app.radio.fd6818.reg_read(syst, a);
        out!(s, " {:02x}={:04x}", a, v);
    }
    rl!(" poke={}", app.pager.poke_n);
}

/// Action 6: `K6 BOOT off=XXXX` + 32 bytes in hex per line, 256 lines (~20 KB, ~2 s at 115200),
/// then `K6 BOOT end seq=N`
#[inline(never)]
fn report_boot(seq: u8) {
    let mut s = Serial;
    let mut off = 0;
    while off < BOOT_LEN {
        out!(s, "K6 BOOT off={:04x} ", off);
        for i in 0..32 {
            let b = unsafe { core::ptr::read_volatile((BOOT_BASE + off + i) as *const u8) };
            out!(s, "{:02x}", b);
        }
        out!(s, "\r\n");
        off += 32;
    }
    rl!("K6 BOOT end seq={}", seq);
}

// ---- frequency entry (#) ----
//
// Like Aura's standby VFO entry: six digits in kHz (439500 = 439.500 MHz, 050000 = 50 MHz);
// MENU or # sets it early with the rest as zeros, EXIT deletes a digit or cancels. It sets the
// master side's VFO in RAM only, so nothing is written to flash; at power-on the radio is back
// on the frequency stock Aura saved.

fn digit(key: KeyId) -> Option<u8> {
    Some(match key {
        KeyId::Digit0 => 0,
        KeyId::Digit1 => 1,
        KeyId::Digit2 => 2,
        KeyId::Digit3 => 3,
        KeyId::Digit4 => 4,
        KeyId::Digit5 => 5,
        KeyId::Digit6 => 6,
        KeyId::Digit7 => 7,
        KeyId::Digit8 => 8,
        KeyId::Digit9 => 9,
        _ => return None,
    })
}

fn freq_key(app: &mut App, syst: &mut SYST, key: KeyId) {
    if let Some(d) = digit(key) {
        app.input.push(d);
        if app.input.len >= VFO_INPUT_DIGITS {
            set_freq(app, syst);
        }
        return;
    }
    match key {
        KeyId::Menu | KeyId::Pound => set_freq(app, syst),
        KeyId::Exit if app.input.is_empty() => app.pager.freq_entry = false,
        KeyId::Exit => app.input.backspace(),
        _ => {}
    }
}

fn set_freq(app: &mut App, syst: &mut SYST) {
    if !app.input.is_empty() {
        let khz = app.input.value();
        let s = &mut app.sides[app.master];
        s.vfo_chan = ChVfoMode::Vfo;
        s.rx_freq_hz = khz * 1000;
        s.refresh_cfg_freqs();
        app.sync_watching_to_master(syst);
        if app.pager.chip_ok {
            app.pager.last_sync = b'-';
            arm(app, syst); // retuning resets the RX path: set the FSK modem up again
        }
        report_cfg(app, "freq");
    }
    app.input.clear();
    app.pager.freq_entry = false;
}

pub(super) fn handle_ptt(app: &mut App, _syst: &mut SYST) {
    if let Some(level) = app.radio.poll_ptt() {
        if !level && app.pager.chip_ok && !stream_on() {
            app.pager.cal_pending = true; // main loop draws, then calls cal_tx
        }
    }
}

// ---- playback ----

/// Plays the message through PWM, blocking (up to the message length), with the radio not
/// listening: MENU, or EXIT in the full image, stops it; the power switch stops it too, so main
/// can shut down. The same playback as streaming (stream_begin, stream_fill, stream_tick).
#[inline(never)] // its locals only while it runs, not in main's frame for good
pub(super) fn play(app: &mut App, syst: &mut SYST, power: &PowerSwitch) {
    app.pager.play_pending = false;
    refresh_status(app);
    if app.pager.status.have == 0 || stream_on() {
        return;
    }
    crate::hal::light::touch(); // a message playing lights the screen
    // stop listening: the decoder needs the CPU, and the FIFO would overflow
    disarm(app, syst);
    app.radio
        .fd6818
        .set_af_out(syst, AfOutState::Mute, true, Modulation::Fm);
    stream_begin(app, syst);
    loop {
        if !stream_fill(app, syst) {
            delay::us(syst, 500);
        }
        app.keypad.poll(syst);
        while let Some(ev) = app.keypad.pop_event() {
            dispatch(app, syst, ev);
        }
        if power.switch_off_raw() {
            unsafe { (*addr_of_mut!(STREAM)).aborted = true };
        }
        if stream_tick(app, syst) {
            break;
        }
    }
    app.pager.idle_ticks = 0;
    app.pager.last_tick = uptime::now();
    if app.mode == Mode::Pager && app.pager.chip_ok {
        arm(app, syst);
    }
    // a receiver module's message (sotto_play, KM_PLAY_RERUN): it listens again, unless a key
    // stopped the playback
    let slot = core::mem::replace(unsafe { &mut *addr_of_mut!(RERUN) }, 255);
    #[cfg(feature = "spilib")]
    if slot != 255 && !app.pager.aborted && app.mode == Mode::Pager {
        lib_run(app, syst, slot);
    }
    #[cfg(not(feature = "spilib"))]
    let _ = slot;
}

// ---- streaming playback: the PWM ring refilled from the receive loops ----
//
// Stream mode plays a message while its later interleave groups arrive (sotto 1200 at
// F/1800: 800 ms of speech per 640 ms XL packet). Playback starts once the first group is
// in, the radio stays armed, and the 4-segment PWM ring (80 ms) is refilled wherever the
// receive code would otherwise wait: poll's wait for the next sync, receive_packet's wait for
// the FIFO (fifo_yield keeps the FIFO moving during the decode), and the main loop
// (stream_service). The screen isn't redrawn meanwhile (a redraw takes 30-50 ms).

struct Stream {
    on: bool,
    audio: PwmAudio,
    wr: usize,
    tail: usize,
    done: bool,
    ended: bool,
    aborted: bool,
    auto: i32,
    g: i32,
    emph: i32,
    dsum: u32,
    dmax: u16,
    dn: u32,
    waits: u16,
    late: u16,
    under: u16,
    last_fill: u16,
}

static mut STREAM: Stream = Stream {
    on: false,
    audio: PwmAudio::new(),
    wr: 0,
    tail: 0,
    done: false,
    ended: false,
    aborted: false,
    auto: 256,
    g: 256,
    emph: 0,
    dsum: 0,
    dmax: 0,
    dn: 0,
    waits: 0,
    late: 0,
    under: 0,
    last_fill: 0,
};

fn stream_on() -> bool {
    unsafe { (*addr_of!(STREAM)).on }
}

/// One PWM segment: the next 20 ms decoded (with the FIFO yield in force), silence while its
/// group isn't in yet, or silence after the end.
#[inline(never)]
fn fill_seg(app: &mut App, syst: &mut SYST, i: usize) {
    let s = unsafe { &mut *addr_of_mut!(STREAM) };
    let seg = s.audio.segment(i);
    if s.done {
        seg.fill(pwm_audio::MID);
        s.tail += 1;
        return;
    }
    unsafe {
        Y_FD = &mut app.radio.fd6818 as *mut _;
        Y_SYST = syst as *mut _;
        Y_IRQN = app.pager.irq_words() as u8;
        Y_LAST = uptime::now();
        ffi::sp_set_yield(Some(fifo_yield));
    }
    let t = uptime::now();
    let more = unsafe { ffi::sp_play_pwm(seg.as_mut_ptr(), pwm_audio::TOP as u32, s.g, s.emph) };
    let dt = uptime::now().wrapping_sub(t);
    unsafe { ffi::sp_set_yield(None) };
    match more {
        1 => {
            s.dsum += dt as u32;
            s.dmax = s.dmax.max(dt);
            s.dn += 1;
        }
        2 => s.waits = s.waits.saturating_add(1),
        _ => {
            s.done = true;
            seg.fill(pwm_audio::MID);
            s.tail += 1;
        }
    }
}

/// Refill the segment the DMA has left, if any: true if it did (a decode's time went by).
#[inline(never)]
fn stream_fill(app: &mut App, syst: &mut SYST) -> bool {
    let (on, wr, cur) = unsafe {
        let s = &*addr_of!(STREAM);
        (s.on, s.wr, s.audio.play_segment())
    };
    if !on || wr == cur {
        return false;
    }
    let now = uptime::now();
    unsafe {
        let s = &mut *addr_of_mut!(STREAM);
        if !s.done && now.wrapping_sub(s.last_fill) > LATE_TICKS {
            s.late = s.late.saturating_add(1);
        }
    }
    fill_seg(app, syst, wr);
    unsafe {
        let s = &mut *addr_of_mut!(STREAM);
        // the DMA got to the segment being written, or past it, before the fill ended: what
        // played there was stale or half new. `late` above misses this (a long decode after
        // a fill in time), which is how the hiss image's streaming popped on dense speech
        // with no count on 2026-11-07
        let n = pwm_audio::NSEG;
        let cur2 = s.audio.play_segment();
        if !s.done && (cur2 + n - cur) % n >= (wr + n - cur) % n {
            s.under = s.under.saturating_add(1);
        }
        s.wr = (wr + 1) % n;
        s.last_fill = uptime::now();
    }
    true
}

/// The first group is in: start playing, and keep receiving.
#[inline(never)]
fn stream_begin(app: &mut App, syst: &mut SYST) {
    refresh_status(app);
    app.pager.played_id = app.pager.status.msg_id as i16;
    let ring = unsafe { ffi::sp_stream_info(core::ptr::null_mut()) };
    rl!(
        "K6 STEP stream id={:02x} total={} got={} ring={}",
        app.pager.status.msg_id, app.pager.status.total, app.pager.status.frames_got, ring
    );
    {
        let s = unsafe { &mut *addr_of_mut!(STREAM) };
        s.auto = unsafe { ffi::sp_auto_gain_q8() };
        s.g = ((s.auto * VOL_Q8[app.pager.vol as usize]) >> 8).min(32767);
        s.emph = app.pager.emphasis as i32;
        (s.wr, s.tail, s.done, s.ended, s.aborted) = (0, 0, false, false, false);
        (s.dsum, s.dmax, s.dn, s.waits, s.late, s.under) = (0, 0, 0, 0, 0, 0);
        s.on = true;
        unsafe {
            Y_SERVICED = 0;
            ffi::sp_play_start();
        }
        // the library's frame must be the ring's segment (8 or 16 kHz): else nothing plays
        let fs = unsafe { ffi::sp_frame_samples() } as usize;
        if fs != pwm_audio::SEG {
            rl!("K6 PWM frame {} != segment {}", fs, pwm_audio::SEG);
            s.done = true;
        }
    }
    paint_free_ram();
    for i in 0..pwm_audio::NSEG {
        fill_seg(app, syst, i);
    }
    let s = unsafe { &mut *addr_of_mut!(STREAM) };
    s.audio.start();
    // as stock: let the RC filter settle before the amplifier opens, taking the RF chip's FIFO
    // words meanwhile (a packet is arriving: 30 ms is 3 words at 1800 baud)
    for _ in 0..15 {
        delay::ms(syst, 2);
        unsafe {
            Y_LAST = uptime::now().wrapping_sub(YIELD_TICKS);
            fifo_yield();
        }
    }
    app.radio.set_speaker(true);
    s.last_fill = uptime::now();
}

#[inline(never)]
fn stream_finish(app: &mut App, syst: &mut SYST) {
    app.radio.set_speaker(false);
    delay::ms(syst, 20);
    let s = unsafe { &mut *addr_of_mut!(STREAM) };
    s.audio.stop();
    s.on = false;
    let mut ov = 0i32;
    unsafe { ffi::sp_stream_info(&mut ov) };
    let p = &mut app.pager;
    p.aborted = s.aborted;
    p.played_frames = unsafe { ffi::sp_play_pos() } as u16;
    p.dec_max = s.dmax;
    p.dec_mean_x10 = if s.dn > 0 { (s.dsum * 10 / s.dn) as u16 } else { 0 };
    p.waits = s.waits;
    p.late = s.late;
    p.under = s.under;
    p.overruns = ov.clamp(0, 65535) as u16;
    p.ram_free = free_ram();
    refresh_status(app);
    report_play(app);
    app.pager.idle_ticks = 0;
    app.pager.last_tick = uptime::now();
}

/// The main loop's turn: refill, notice the sender has stopped, finish. True if it decoded.
#[inline(never)]
pub(super) fn stream_service(app: &mut App, syst: &mut SYST) -> bool {
    if app.mode != Mode::Pager || !app.pager.stream_play {
        return false;
    }
    let st = &app.pager.status;
    if !stream_on() {
        // a short message whose first group never filled (a packet lost): start once the
        // sender has stopped
        if st.have != 0
            && st.frames_got > 0
            && app.pager.auto_play
            && app.pager.played_id != st.msg_id as i16
            && app.pager.idle_ticks >= STREAM_END_TICKS
        {
            unsafe { ffi::sp_stream_end() };
            if unsafe { ffi::sp_stream_ready() } != 0 {
                stream_begin(app, syst);
            }
        }
        return false;
    }
    let s = unsafe { &mut *addr_of_mut!(STREAM) };
    if !s.ended && app.pager.idle_ticks >= STREAM_END_TICKS {
        s.ended = true;
        unsafe { ffi::sp_stream_end() };
    }
    let filled = stream_fill(app, syst);
    stream_tick(app, syst);
    filled
}

/// Finish a playback that is over (or stopped, or whose DMA has stalled): true once it is.
fn stream_tick(app: &mut App, syst: &mut SYST) -> bool {
    let s = unsafe { &mut *addr_of_mut!(STREAM) };
    if !s.on {
        return true;
    }
    // a DMA that doesn't move (PWM path not running) would hold playback for good
    if uptime::now().wrapping_sub(s.last_fill) > STALL_TICKS {
        s.aborted = true;
        rl!("K6 PWM stalled cndtr={}", s.audio.probe().0);
    }
    if s.tail >= pwm_audio::NSEG || s.aborted {
        stream_finish(app, syst);
        return true;
    }
    false
}

pub(super) fn streaming() -> bool {
    stream_on()
}

// ---- calibration transmit (spec section 8 step 2) ----

fn tx_packet(app: &mut App, syst: &mut SYST, words: &[u16; 32]) {
    let fd = &mut app.radio.fd6818;
    fd.reg_write(syst, REG_IRQ_EN, IRQ_TX_DONE);
    fd.reg_write(syst, REG_FSK_CTRL, CTRL_BASE | CTRL_CLR_TX | CTRL_CLR_RX);
    fd.reg_write(syst, REG_FSK_CTRL, CTRL_BASE);
    for &w in words.iter() {
        fd.reg_write(syst, REG_FIFO, w);
    }
    delay::ms(syst, 20); // as the stock MDC path
    fd.reg_write(syst, REG_FSK_CTRL, CTRL_BASE | CTRL_TX_EN);
    for _ in 0..200 {
        delay::ms(syst, 5);
        if fd.reg_read(syst, REG_STATUS) & 1 != 0 {
            break;
        }
    }
    fd.reg_write(syst, REG_IRQ_FLAGS, 0);
    fd.reg_write(syst, REG_FSK_CTRL, CTRL_BASE);
}

/// Keys up on the master side's TX frequency and power (FM, no CTCSS) and
/// sends test packet 0 of the spec (profile L) twice on the rung in force: F/1800, or D/2400
/// while a session has it (the plan's step 3: does the IC-7100 decode the K6's D/2400).
/// (Not in the `lean` builds: the codebook install image, flashed once, and the sotto 1200
/// fallback with the table in MCU flash.)
#[cfg(feature = "lean")]
pub(super) fn cal_tx(app: &mut App, _syst: &mut SYST) {
    app.pager.cal_pending = false;
}

#[cfg(not(feature = "lean"))]
#[inline(never)] // its locals only while it runs, not in main's frame for good
pub(super) fn cal_tx(app: &mut App, syst: &mut SYST) {
    app.pager.cal_pending = false;
    let mut bytes = [0u8; 64];
    unsafe { ffi::sp_test_packet(bytes.as_mut_ptr()) };
    let mut words = [0u16; 32];
    for (i, w) in words.iter_mut().enumerate() {
        // low byte first: the chip sends each FIFO word's low byte first
        *w = bytes[2 * i] as u16 | (bytes[2 * i + 1] as u16) << 8;
    }

    let (rx_hz, tx_hz, power) = {
        let s = &app.sides[app.master];
        (s.cfg.freq_hz, s.cfg.tx_freq_hz, s.cfg.power)
    };
    app.radio.set_frequency(rx_hz);
    app.radio.set_tx_frequency(tx_hz);
    app.radio.set_power(power);
    app.radio.set_subaudio_tx(SubAudio::None);
    app.radio.set_modulation(Modulation::Fm);
    app.radio
        .apply_pa_calibration(&mut app.storage, tx_hz, power);
    disarm(app, syst);
    let ok = app.radio.enter_tx(syst);
    crate::hal::txdog::limit(20_000); // 300 ms lead-in and four 64-byte packets
    if !ok {
        app.pager.cal_result = b'X';
        rl!("K6 CAL refused tx={}", tx_hz);
        app.sync_watching_to_master(syst);
        arm(app, syst);
        return;
    }
    delay::ms(syst, 300); // carrier lead-in for the IC-7100's AGC
    // REG_5D sets the TX length too: 64 bytes, whatever profile a control packet chose
    let saved = app.pager.xl;
    app.pager.xl = false;
    for _ in 0..2 {
        configure(app, syst);
        tx_packet(app, syst, &words);
        delay::ms(syst, 100);
    }
    app.pager.xl = saved;
    disarm(app, syst);
    app.radio.enter_rx(syst); // not end_tx: no roger tone after the test
    app.sync_watching_to_master(syst);
    app.pager.cal_result = b'K';
    rl!("K6 CAL sent tx={}", tx_hz);
    arm(app, syst);
}

// ---- ARQ: the K6 answers a reply-wanted upload over the air (k6_mod.h, KM_MAGIC_REPORT) ----
//
// An upload whose chunks come as magic 0xAE asks for answers. Two seconds after the sender's
// last packet, with the channel clear, the K6 keys up on 440.850 MHz (only there: Jeff's
// bench frequency, AG7EW), low power, and sends a 0xAF status packet twice in the pager's own
// PHY and baud rate (64 bytes, through the chip's FSK modem as the calibration transmit), then
// AG7EW in MCW on the tone oscillator. Partway, the status lists the chunks still missing,
// and the sender resends only those; once the module has run, it carries the module's return,
// its time and stack_free, followed by its say lines as text packets if the header asked for
// them. A repeat of a finished transfer's chunk brings the result again. At most ARQ_MAX
// reports a transfer. Jeff OK'd the K6 answering uploads on 2026-11-06.

/// ARQ state, in .bss rather than PagerState (whose initialiser costs flash a field)
struct Arq {
    /// ARQ (uploads sent with magic 0xAE): the report the K6 owes the sender, 0 none, 1 the
    /// chunks still missing, 2 the result; 100 us ticks since the last packet heard and spent
    /// waiting for a clear channel; reports sent for this transfer; the report counter; the
    /// result to send (k6_mod.h KM_ST_*, the module's return, ms, stack_free)
    owe: u8,
    wait: u32,
    busy: u32,
    sent: u8,
    seq: u8,
    state: u8,
    ret: i32,
    ms: u32,
    sfree: u16,
    /// the transfer they are for (xfer << 8 | count; count >= 1, so 0 = none)
    xfer: u16,
}

static mut ARQ: Arq = Arq { owe: 0, wait: 0, busy: 0, sent: 0, seq: 0, state: 0, ret: 0, ms: 0, sfree: 0, xfer: 0 };

fn arq() -> &'static mut Arq {
    unsafe { &mut *addr_of_mut!(ARQ) }
}

const KM_ST_PARTWAY: u8 = 0;
const KM_ST_RAN: u8 = 1;
const KM_ST_REFUSED: u8 = 2;
const KM_TLM_PART: usize = 57;
const KM_TLM_MAX: usize = 12;
/// the sender's carrier gone and the IC-7100's receive audio back (muted ~1.8 s after its unkey;
/// the K6's key-up takes ~1.4 s more before the first bit)
const ARQ_WAIT_TICKS: u32 = 20_000;
const ARQ_CLEAR_DBM: i32 = -90;
/// a channel that stays busy this long: no report
const ARQ_GIVE_UP_TICKS: u32 = 300_000;
const ARQ_MAX: u8 = 16;
const ARQ_HZ: u32 = 440_850_000;
const ARQ_CALL: &[u8] = b"AG7EW\0";
/// MCW ID: 25 wpm (48 ms a unit), 800 Hz on tone 1 at REG_70 gain 60 (ft8tx's, about -18 dBFS
/// of tone on the IC-7100's USB audio)
const MCW_UNIT_MS: u32 = 48;
const MCW_WORD: u16 = 8260; // 800 Hz x 10.32444
const MCW_ON: u16 = 0x8000 | 60 << 8;
/// Morse by code, as c/k6/mod/cwkey.c: start at 1, then 0 a dot and 1 a dash (E = 2, A = 5)
const MORSE_TAB: &[u8] = b"~~ETIANMSURWDKGOHVF~L~PJBXCYZQ~~54~3~~~2~~+~~~~16=/~~~~~7~~~8~90";

/// The module packets owe the sender a report: the gaps (partway) or the result
#[cfg(not(feature = "lean"))]
fn arq_owe(state: u8, ret: i32, ms: u32, sfree: u16) {
    let st = unsafe { &*ffi::km_status() };
    let id = (st.xfer as u16) << 8 | st.count as u16;
    let p = arq();
    if id != p.xfer {
        p.xfer = id;
        p.sent = 0;
    }
    p.owe = if state == KM_ST_PARTWAY { 1 } else { 2 };
    p.wait = 0;
    p.busy = 0;
    p.state = state;
    p.ret = ret;
    p.ms = ms;
    p.sfree = sfree;
}

#[cfg(not(feature = "lean"))]
fn arq_poll(app: &mut App, syst: &mut SYST, dt: u32) {
    let p = arq();
    p.wait = p.wait.saturating_add(dt);
    if p.wait < ARQ_WAIT_TICKS || app.pager.play_pending || stream_on() {
        return;
    }
    let rssi = ((app.radio.fd6818.reg_read(syst, 0x67) & 0x1FF) >> 1) as i32 - 174;
    if rssi >= ARQ_CLEAR_DBM {
        p.busy = p.busy.saturating_add(dt);
        if p.busy >= ARQ_GIVE_UP_TICKS {
            p.owe = 0;
            rl!("K6 ARQ gave up: the channel stayed busy ({} dBm)", rssi);
        }
        return;
    }
    arq_send(app, syst);
}

/// The longest an ARQ report key-up may take (lead-in, 2 status packets, up to KM_TLM_MAX
/// telemetry parts and the CW ID come to about 12 s); hal::txdog reboots past it
const ARQ_TX_MAX_MS: u32 = 30_000;

#[cfg(not(feature = "lean"))]
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn arq_send(app: &mut App, syst: &mut SYST) {
    let kind = arq().owe;
    arq().owe = 0;
    arq().busy = 0;
    if arq().sent >= ARQ_MAX {
        rl!("K6 ARQ no report: {} sent for this transfer already", ARQ_MAX);
        return;
    }
    let hz = app.master_freq_hz();
    if hz != ARQ_HZ {
        rl!("K6 ARQ no report: on {} Hz; reports go on 440.850 MHz only", hz);
        return;
    }
    // never key up on RAM a stack overrun may have trashed, nor on a status that can't be true
    ram_check(free_ram());
    let st = unsafe { &*ffi::km_status() };
    if st.have > st.count {
        rl!("K6 ARQ no report: status corrupt (have={}/{})", st.have, st.count);
        return;
    }
    arq().sent += 1;
    let state = if kind == 1 { KM_ST_PARTWAY } else { arq().state };
    let cap = (st.tlm as usize * KM_TLM_PART).min(arena::TLM_BYTES - TLM_HDR);
    let tlen = if kind == 2 && state == KM_ST_RAN && cap > 0 { tlm_len() } else { 0 };
    let nparts = ((tlen + KM_TLM_PART - 1) / KM_TLM_PART).min(KM_TLM_MAX);
    let parts = 1 + nparts as u8;
    let seq = arq().seq;
    arq().seq = seq.wrapping_add(1);
    let (ret, ms, sfree) = (arq().ret, arq().ms, arq().sfree);
    rl!(
        "K6 ARQ tx seq={} state={} xfer={} have={}/{} r={} parts={}",
        seq, state, st.xfer, st.have, st.count, st.last, parts
    );

    disarm(app, syst);
    app.radio.set_frequency(hz);
    app.radio.set_tx_frequency(hz);
    app.radio.set_power(crate::drivers::fd6818::Power::Low);
    app.radio.set_subaudio_tx(SubAudio::None);
    app.radio.set_modulation(Modulation::Fm);
    app.radio.apply_pa_calibration(&mut app.storage, hz, crate::drivers::fd6818::Power::Low);
    // the module transmitter's band lock (FCC HAM, Jeff's choice for AG7EW), for this key-up only
    app.radio.set_tx_allowed(BandLock::Fcc.tx_ranges());
    let ok = app.radio.enter_tx(syst);
    app.radio.set_tx_allowed(BandLock::from_u8(app.settings.band_lock).tx_ranges());
    if !ok {
        rl!("K6 ARQ tx refused hz={}", hz);
        app.radio.enter_rx(syst);
        app.sync_watching_to_master(syst);
        arm(app, syst);
        return;
    }
    crate::hal::txdog::limit(ARQ_TX_MAX_MS);
    let t0 = uptime::now();
    delay::ms(syst, 300); // carrier lead-in for the IC-7100's AGC and squelch
    // REG_5D sets the TX length too: 64 bytes, whatever profile a control packet chose. The
    // reports go at F/1800 on every rung (the plan's open question 2: short, little to gain,
    // and every sender hears F/1800), and say the rung the K6 is on and whether it fell back
    // (byte 9's high bits, k6_mod.h KM_RATE_*), so the sender follows
    let saved = (app.pager.xl, app.pager.d2400);
    app.pager.xl = false;
    app.pager.d2400 = false;
    configure(app, syst);
    let rate = KM_RATE_D24
        | if saved.1 { KM_RATE_AT_D24 } else { 0 }
        | ((unsafe { ffi::kr_take_why() } as u8) << KM_RATE_WHY_SHIFT);
    let mut pk = [0u8; 64];
    for _ in 0..2 {
        unsafe { ffi::km_report_status(pk.as_mut_ptr(), seq, parts, state | rate, ARQ_CALL.as_ptr(), ret, ms, sfree) };
        tx_bytes(app, syst, &pk);
        delay::ms(syst, 60);
    }
    for i in 0..nparts {
        let mut part = [0u8; KM_TLM_PART];
        let n = tlm_read(i * KM_TLM_PART, &mut part, cap);
        unsafe { ffi::km_report_text(pk.as_mut_ptr(), seq, i as u8 + 1, parts, part.as_ptr(), n as u32) };
        tx_bytes(app, syst, &pk);
        delay::ms(syst, 40);
    }
    (app.pager.xl, app.pager.d2400) = saved;
    disarm(app, syst);
    mcw_id(app, syst);
    app.radio.enter_rx(syst); // not end_tx: no roger tone
    let power = app.sides[app.master].cfg.power;
    app.radio.set_power(power);
    let on = uptime::now().wrapping_sub(t0) / 10;
    rl!("K6 ARQ sent seq={} ms={}", seq, on);
    arq().wait = 0;
    app.sync_watching_to_master(syst);
    arm(app, syst);
}

/// 64 bytes through the chip's FSK modem, low byte of each FIFO word first (as cal_tx)
#[cfg(not(feature = "lean"))]
fn tx_bytes(app: &mut App, syst: &mut SYST, b: &[u8; 64]) {
    let mut words = [0u16; 32];
    for (i, w) in words.iter_mut().enumerate() {
        *w = b[2 * i] as u16 | (b[2 * i + 1] as u16) << 8;
    }
    tx_packet(app, syst, &words);
}

/// The station's call in MCW: the 800 Hz tone keyed on tone 1 (REG_70), the mic off
#[cfg(not(feature = "lean"))]
fn mcw_id(app: &mut App, syst: &mut SYST) {
    let fd = &mut app.radio.fd6818;
    fd.reg_write(syst, 0x30, MOD_TX_STATE);
    fd.reg_write(syst, REG_TX_GAIN, 0);
    fd.reg_write(syst, 0x71, MCW_WORD);
    delay::ms(syst, 200);
    for &c in &ARQ_CALL[..ARQ_CALL.len() - 1] {
        let code = MORSE_TAB.iter().position(|&m| m == c && m != b'~').unwrap_or(0) as u32;
        if code < 2 {
            continue;
        }
        let mut b = 31 - code.leading_zeros(); // below the start bit
        while b > 0 {
            b -= 1;
            let n = if code >> b & 1 != 0 { 3 } else { 1 };
            fd.reg_write(syst, REG_TX_GAIN, MCW_ON);
            delay::ms(syst, n * MCW_UNIT_MS);
            fd.reg_write(syst, REG_TX_GAIN, 0);
            delay::ms(syst, if b > 0 { MCW_UNIT_MS } else { 3 * MCW_UNIT_MS });
        }
    }
    fd.reg_write(syst, 0x71, 0);
}

// ---- serial report (programming cable, radio to PC only) ----
//
// One text line per event on USART1 TX (PA9; main set it up at 115200 8N1), for
// sotto's `python -m modem.k6_harness` on the PC. Nothing is read back: the
// cable's other wire is PA10, the PTT line, so the PC must never send. A
// 64-byte packet's line is about 230 characters, 20 ms, sent after RX is
// re-armed and well inside the next packet's 107 ms preamble.

// (crate::out, no core::fmt: queued, sent by the USART interrupt, hal::uart, so a report line
// doesn't hold the caller for its 87 us a character)
use crate::out::Uart as Serial;

fn key_name(k: KeyId) -> &'static str {
    match k {
        KeyId::Digit4 => "key4",
        KeyId::Digit9 => "key9",
        KeyId::Asterisk => "clear",
        KeyId::Digit5 => "play",
        KeyId::Up | KeyId::Down => "vol",
        _ => "key",
    }
}

pub(super) fn note_draw(app: &mut App, ticks: u16) {
    app.pager.draw_max = app.pager.draw_max.max(ticks);
}

/// The link settings, after `enter`, a key or a new frequency.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn report_cfg(app: &mut App, why: &str) {
    app.pager.cfg_ticks = 0;
    let p = &app.pager;
    let idle_rd = p.idle_rd;
    let baud = p.baud();
    rl!(
        "K6 CFG why={} chip={:04x} freq={} phy={} frames={} baud={} rates=F12,F18,D24 stream={} reg58={:04x} reg2b={:04x} inv={} soft={} swap={} emph={} vol={} len5d={:04x} keep={} idlekeep={} idlerd={} timed={} rearm={} auto={} wd={} splay={} draw={} bl={} rssi={} batt={} build={}",
        why,
        app.chip_id,
        app.master_freq_hz(),
        if p.d2400 { 'D' } else { 'F' },
        if p.xl { "XL" } else { "L" },
        baud,
        p.stream() as u8,
        p.fsk_cfg().0, // REG_58 as written (a 58 write in force overrides it)
        p.reg2b_base.unwrap_or(0) | p.af_bits(),
        p.invert as u8,
        p.soft_inv as u8,
        p.swap as u8,
        p.emphasis as u8,
        p.vol_db(),
        p.len_field(),
        p.keep_after_done as u8,
        p.idle_keep as u8,
        idle_rd,
        p.timed() as u8,
        p.rearm_label(),
        p.auto_play as u8,
        p.watchdog as u8,
        p.stream_play as u8,
        p.draw_max / 10,
        crate::hal::light::code(), // the backlight's idle setting (hal::light::SECS)
        p.idle_rssi,                // dBm at the last idle tick
        app.battery_voltage_cv() as u32 * 10, // mV, as of the last idle tick
        BUILD
    );
    app.pager.idle_rd = 0;
}

/// One packet: the bytes as read (with the byte swap `swap` was then, before
/// any inversion), what the radio made of it, and its bit errors against the
/// test message. `res` is sp_packet's result, None for a short packet.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn report_packet(app: &mut App, read: &[u8], swap: bool, res: Option<i32>) {
    let p = &mut app.pager;
    p.reported = p.reported.wrapping_add(1);
    let p = &app.pager;
    let st = &p.status;
    let mut s = Serial;
    out!(s, "K6 PKT n={} got={} sync={} rssi={} noise={} ", p.reported, p.raw_len, p.last_sync as char, p.sync_rssi, p.sync_noise);
    match res {
        Some(r) => out!(s, "res={} magic={:02x} ", r, p.last_magic),
        None => out!(s, "res=short "),
    };
    out!(s, "rswap={} soft={} swap={} ", swap as u8, p.soft_inv as u8, p.swap as u8);
    if res.is_some() && p.ber.errors >= 0 {
        let e = &p.ber;
        out!(s, "be={} bseq={} bswap={} binv={} blk=", e.errors, e.seq, e.swap, e.invert);
        for (i, n) in e.block.iter().enumerate() {
            out!(s, "{}{}", if i > 0 { "," } else { "" }, n);
        }
        out!(s, " ");
    }
    out!(s, "ev=");
    for (i, &(f, t, n)) in p.ev[..p.ev_n as usize].iter().enumerate() {
        // interrupt flags in hex : ms since the packet's first interrupt : words read
        out!(s, "{}{:x}:{}:{}", if i > 0 { "," } else { "" }, f, t / 10, n);
    }
    out!(s, " gap={} stray={} rearm={}", p.gap_ms, p.stray_ms, p.rearm_before);
    if p.stream() {
        out!(s, " restarts={}", p.st_restarts);
    }
    out!(s, " good={} bad={} to={} spur={}", st.good_packets, st.bad_packets, p.timeouts, unsafe { Y_SPUR });
    // the bytes only for a packet that failed (about 130 characters, 11 ms at 115200): this line
    // goes out between back-to-back packets, and at 2400 baud that quiet stretch is ~45 ms. At
    // 2400 baud only the first 64 bytes (an XL packet's 256 hex digits alone take 22 ms)
    if !matches!(res, Some(r) if r >= 0) {
        out!(s, " raw=");
        let n = if p.baud() > 1200 { read.len().min(64) } else { read.len() };
        for b in &read[..n] {
            out!(s, "{:02x}", b);
        }
    }
    out!(s, "\r\n");
}

/// After a playback: how much arrived and how long the decoder took.
#[inline(never)] // its locals only while it runs, not in main's frame for good
fn report_play(app: &App) {
    let p = &app.pager;
    let st = &p.status;
    let mut missing = [0u16; 8];
    let n_missing = unsafe { ffi::sp_missing(missing.as_mut_ptr(), missing.len() as i32) };
    // a checksum of the samples decoded, before PWM: tools/k6_pcmhash.py gets the same
    // from the PC's copy of the decoder when the radio played the right audio
    let mut samples = 0u32;
    let pcm = unsafe { ffi::sp_pcm_hash(&mut samples) };
    // the link since the last PLAY line: synced packets, CRC failures, RSSI (k6_rate.c)
    let kr = unsafe { &*ffi::kr_state() };
    let mut s = Serial;
    out!(
        s,
        "K6 PLAY id={:02x} total={} stored={} got={} packets={}/{} played={} dec_mean_us={} dec_max_us={} ram_free={} aborted={} stream={} waits={} late={} under={} overruns={} yields={} pcm={:08x} samples={} rebuilt={} rung={} syncs={} crcbad={} rssi_min={} rssi_med={} missing={}",
        st.msg_id,
        st.total,
        st.stored,
        st.frames_got,
        st.packets_got,
        st.packets_total,
        p.played_frames,
        p.dec_mean_x10 as u32 * 10,
        p.dec_max as u32 * 100,
        p.ram_free,
        p.aborted as u8,
        (unsafe { ffi::sp_stream_info(core::ptr::null_mut()) } != 0 && p.stream_play) as u8,
        p.waits,
        p.late,
        p.under,
        p.overruns,
        unsafe { Y_SERVICED },
        pcm,
        samples,
        unsafe { ffi::sp_rebuilt() },
        if p.d2400 { "D24" } else { "F18" },
        kr.syncs,
        kr.bad,
        kr.rssi_min,
        unsafe { ffi::kr_rssi_median() },
        n_missing
    );
    // the seqs not received (the first 8), e.g. missing=2:3,17
    for (i, q) in missing.iter().take(n_missing.clamp(0, 8) as usize).enumerate() {
        out!(s, "{}{}", if i == 0 { ':' } else { ',' }, q);
    }
    out!(s, "\r\n");
    unsafe { ffi::kr_link_reset() };
}

/// A CPU fault (a bad address, say): note the pc and lr and reboot at once, which turns the
/// transmitter off; the next boot prints `K6 FAULT last boot pc= lr=`. This runs because
/// hal::txdog maps a vector table in SRAM at address 0 (no VTOR here); without it the
/// bootloader's HardFault (`b .`) hangs the radio, and `K6 STEP` lines say where it stopped.
#[cfg(target_arch = "arm")]
#[cortex_m_rt::exception]
unsafe fn HardFault(ef: &cortex_m_rt::ExceptionFrame) -> ! {
    crate::hal::txdog::fault(ef.pc(), ef.lr())
}

/// A Rust panic (a slice index out of range, say) used to stop the radio silently in
/// panic_halt's loop: say where on the serial report, then reboot. The report gives code
/// addresses, not the file and line: the panic's location records and message formatting cost
/// about 3 KB of flash (docs/k6-compilers.md). `K6 PANIC at a b c d` lists the first return
/// addresses on the stack (the panic call chain, innermost first); look them up against the
/// image's .elf with `llvm-addr2line -fipe out/NAME.elf a b c d`.
#[cfg(target_arch = "arm")]
#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    cortex_m::interrupt::disable();
    let mut at = [0u32; 4];
    let mut n = 0;
    let sp = cortex_m::register::msp::read();
    let mut a = sp & !3;
    // Thumb return addresses into the application's flash, in the 256 bytes above the stack
    // pointer (below hal::txdog's note at the top of SRAM)
    while n < at.len() && a < sp + 256 && a < 0x2000_5FF0 {
        let w = unsafe { core::ptr::read_volatile(a as *const u32) };
        if w & 1 == 1 && (0x0800_2000..0x0802_0000).contains(&w) {
            at[n] = w & !1;
            n += 1;
        }
        a += 4;
    }
    rl!(
        "K6 PANIC at {:08x} {:08x} {:08x} {:08x} build={}",
        at[0], at[1], at[2], at[3], BUILD
    );
    crate::hal::uart::flush();
    for _ in 0..2_000_000 {
        cortex_m::asm::nop();
    }
    crate::hal::txdog::reset()
}

/// Sizes for the build report (build.sh reads them from the image): the App, which lives in
/// main's stack frame, and its biggest parts.
#[cfg(target_arch = "arm")]
#[no_mangle]
#[used]
pub static K6_SIZES: [u32; 6] = [
    core::mem::size_of::<App>() as u32,
    core::mem::size_of::<PagerState>() as u32,
    core::mem::size_of::<crate::flash_map::Settings>() as u32,
    core::mem::size_of::<crate::device::storage::Storage>() as u32,
    core::mem::size_of::<crate::device::radio::Radio>() as u32,
    core::mem::size_of::<crate::device::keypad::Keypad>() as u32,
];
