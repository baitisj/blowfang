//! EXPLORE: the K6 signal explorer's list of likely repeaters, in the pager image (sotto's
//! docs/k6-explore-app.md; the design is the "K6 signal explorer" plan, Phase 3).
//!
//! The radio only listens. It sweeps the bands a block of bins at a time (c/k6/xscan.c, the
//! scan module's sweep), turns every transmission it follows into a candidate (c/k6/explore.c:
//! same level every time, the band plan, an ID rhythm, a CW ID) and ranks them:
//!
//! ```text
//! EX ALL 11 2.3s -93
//! >R 146.940 -0.6  -80
//!  r 443.100 +5.0  -85
//!  ? 440.850 +5.0  -22
//!  s 146.520  S    -70
//! n4 sc8 W7ABC/R
//! ```
//!
//! Keys: UP/DOWN pick a row; MENU Try (listen to it: the sweep stops, the speaker opens);
//! 0 Listen for ID (the CW ID decoder, c/k6/xlisten.c, up to 25 minutes, any key stops);
//! 5 Save (the repeater table in SPI flash, c/k6/rt.c, through module API level 5's checks);
//! * ignore; # the next band set; 1 automatic Listen for ID on/off; 9 its speaker on/off (on
//! at start); 3 twice on a saved row deletes its table entry; 7 the list and the table on the serial report (K6 CAND,
//! K6 RT lines); EXIT twice back to VOICE MSG (the list is in RAM only and goes with it).
//!
//! RAM: none of its own beyond this file's statics. While EXPLORE runs it borrows the message
//! store (the sweep state and 64 candidates) and the PWM ring (the floors, the waterfall row
//! and Listen for ID's state), the way a RAM module or the 4FSK session does; leaving clears
//! the store for messages again.

use super::{arm, clear_store, disarm, ffi as pffi, mod_tick, stream_on, MOD_API, MOD_CTX};
use super::{FL_ERASES, STORE_BYTES};
use crate::hal::arena;
use crate::app::App;
use crate::device::keypad::{KeyEvent, KeyEventKind, KeyId};
use crate::drivers::fd6818::AfOutState;
use crate::device::radio::Modulation;
use crate::hal::{pwm_audio, uptime};
use crate::ui::TextBuf;
use core::mem::MaybeUninit;
use core::ptr::{addr_of, addr_of_mut, null_mut};
use cortex_m::peripheral::SYST;
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::Rectangle;
use crate::ui::glyph;

/// The sweep's and Listen for ID's C functions: declared as linked in, or (the 8 kHz voice +
/// EXPLORE image) called through the table of the code page that holds them, which `page`
/// loads first when another is in the pool
macro_rules! paged {
    ($page:ident: $($i:literal fn $name:ident($($a:ident: $t:ty),*) $(-> $r:ty)?;)*) => {
        #[cfg(not(feature = "voice-explore"))]
        extern "C" {
            $(pub fn $name($($a: $t),*) $(-> $r)?;)*
        }
        $(
            #[cfg(feature = "voice-explore")]
            #[inline(never)]
            pub unsafe fn $name($($a: $t),*) $(-> $r)? {
                let f: unsafe extern "C" fn($($t),*) $(-> $r)? =
                    unsafe { core::mem::transmute(super::page::func(super::page::$page, $i)) };
                unsafe { f($($a),*) }
            }
        )*
    };
}

/// EXPLORE's code pages (c/k6/kp.h), in the 8 kHz voice + EXPLORE image: the sweep (xscan.c),
/// Listen for ID (xlisten.c, xdmr.c) and the repeater table (rt.c) don't fit its MCU flash, so
/// they sit in SPI library slots 10, 11 and 9 (k6_lib.h, flag KM_F_PAGE), linked for this image
/// (tools/k6_pages.py), and run from the decoder pool, which no message uses while EXPLORE
/// runs. One is there at a time: the sweep in the list view, Listen for ID in a listen, the
/// table for a look-up or a write (a listen loads three or four, a few ms each).
#[cfg(feature = "voice-explore")]
mod page {
    use crate::hal::arena;
    use core::ptr::{addr_of, addr_of_mut};

    pub const SWEEP: u8 = 1;
    pub const LISTEN: u8 = 2;
    pub const TABLE: u8 = 3;
    pub const EDIT: u8 = 4;
    /// the library slot and the number of functions of each page (kp.h)
    const SLOT: [i32; 5] = [0, 10, 11, 9, 12];
    const FNS: [u32; 5] = [0, 12, 14, 8, 7];
    const KM_HDR: usize = 16;
    const ID_BYTES: usize = 8;
    /// the page in the pool now (0: none, or the pool was lent out)
    static mut NOW: u8 = 0;
    extern "C" {
        /// this image's build ID (build.rs: the note's first 8 bytes), which a page made for it ends with
        static k6_build_id: [u8; ID_BYTES];
    }

    fn magic(p: u8) -> u32 {
        0x504B | (p as u32) << 16 | FNS[p as usize] << 24
    }

    fn table() -> *const u32 {
        unsafe { arena::dec().add(KM_HDR) as *const u32 }
    }

    /// The pool may hold something else now (EXPLORE starts, or leaves)
    pub fn forget() {
        unsafe { *addr_of_mut!(NOW) = 0 };
    }

    /// Whether slot p holds a page made for this image, from its header and its last bytes
    /// alone (the pool untouched): 0, a k6_lib.h KL_* (-1 the slot is empty), -10 made for
    /// another image, -11 not this page. load checks the rest (the tag, the table).
    /// A packed page (level 15, docs/k6-lz4.md) is the raw length (u16) and an LZ4 block whose
    /// last 8 bytes modem/lz4.py keeps as literals, so its build ID is the slot's last 8 bytes
    /// as stored; its magic comes from decoding the block's first bytes.
    pub fn check(p: u8) -> i32 {
        let fl = &super::super::LIB_FL;
        let mut i = super::pffi::KlInfo::default();
        let r = unsafe { super::pffi::kl_info(SLOT[p as usize], fl, &mut i) };
        if r != 0 {
            return r;
        }
        let n = i.code_len as usize;
        let packed = i.packed as usize;
        if i.flags & 2 == 0 || KM_HDR + n + 8 > arena::DEC_BYTES || n < 8 + 4 * FNS[p as usize] as usize + ID_BYTES
            || (packed != 0 && packed < 2 + 16)
        {
            return -11;
        }
        let tail = if packed != 0 { packed } else { n };
        let base = 0x11_0000 + 0x4000 * SLOT[p as usize] as u32 + KM_HDR as u32;
        let mut head = [0u8; 16];
        let mut w = [0u8; 4];
        let mut id = [0u8; ID_BYTES];
        unsafe {
            if (fl.read)(base, head.as_mut_ptr(), 16) != 0
                || (fl.read)(base + (tail - ID_BYTES) as u32, id.as_mut_ptr(), ID_BYTES as u32) != 0
            {
                return -4;
            }
            if packed != 0 {
                // the first 4 bytes it decodes (it stops short of the block's end: -1, the bytes kept)
                super::pffi::lz4d(head.as_ptr().add(2), 14, w.as_mut_ptr(), 4);
            } else {
                w.copy_from_slice(&head[..4]);
            }
        }
        if id != unsafe { *addr_of!(k6_build_id) } {
            return -10;
        }
        if u32::from_le_bytes(w) != magic(p) {
            return -11;
        }
        0
    }

    /// Page p into the decoder pool and check it: 0, a k6_lib.h KL_* (-1 the slot is empty),
    /// -10 made for another image, -11 a bad table
    pub fn load(p: u8) -> i32 {
        forget();
        let dst = arena::dec();
        let r = unsafe { super::pffi::kl_page(SLOT[p as usize], &super::super::LIB_FL, dst, arena::DEC_BYTES as u32) };
        if r < 0 {
            return r;
        }
        let n = r as usize;
        if n < 8 + 4 * FNS[p as usize] as usize + ID_BYTES {
            return -11;
        }
        let id = unsafe { core::slice::from_raw_parts(dst.add(KM_HDR + n - ID_BYTES), ID_BYTES) };
        if id != unsafe { &*addr_of!(k6_build_id) } {
            return -10;
        }
        let t = table();
        if unsafe { *t } != magic(p) || unsafe { *t.add(1) } != t as u32 {
            return -11;
        }
        unsafe { *addr_of_mut!(NOW) = p };
        0
    }

    /// Function i of page p, the page loaded first if the pool holds another (or was lent out).
    /// Both pages loaded when EXPLORE started, so a failure here is the SPI flash's: the radio
    /// restarts (it comes back in VOICE MSG) rather than run what the pool holds.
    pub fn func(p: u8, i: usize) -> usize {
        let t = table();
        if unsafe { *addr_of!(NOW) } != p || unsafe { *t } != magic(p) {
            let r = load(p);
            if r != 0 {
                rl!("K6 EX page {} load r={}: restart", p, r);
                cortex_m::peripheral::SCB::sys_reset();
            }
        }
        unsafe { *t.add(2 + i) as usize }
    }
}

mod ffi {
    use super::pffi::K6Api;
    /// c/k6/explore.h ex_cand_t (48 bytes)
    #[repr(C)]
    #[derive(Clone, Copy)]
    pub struct Cand {
        pub hz: u32,
        pub first_ms: u32,
        pub last_ms: u32,
        pub on_ms: u32,
        pub off_hz: i32,
        pub overs: u16,
        pub tone: u16,
        pub lo: i16,
        pub hi: i16,
        pub prev_start: u32,
        pub ids: u8,
        pub skirts: u8,
        pub flags: u8,
        pub score: i8,
        pub grade: u8,
        pub call: [u8; 10],
        pub listens: u8,
        pub heard_s: u16,
        pub clean: u8,
        pub dmr_n: u8,
    }
    #[repr(C)]
    pub struct Ex {
        pub c: *mut Cand,
        pub n: u16,
        pub cap: u16,
        pub tol_hz: u32,
        pub known: *mut u32,
        pub known_n: u16,
        pub known_cap: u16,
        pub now: u32,
    }
    /// c/k6/rt.h rt_rec_t (64 bytes)
    #[repr(C)]
    #[derive(Clone, Copy)]
    pub struct RtRec {
        pub state: u16,
        pub seq: u16,
        pub rx_hz: u32,
        pub tx_off: i32,
        pub rx_tone: u16,
        pub tx_tone: u16,
        pub flags: u16,
        pub best_rssi: u8,
        pub score: u8,
        pub last_heard: u32,
        pub name: [u8; 16],
        pub call: [u8; 10],
        pub call_flags: u8,
        pub cw: u8,
        pub group: u8,
        pub spare0: u8,
        pub id: u16,
        pub wseq: u32,
        pub crc: u32,
    }
    #[repr(C)]
    pub struct RtFlash {
        pub read: unsafe extern "C" fn(*mut u8, u32, *mut u8, u32) -> i32,
        pub write: unsafe extern "C" fn(*mut u8, u32, *const u8, u32) -> i32,
        pub erase: unsafe extern "C" fn(*mut u8, u32) -> i32,
        pub ctx: usize, // void *ctx (unused here; usize keeps the static Sync)
    }
    #[repr(C)]
    pub struct Rt {
        pub fl: *const RtFlash,
        pub base: u32,
        pub live: u16,
        pub dead: u16,
        pub free: u16,
        pub max_id: u16,
        pub wseq: u32,
        pub spare: u8,
        pub gcs: u8,
        pub repairs: u8,
    }
    #[repr(C)]
    #[derive(Clone, Copy)]
    pub struct Seg {
        pub lo_hz: u32,
        pub step_hz: u32,
        pub nbins: u16,
        pub snap_hz: u16,
    }
    /// the settings at the start of c/k6/xscan.h's xs_t
    #[repr(C)]
    pub struct XsHead {
        pub nseg: u8,
        pub wide: u8,
        pub noise_db: u8,
        pub hit_db: u8,
        pub refine: u8,
        pub say_rows: u8,
        pub quiet: u8,
        pub refloor_s: u8,
        pub quick_us: u16,
        pub confirm_us: u16,
        pub hot_ms: u16,
        pub spur_s: u16,
        pub seg: [Seg; 4],
    }
    #[repr(C)]
    pub struct XsInfo {
        pub sweeps: u32,
        pub bins: u32,
        pub ms: u32,
        pub last_ms: u16,
        pub g: u8,
        pub phase: u8,
        pub floor_dbm: [i16; 4],
        pub hot: u8,
        pub up: u8,
        pub pad: [u8; 2],
    }
    #[repr(C)]
    pub struct XlInfo {
        pub ms: u32,
        pub msgs: u16,
        pub junk: u16,
        pub keyed: u8,
        pub seen: u8,
        pub fl: u8,
        pub wpm: u8,
        pub tone_hz: u16,
        pub call: [u8; 11],
        pub text: [u8; 21],
    }
    extern "C" {
        pub fn ex_pick_id(e: *mut Ex, up_hz: *const u32, n: u32) -> *mut Cand;
        pub fn ex_init(e: *mut Ex, store: *mut Cand, cap: u32);
        pub fn ex_known_store(e: *mut Ex, buf: *mut u32, cap: u32);
        pub fn ex_know(e: *mut Ex, hz: u32);
        pub fn ex_unknow(e: *mut Ex, hz: u32);
        pub fn ex_find(e: *mut Ex, hz: u32) -> *mut Cand;
        pub fn ex_rank(e: *mut Ex, i: u32) -> *mut Cand;
        pub fn ex_view(e: *mut Ex, i: u32) -> *mut Cand;
        pub fn ex_view_n(e: *const Ex) -> u32;
        pub fn ex_view_rows(e: *mut Ex, i: u32, out: *mut *mut Cand, k: u32) -> u32;
        pub fn ex_cw(e: *mut Ex, hz: u32, call: *const u8);
        pub fn ex_grade(c: *mut Cand);
        pub fn ex_listen_cap(c: *const Cand, max_s: u32, min_s: u32) -> u32;
        pub fn ex_heard(e: *mut Ex, hz: u32, s: u32);
        pub fn ex_dmr_heard(e: *mut Ex, hz: u32, dmr: i32, s: u32) -> i32;
        pub fn ex_to_rt(c: *const Cand, r: *mut RtRec);
        pub fn ex_tone(e: *mut Ex, hz: u32, tone: u16);
    }
    // The sweep (c/k6/xscan.c), Listen for ID (xlisten.c, xdmr.c) and the repeater table (rt.c):
    // linked in, or in the 8 kHz voice + EXPLORE image three code pages in SPI flash
    // (super::page; c/k6/kp.h, the same order)
    paged! {
        SWEEP:
        0 fn xs_defaults(s: *mut u8);
        1 fn xs_state_bytes() -> u32;
        2 fn xs_mem_bytes(s: *const u8) -> u32;
        3 fn xs_init(s: *mut u8, api: *const K6Api, ex: *mut Ex, mem: *mut u8, bytes: u32) -> i32;
        4 fn xs_step(s: *mut u8) -> i32;
        5 fn xs_close(s: *mut u8);
        6 fn xs_tone(s: *mut u8, peek_ms: u32) -> u32;
        7 fn xs_resume(s: *mut u8);
        8 fn xs_summary(s: *mut u8);
        9 fn xs_info(s: *const u8, out: *mut XsInfo);
        10 fn xs_up(s: *const u8, out: *mut u32, max: u32) -> u32;
        11 fn xs_wake(s: *mut u8, hz: u32, fl_dbm: *mut i32) -> i32;
    }
    paged! {
        LISTEN:
        0 fn xl_state_bytes() -> u32;
        1 fn xl_start(x: *mut u8, api: *const K6Api, rx_hz: u32, max_s: u32);
        2 fn xl_step(x: *mut u8) -> i32;
        3 fn xl_stop(x: *mut u8, why: i32);
        4 fn xl_info(x: *const u8, out: *mut XlInfo);
        5 fn xd_state_bytes() -> u32;
        6 fn xd_start(s: *mut u8, api: *const K6Api, spk: i32, cps8: u32);
        7 fn xd_step(s: *mut u8, x: *mut u8) -> i32;
        8 fn xd_stop(s: *mut u8) -> i32;
        9 fn xd_known(s: *mut u8);
        10 fn xd_flags(s: *const u8) -> u32;
        11 fn xd_adj(s: *const u8) -> i32;
        12 fn xd_rate8(s: *const u8) -> u32;
        13 fn xd_tone(s: *const u8) -> u32;
    }
    // the repeater table (c/k6/rt.c)
    paged! {
        TABLE:
        0 fn rt_mount(t: *mut Rt, fl: *const RtFlash, base: u32, force: i32) -> i32;
        1 fn rt_find(t: *mut Rt, id: u16, rx_hz: u32, tol: u32, out: *mut RtRec) -> i32;
        2 fn rt_next(t: *mut Rt, slot: i32, out: *mut RtRec) -> i32;
        3 fn rt_put(t: *mut Rt, r: *mut RtRec) -> i32;
        4 fn rt_delete(t: *mut Rt, id: u16) -> i32;
        5 fn rt_cw_check(t: *mut Rt, rx_hz: u32, tol: u32, call: *const u8, out: *mut RtRec) -> i32;
        6 fn rt_cw_apply(t: *mut Rt, rx_hz: u32, tol: u32, call: *const u8, call_flags: u8, tone_hz: i32, wpm: i32, confirmed: i32, out: *mut RtRec) -> i32;
        7 fn rt_heard_apply(t: *mut Rt, rx_hz: u32, tol: u32, tone: u16, dmr: i32, out: *mut RtRec) -> i32;
    }
    /// c/k6/sted.h sted_t: the station editor's state, the image's own (the page only code)
    #[repr(C)]
    pub struct Sted {
        pub r: RtRec,
        pub was: RtRec,
        pub fwas: RtRec,
        pub tap_ms: u32,
        pub msg: *const u8,
        pub f: u8,
        pub open: u8,
        pub ask: u8,
        pub nd: u8,
        pub tap_k: u8,
        pub tap_i: u8,
        pub fresh: u8,
        pub neg: u8,
        pub cust: u8,
        pub dig: [u8; 10],
    }
    // the station editor (c/k6/sted.c; the release plan, Jeff 2026-11-13: the editor in the core)
    paged! {
        EDIT:
        0 fn sted_open(e: *mut Sted, r: *const RtRec);
        1 fn sted_key(e: *mut Sted, key: i32, ms: u32) -> i32;
        2 fn sted_changed(e: *const Sted) -> i32;
        3 fn sted_commit(e: *mut Sted);
        4 fn sted_saved(e: *mut Sted, written: *const RtRec);
        5 fn sted_row(e: *const Sted, f: i32, out: *mut u8) -> i32;
        6 fn sted_hint(e: *const Sted, out: *mut u8) -> i32;
    }
}
use ffi::{Cand, Ex, Rt, RtFlash, RtRec, Seg, Sted, XsHead, XsInfo, XlInfo};

const CAP: usize = 64;
const RT_BASE: u32 = 0x0F_0000;
const RT_TOL_HZ: u32 = 5_000;
const LISTEN_S: u32 = 1500;
/// the automatic Listen for ID (Jeff's rule, 2026-11-07): it goes to a candidate with no call
/// when it keys up, the one listened to fewest times first (ex_pick_id); it stays while the
/// carrier is up, then AUTO_TAIL_MS more, AUTO_MAX_S at most, halved for each minute already
/// spent listening to it, AUTO_MIN_S at least (Jeff 2026-11-07: "reduces the time spent
/// listening for CW the longer a given station has actually been listened to"). The next is picked only at the end
/// of a full pass over the band set (AUTO_PASSES after a listen, so one is whole), from all that
/// are up then, loudest first (Jeff 2026-11-07: "spend a couple seconds to do a full sweep
/// first"). A call needs two decodes alike, on one listen or two.
const AUTO_MAX_S: u32 = 120;
const AUTO_MIN_S: u32 = 20;
const AUTO_TAIL_MS: u32 = 3000;
const AUTO_PASSES: u32 = 2;
const AUTO_DROP_DB: i16 = 15;
const XL_MSG: i32 = 3;
const EX_F_IGNORED: u8 = 0x01;
const EX_F_TRIED: u8 = 0x02;
const EX_F_OFF_HEARD: u8 = 0x04;
const EX_F_SIMPLEX: u8 = 0x08;
const EX_F_SPUR: u8 = 0x10;
const EX_F_KNOWN: u8 = 0x20;
/// known_calls has looked this one up in the table (a bit c/k6/explore.h leaves free)
const EX_F_LOOKED: u8 = 0x40;
/// a listen heard DMR syncs there (c/k6/explore.h EX_F_DMR, set from c/k6/xdmr.c's xd_flags)
const EX_F_DMR: u8 = 0x80;
const XD_NOW: u32 = 1;
const XD_EVER: u32 = 2;
const RT_TONE_FILL: i32 = 2;
const RT_TONE_DIFFERS: i32 = 3;
/// dB the listen's frozen receiver gain adds to RSSI reads (xd_adj), taken off by rssi_dbm
static mut DMR_ADJ: i16 = 0;
/// Listen trace (2026-11-08: the first DMR/CTCSS image froze 0-3 s into a listen with no clue):
/// the listen's steps so far, every step of the first TRACE_ALL on the serial report and flushed
static mut TRACE_N: u32 = 0;
const TRACE_ALL: u32 = 100;

macro_rules! trace {
    ($($a:tt)*) => {{
        rl!($($a)*);
        crate::hal::uart::flush();
    }};
}

fn trace_on() -> bool {
    let n = unsafe { TRACE_N };
    n < TRACE_ALL || n % 32 == 0
}
/// the saved table's frequencies, for the new / known rule on the list (more stay unmarked)
const KNOWN_N: usize = 64;
#[link_section = ".ram0"] // hal::ram0
static mut KNOWN: [u32; KNOWN_N] = [0; KNOWN_N];
const RT_F_CALL_USER: u16 = 0x0010;
const RT_F_DMR: u16 = 0x0020;
const RT_F_NAME_USER: u16 = 0x0008;
/// saved by the explorer on its own (DMR on EX_DMR_SAVE_N listens), not by Save (rt.h)
const RT_F_AUTO: u16 = 0x0080;
/// ex_dmr_heard results (explore.h)
const EX_DMR_CLEARED: i32 = 1;
const EX_DMR_SAVE: i32 = 2;
const RT_C_FROM_CW: u8 = 0x08;
const RT_C_TWICE: u8 = 0x10;
/// xs_step / xl_step results
const XS_KEY: i32 = 2;
const XL_OK: i32 = 1;
const XL_TIME: i32 = 2;
/// redraw at most this often while sweeping (100 us ticks): a draw takes 30-50 ms
const DRAW_TICKS: u16 = 4000;
/// the sweep's peek at an up carrier with no call (xs_tone), ms: the tone detector keyed there
/// means an ID is going out now (WA7DMR on 440.925, 2026-11-08 04:59: 4 s, mid-carrier)
const PEEK_MS: u32 = 40;
/// a listen begun on a peek hears the end of that ID only, so the station goes on the watch list:
/// each time it keys up after that, the listener goes there at once and stays the whole carrier
/// (WA7DMR sent its one ID in 52 minutes 13 s into a key-up: no 10 minute timer). Not again
/// within WATCH_GAP_MS of the last listen there, and the gap doubles with each listen there that
/// hears no ID, up to 2^WATCH_BACKOFF times (10.7 minutes): a busy voice repeater whose speech
/// keys the tone detector took 48 of 55 listens on 2026-11-08 17:52-18:34Z (443.525).
const WATCH_GAP_MS: u32 = 20_000;

/// The explore image boots straight into EXPLORE (the release plan, Jeff 2026-11-13; a 120 s
/// VOICE MSG window before that). VOICE MSG is the RAM arena: it takes modules, messages and
/// control packets (the OTA reset among them), and goes back into EXPLORE by itself after
/// VOICE_IDLE_MS with no packet and no key press. The ways into the arena: EXIT twice in EXPLORE,
/// and the CW wake: while sweeping, a carrier on the bench frequency 20 dB over that frequency's
/// own floor (or over -60 dBm whatever the floor; c/k6/xscan.h, xs_wake), read twice
/// BEACON_EVERY_MS apart, sends it to VOICE MSG for BEACON_HOLD_MS: hamlet keys up
/// (with its ID), waits for `K6 EX beacon`, then uploads or sends the OTA reset (Jeff OK'd it,
/// 2026-11-08 17:37). A listen going on finishes first (2 minutes at most). EXPLORE's code
/// pages missing (after a flash, before hamlet installs them) hold the arena until they're in.
const VOICE_IDLE_MS: u32 = 60_000;
const BEACON_HZ: u32 = 440_850_000;
/// Over the floor since 2026-11-19 (Jeff): the fixed -60 (2026-11-17, itself down from -45)
/// missed the bench Pluto at its top (-61 dBm here); the floor on 440.850 reads about -114
const BEACON_EVERY_MS: u32 = 1000;
const BEACON_HOLD_MS: u32 = 60_000;
/// VOICE MSG since (ms), and how long it waits for quiet before EXPLORE
static mut VOICE: (u32, u32) = (0, VOICE_IDLE_MS);
/// EXPLORE's code pages weren't there at the last try (the arena says PAGES MISSING)
static mut PAGES_MISSING: bool = false;
/// Key 0 in VOICE MSG: the arena stays put, no going back into EXPLORE on its own (Jeff's
/// Monday to-do, 2026-11-14). One way (Jeff, 2026-11-16: no second 0 to undo it): EXIT to
/// EXPLORE clears it, so the next visit to the arena has its quiet minute again; off at boot.
static mut STAY: bool = false;

pub fn stay() -> bool {
    unsafe { *addr_of!(STAY) }
}

/// Key 0 in VOICE MSG: the arena stays until EXIT
pub fn stay_set() {
    if !stay() {
        unsafe { *addr_of_mut!(STAY) = true };
        rl!("K6 EX stay=1");
    }
}

pub fn pages_missing() -> bool {
    unsafe { *addr_of!(PAGES_MISSING) }
}
const WATCH_BACKOFF: u8 = 5;

/// how long a watched station waits after its last listen, with `misses` listens there done
fn watch_gap(misses: u8) -> u32 {
    WATCH_GAP_MS << misses.min(WATCH_BACKOFF)
}
const ROWS: usize = 7;

/// The band sets, # cycles them: (name, segments (lo, step, bins, snap)). Each set's floors and
/// row fit the PWM ring with Listen for ID's state (tests/test_k6_xscan.py).
const BANDS: [(&str, &[(u32, u32, u16, u16)]); 4] = [
    ("ALL", &[(144_000_000, 10_000, 400, 5_000), (223_800_000, 20_000, 60, 20_000), (440_000_000, 12_500, 400, 12_500)]),
    ("2M", &[(144_000_000, 10_000, 400, 5_000)]),
    ("70CM", &[(440_000_000, 12_500, 400, 12_500), (445_000_000, 12_500, 400, 12_500)]),
    ("1.25M", &[(222_000_000, 20_000, 150, 20_000)]),
];

#[derive(Clone, Copy, PartialEq, Eq)]
enum View {
    List,
    Try,
    Listen,
    Ask,
    /// the station editor (key 8 on a row; c/k6/sted.c in its code page)
    Edit,
    /// MENU > STATIONS: the saved table, a station at a time (in `ask`)
    Stations,
}

struct State {
    on: bool,
    view: View,
    sel: u8,
    band: u8,
    leave_armed: bool,
    /// the beacon check: the last read (ms); xs_wake keeps the floor and the reads in a row
    beacon_ms: u32,
    /// key 3 pressed once on a saved row: a second press deletes its table entry
    del_armed: bool,
    /// key 6 pressed once: a second press deletes every auto-saved entry (RT_F_AUTO)
    adel_armed: bool,
    dirty: bool,
    last_draw: u16,
    msg: [u8; 21],
    msg_len: u8,
    msg_t: u16,
    xs: *mut u8,
    xl: *mut u8,
    ring: *mut u8,
    ring_bytes: u32,
    ex: Ex,
    rt: Rt,
    rt_res: i32,
    /// the candidate Try or Listen is on
    hz: u32,
    off: i32,
    /// Listen for ID's confirmed call, waiting on a key (Ask)
    ask: RtRec,
    heard: [u8; 11],
    heard_fl: u8,
    heard_tone: u16,
    heard_wpm: u8,
    level: i16,
    /// automatic Listen for ID: on (key 1), this listen is one, its carrier last seen (ms),
    /// the level that counts as up, when the last one ended; calls heard once (hz, call)
    auto: bool,
    /// the automatic listen plays on the speaker (Jeff 2026-11-07 22:10: on by default); key 9
    auto_spk: bool,
    auto_now: bool,
    auto_seen_ms: u32,
    auto_start_ms: u32,
    auto_max_s: u32,
    auto_lvl: i16,
    /// full passes (xs_info sweeps) at the last listen's end or the band's start, and the last
    /// pass the listener looked at
    auto_mark: u32,
    auto_pass: u32,
    once: [(u32, [u8; 11]); 6],
    once_i: u8,
    /// the watch list (frequency, ms its last listen ended, listens there with no ID yet; 0 free),
    /// and whether the listen going on began on a peek (its first transmission is the tail of an
    /// ID, not kept)
    watch: [(u32, u32, u8); 4],
    mid: bool,
    /// the sweep was left for Try or Listen: its segment reads its floor again on the way back
    resume: bool,
    /// Listen for ID's side detectors (c/k6/xdmr.c: DMR mute, the CTCSS read): their state at the
    /// end of the message store (null: no room, listens run without them), the DMA rate the last
    /// listen measured, and whether DMR is on now (for the header)
    xd: *mut u8,
    cps8: u32,
    dmr_now: bool,
    /// the station editor's state (sted_t), while View::Edit
    ed: Sted,
    /// STATIONS: the station shown (its place among the live entries), and the editor opened
    /// from there (its EXIT goes back to STATIONS)
    st_i: u8,
    st_from: bool,
}

/// In hal::ram0 since 2026-11-19 (its 600 B left .data for the arena; KNOWN, 256 B, went back to
/// .bss to make room): NOLOAD, so its starting value is S0, written by init() at boot
#[link_section = ".ram0"] // hal::ram0
static mut S: MaybeUninit<State> = MaybeUninit::uninit();

/// main, right after hal::ram0::clear: EXPLORE's state as it starts
pub fn init() {
    unsafe { addr_of_mut!(S).write(MaybeUninit::new(S0)) }
}

const S0: State = State {
    on: false,
    view: View::List,
    sel: 0,
    band: 0,
    leave_armed: false,
    beacon_ms: 0,
    del_armed: false,
    adel_armed: false,
    dirty: true,
    last_draw: 0,
    msg: [0; 21],
    msg_len: 0,
    msg_t: 0,
    xs: null_mut(),
    xl: null_mut(),
    ring: null_mut(),
    ring_bytes: 0,
    ex: Ex { c: null_mut(), n: 0, cap: 0, tol_hz: 0, known: null_mut(), known_n: 0, known_cap: 0, now: 0 },
    rt: Rt { fl: core::ptr::null(), base: 0, live: 0, dead: 0, free: 0, max_id: 0, wseq: 0, spare: 0, gcs: 0, repairs: 0 },
    rt_res: 0,
    hz: 0,
    off: 0,
    ask: RtRec {
        state: 0, seq: 0, rx_hz: 0, tx_off: 0, rx_tone: 0, tx_tone: 0, flags: 0, best_rssi: 0, score: 0,
        last_heard: 0, name: [0; 16], call: [0; 10], call_flags: 0, cw: 0, group: 0, spare0: 0, id: 0, wseq: 0, crc: 0,
    },
    heard: [0; 11],
    heard_fl: 0,
    heard_tone: 0,
    heard_wpm: 0,
    level: 0,
    auto: true,
    auto_spk: true,
    auto_now: false,
    auto_seen_ms: 0,
    auto_start_ms: 0,
    auto_max_s: AUTO_MAX_S,
    auto_lvl: 0,
    auto_mark: 0,
    auto_pass: 0,
    once: [(0, [0; 11]); 6],
    once_i: 0,
    watch: [(0, 0, 0); 4],
    mid: false,
    resume: false,
    xd: null_mut(),
    cps8: 0,
    dmr_now: false,
    ed: Sted {
        r: RT_REC0,
        was: RT_REC0,
        fwas: RT_REC0,
        tap_ms: 0,
        msg: core::ptr::null(),
        f: 0,
        open: 0,
        ask: 0,
        nd: 0,
        tap_k: 0,
        tap_i: 0,
        fresh: 0,
        neg: 0,
        cust: 0,
        dig: [0; 10],
    },
    st_i: 0,
    st_from: false,
};

const RT_REC0: RtRec = RtRec {
    state: 0, seq: 0, rx_hz: 0, tx_off: 0, rx_tone: 0, tx_tone: 0, flags: 0, best_rssi: 0, score: 0,
    last_heard: 0, name: [0; 16], call: [0; 10], call_flags: 0, cw: 0, group: 0, spare0: 0, id: 0, wseq: 0, crc: 0,
};

/// the C side's ms (module API time), kept by mod_tick
fn now_ms() -> u32 {
    mod_tick() / 10
}

fn rssi_dbm(app: &mut App, syst: &mut SYST) -> i16 {
    let v = app.radio.fd6818.reg_read(syst, 0x67);
    (((v & 0x1FF) >> 1) as i16) - 174 - unsafe { DMR_ADJ }
}

fn st() -> &'static mut State {
    unsafe { (*addr_of_mut!(S)).assume_init_mut() }
}

pub fn on() -> bool {
    unsafe { (*addr_of!(S)).assume_init_ref().on }
}

/// The module list changed over EXPLORE's screen: draw it at the next turn
pub fn touch() {
    st().dirty = true;
}

/// The help card's screen name for the view (tools/k6_cards.py, docs/manual/cards/core.txt)
#[cfg(banded)]
pub fn help_name() -> &'static [u8] {
    match st().view {
        View::List => b"EXPLORE",
        View::Try => b"TRY",
        View::Listen => b"LISTEN",
        View::Ask => b"NEWCALL",
        View::Edit => b"EDIT",
        View::Stations => b"STATIONS",
    }
}

/// A held MENU found no card for the screen (no deck installed in slots 13-15)
#[cfg(banded)]
pub fn help_missing() {
    if on() {
        note("NO HELP CARD (13)");
    }
}

/// VOICE MSG waits `ms` of quiet (no packet, no key) from now before EXPLORE
fn voice_wait(ms: u32) {
    unsafe { *addr_of_mut!(VOICE) = (now_ms(), ms) };
}

/// The image has booted (into VOICE MSG's first entry, for its boot lines): EXPLORE at once
pub fn boot() {
    voice_wait(0);
    rl!("K6 EX at boot (arena: EXIT twice, or the CW wake)");
}

/// A key press in VOICE MSG: the wait starts again
pub fn voice_key() {
    voice_wait(VOICE_IDLE_MS);
}

/// VOICE MSG's turn in the main loop: into EXPLORE once it has been quiet long enough
pub fn voice_poll(app: &mut App, syst: &mut SYST) {
    let (since, wait) = unsafe { *addr_of!(VOICE) };
    if stay() {
        return;
    }
    if now_ms().wrapping_sub(since) >= wait && app.pager.idle_ticks >= wait * 10 {
        rl!("K6 EX quiet {} s: EXPLORE", wait / 1000);
        enter(app, syst);
    }
}

// ---- the flash, through module API level 5's checks ----

unsafe extern "C" fn rt_read(_c: *mut u8, addr: u32, buf: *mut u8, n: u32) -> i32 {
    unsafe { (MOD_API.fl_read)(addr, buf, n) }
}
unsafe extern "C" fn rt_write(_c: *mut u8, addr: u32, buf: *const u8, n: u32) -> i32 {
    unsafe { (MOD_API.fl_prog)(addr, buf, n) }
}
unsafe extern "C" fn rt_erase(_c: *mut u8, addr: u32) -> i32 {
    unsafe { (MOD_API.fl_erase)(addr) }
}
static RT_FLASH: RtFlash = RtFlash { read: rt_read, write: rt_write, erase: rt_erase, ctx: 0 };

/// The module API's context for the C calls: the app and SysTick while a call runs (they're
/// borrowed for it and nothing else touches them until it returns), the time kept between.
fn ctx_on(app: &mut App, syst: &mut SYST) {
    unsafe {
        let c = &mut *addr_of_mut!(MOD_CTX);
        c.app = app as *mut App;
        c.syst = syst as *mut SYST;
    }
}
fn ctx_off() {
    unsafe { (*addr_of_mut!(MOD_CTX)).app = null_mut() };
}

fn note(s: &str) {
    let t = st();
    let n = s.len().min(t.msg.len());
    t.msg[..n].copy_from_slice(&s.as_bytes()[..n]);
    t.msg_len = n as u8;
    t.msg_t = uptime::now();
    t.dirty = true;
}

fn cstr(b: &[u8]) -> &str {
    let n = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    crate::out::ascii(&b[..n])
}

/// A saved repeater's candidate gets the table's call (bench 6/7: 440.850 listed under the rule
/// with no call, so no check mark, and the listener went to it again). Once each, inside ctx_on.
fn known_calls() {
    let t = st();
    if t.rt_res < 0 {
        return;
    }
    for i in 0..t.ex.n as usize {
        let c = unsafe { &mut *t.ex.c.add(i) };
        if c.flags & EX_F_KNOWN == 0 || c.flags & EX_F_LOOKED != 0 || c.call[0] != 0 {
            continue;
        }
        c.flags |= EX_F_LOOKED;
        let mut r: RtRec = unsafe { core::mem::zeroed() };
        if unsafe { ffi::rt_find(&mut t.rt, 0, c.hz, RT_TOL_HZ, &mut r) } >= 0 && r.call[0] != 0 {
            c.call = r.call;
            unsafe { ffi::ex_grade(c) };
        }
    }
}

fn cand(i: u32) -> Option<&'static mut Cand> {
    let p = unsafe { ffi::ex_rank(&mut st().ex, i) };
    if p.is_null() {
        None
    } else {
        Some(unsafe { &mut *p })
    }
}

/// the i-th row of the list on the screen: skirts are left out, folded into the louder carrier
/// beside them (Jeff 2026-11-07: "lump close together signals together")
fn shown(i: u32) -> Option<&'static mut Cand> {
    let p = unsafe { ffi::ex_view(&mut st().ex, i) };
    if p.is_null() {
        None
    } else {
        Some(unsafe { &mut *p })
    }
}

/// set the sweep up on band set b (the candidates stay)
/// full passes over the band set so far
fn passes() -> u32 {
    let mut info: XsInfo = unsafe { core::mem::zeroed() };
    unsafe { ffi::xs_info(st().xs, &mut info) };
    info.sweeps
}

fn start_band(b: u8) {
    let t = st();
    t.auto_mark = 0;
    t.auto_pass = 0;
    t.band = b % BANDS.len() as u8;
    let (_, segs) = BANDS[t.band as usize];
    unsafe {
        ffi::xs_defaults(t.xs);
        let h = &mut *(t.xs as *mut XsHead);
        h.nseg = segs.len() as u8;
        for (i, &(lo, step, n, snap)) in segs.iter().enumerate() {
            h.seg[i] = Seg { lo_hz: lo, step_hz: step, nbins: n, snap_hz: snap };
        }
        let need = (ffi::xs_mem_bytes(t.xs) + 3) & !3;
        t.xl = t.ring.add(need as usize);
        let r = ffi::xs_init(t.xs, &MOD_API, &mut t.ex, t.ring, need);
        rl!("K6 EX band {} bins={} r={}", BANDS[t.band as usize].0, need, r);
    }
}

/// From VOICE MSG (EXIT): the receiver off packets, the store and the ring borrowed.
pub(super) fn enter(app: &mut App, syst: &mut SYST) {
    if stream_on() || app.pager.play_pending {
        return;
    }
    // the 8 kHz voice + EXPLORE image: both code pages there and made for this image, before
    // anything is borrowed (the pool too: a refusal leaves VOICE MSG as it was)
    #[cfg(feature = "voice-explore")]
    {
        page::forget();
        let (w, l, r) = (page::check(page::SWEEP), page::check(page::LISTEN), page::check(page::TABLE));
        if w != 0 || l != 0 || r != 0 {
            rl!(
                "K6 EX pages missing: sweep r={} listen r={} table r={}; install this image's out/page-*.bin (k6 mod PAGE --page --install 10 / 11 / 9)",
                w, l, r
            );
            voice_wait(VOICE_IDLE_MS);
            unsafe { *addr_of_mut!(PAGES_MISSING) = true };
            return;
        }
        unsafe { *addr_of_mut!(PAGES_MISSING) = false };
    }
    disarm(app, syst);
    app.radio.set_speaker(false);
    unsafe { pffi::km_reset() };
    // Listen for ID's state sizes first, then the sweep's page, which stays
    #[cfg(feature = "voice-explore")]
    let (xl_bytes, xd_raw) = unsafe { (ffi::xl_state_bytes(), ffi::xd_state_bytes()) };
    unsafe {
        *addr_of_mut!(MOD_CTX) = super::ModCtx {
            app: null_mut(),
            syst: null_mut(),
            rx_hz: app.master_freq_hz(),
            t_last: uptime::now(),
            ticks: 0,
            scratch: arena::end(), // none: EXPLORE has the store and the ring
            tx_until: 0,
            tx: false,
            cal_written: true,
            fl_erases: 0,
            tlm_cap: 0,
        };
    }
    let t = st();
    let store = arena::base();
    let xs_bytes = (unsafe { ffi::xs_state_bytes() } as usize + 3) & !3;
    t.xs = store;
    let cands = unsafe { store.add(xs_bytes) } as *mut Cand;
    // the side detectors' state at the store's end, if the candidates can spare it
    #[cfg(not(feature = "voice-explore"))]
    let (xl_bytes, xd_raw) = unsafe { (ffi::xl_state_bytes(), ffi::xd_state_bytes()) };
    let xd_bytes = (xd_raw as usize + 3) & !3;
    let room = STORE_BYTES - xs_bytes;
    let cap = (room.saturating_sub(xd_bytes) / core::mem::size_of::<Cand>()).min(CAP);
    t.xd = if room >= xd_bytes + cap * core::mem::size_of::<Cand>() {
        unsafe { store.add(STORE_BYTES - xd_bytes) }
    } else {
        null_mut()
    };
    unsafe {
        ffi::ex_init(&mut t.ex, cands, cap as u32);
        ffi::ex_known_store(&mut t.ex, addr_of_mut!(KNOWN) as *mut u32, KNOWN_N as u32);
    }
    t.ring = ((pwm_audio::idle_bytes() as usize + 3) & !3) as *mut u8;
    t.ring_bytes = (pwm_audio::RING_BYTES - 4) as u32;
    t.on = true;
    t.view = View::List;
    t.sel = 0;
    t.leave_armed = false;
    t.msg_len = 0;
    ctx_on(app, syst);
    t.rt_res = unsafe { ffi::rt_mount(&mut t.rt, &RT_FLASH, RT_BASE, 0) };
    if t.rt_res >= 0 {
        // every saved entry is a known repeater: listed under the rule
        let mut r: RtRec = unsafe { core::mem::zeroed() };
        let mut slot = -1;
        loop {
            slot = unsafe { ffi::rt_next(&mut t.rt, slot, &mut r) };
            if slot < 0 {
                break;
            }
            unsafe { ffi::ex_know(&mut t.ex, r.rx_hz) };
        }
    }
    start_band(t.band);
    ctx_off();
    rl!(
        "K6 EX enter cap={} rt={} live={} repairs={} xl={} xd={}",
        cap, t.rt_res, t.rt.live, t.rt.repairs, xl_bytes, if t.xd.is_null() { 0 } else { xd_bytes }
    );
    if t.rt_res < 0 {
        note(if t.rt_res == -5 { "TABLE AREA NOT BLANK" } else { "TABLE READ FAILED" });
    }
    t.dirty = true;
}

pub(super) fn leave(app: &mut App, syst: &mut SYST) {
    let t = st();
    ctx_on(app, syst);
    unsafe {
        ffi::xs_close(t.xs);
        ffi::xs_summary(t.xs);
    }
    ctx_off();
    dump();
    #[cfg(feature = "voice-explore")]
    page::forget(); // the pool is the decoder's again
    t.on = false;
    monitor_off(app, syst);
    clear_store(app);
    app.sync_watching_to_master(syst);
    arm(app, syst);
    unsafe { *addr_of_mut!(STAY) = false }; // EXIT from a held arena: its quiet minute again next time
    voice_wait(VOICE_IDLE_MS);
    rl!("K6 EX leave");
}

fn monitor_on(app: &mut App, syst: &mut SYST, hz: u32) {
    app.radio.retune_rx(syst, hz, true);
    app.radio.fd6818.set_af_out(syst, AfOutState::RxAudio, true, Modulation::Fm);
    app.radio.set_speaker(true);
}

fn monitor_off(app: &mut App, syst: &mut SYST) {
    app.radio.set_speaker(false);
    app.radio.fd6818.set_af_out(syst, AfOutState::Mute, true, Modulation::Fm);
}

/// The main loop's turn: one sweep step, or the listener's, or Try's level. true: redraw now.
pub fn poll(app: &mut App, syst: &mut SYST) -> bool {
    let t = st();
    // EXIT twice left in this turn's keys (leave): the store, the sweep's state, is cleared
    // and a step on it calls through a null API (the 8 kHz voice + EXPLORE image hung there,
    // 2026-11-10). Redraw: VOICE MSG's screen now
    if !t.on {
        return true;
    }
    mod_tick(); // the C side's ms, kept across the gaps
    match t.view {
        View::List => {
            ctx_on(app, syst);
            if core::mem::take(&mut t.resume) {
                // bench 3 (20:25): 2 m's floor rose 8 dB in a 2 minute listen, and the rest of
                // the segment came back as a wall of new hits against the old floor
                unsafe { ffi::xs_resume(t.xs) };
            }
            let key = unsafe { ffi::xs_step(t.xs) } == XS_KEY;
            known_calls();
            let now = now_ms();
            let (mut beacon, mut fl) = (0, 0);
            if now.wrapping_sub(t.beacon_ms) >= BEACON_EVERY_MS {
                t.beacon_ms = now;
                beacon = unsafe { ffi::xs_wake(t.xs, BEACON_HZ, &mut fl) };
            }
            ctx_off();
            if beacon != 0 {
                rl!("K6 EX beacon {} dBm floor {}: VOICE MSG for {} s", beacon, fl, BEACON_HOLD_MS / 1000);
                leave(app, syst);
                voice_wait(BEACON_HOLD_MS);
                return true;
            }
            let tone = unsafe { ffi::xs_tone(t.xs, if t.auto { PEEK_MS } else { 0 }) };
            // a row can fold away (it turned out a skirt): keep the selection on the list
            let n = unsafe { ffi::ex_view_n(&t.ex) };
            if n > 0 && t.sel as u32 >= n {
                t.sel = (n - 1) as _;
            }
            if !key && t.auto && tone != 0 {
                tone_heard(app, syst, tone);
            } else if !key && t.auto && watch_up(app, syst) {
            } else if !key && t.auto {
                let n = passes();
                if n != t.auto_pass {
                    t.auto_pass = n;
                    if n >= t.auto_mark + AUTO_PASSES {
                        auto_pick(app, syst);
                    }
                }
            }
        }
        View::Listen => {
            let n = unsafe { TRACE_N };
            let tr = trace_on();
            if tr {
                trace!("K6 EX t {} a {}", n, now_ms());
            }
            ctx_on(app, syst);
            let r = if t.xd.is_null() { unsafe { ffi::xl_step(t.xl) } } else { unsafe { ffi::xd_step(t.xd, t.xl) } };
            ctx_off();
            if tr {
                trace!("K6 EX t {} b r={}", n, r);
            }
            dmr_seen();
            if r == XL_OK || r == XL_TIME {
                listen_end(app, syst, r);
            } else if t.auto_now {
                auto_check(app, syst, r);
            } else if r != 0 {
                t.dirty = true;
            }
            if tr {
                trace!("K6 EX t {} c", n);
            }
            unsafe { TRACE_N = n.wrapping_add(1) };
        }
        View::Try => {
            if uptime::now().wrapping_sub(t.last_draw) >= DRAW_TICKS {
                t.level = rssi_dbm(app, syst);
            }
        }
        View::Ask | View::Edit | View::Stations => {}
    }
    if t.msg_len != 0 && uptime::now().wrapping_sub(t.msg_t) > 25_000 {
        t.msg_len = 0;
        t.dirty = true;
    }
    if t.dirty || uptime::now().wrapping_sub(t.last_draw) >= DRAW_TICKS {
        t.dirty = false;
        t.last_draw = uptime::now();
        return true;
    }
    false
}

// ---- keys ----

/// redraws for arrow presses during a listen at most this often (100 us ticks)
const SCROLL_TICKS: u16 = 1500;

/// the selection one row up or down the screen list, wrapping from the last row to the first
/// and back (Jeff 2026-11-08)
fn step_sel(t: &mut State, up: bool) {
    let n = unsafe { ffi::ex_view_n(&t.ex) };
    if n == 0 {
        t.sel = 0;
        return;
    }
    let s = (t.sel as u32).min(n - 1);
    let s = if up { if s == 0 { n - 1 } else { s - 1 } } else if s + 1 >= n { 0 } else { s + 1 };
    t.sel = s as _;
}

pub(super) fn dispatch(app: &mut App, syst: &mut SYST, ev: KeyEvent) {
    if !matches!(ev.kind, KeyEventKind::Single | KeyEventKind::Repeat) {
        return;
    }
    // EXIT counts presses, not repeats: on the bench one held EXIT armed, left, and its next
    // repeat opened EXPLORE again from VOICE MSG (2026-11-07 18:20)
    if matches!(ev.key, KeyId::Exit | KeyId::Digit3 | KeyId::Digit6) && !matches!(ev.kind, KeyEventKind::Single) {
        return;
    }
    let t = st();
    // the editor takes a held arrow, not a held digit (multi-tap counts presses)
    if t.view == View::Edit && ev.kind == KeyEventKind::Repeat && !matches!(ev.key, KeyId::Up | KeyId::Down) {
        return;
    }
    t.dirty = true;
    if ev.key != KeyId::Exit {
        t.leave_armed = false;
    }
    if ev.key != KeyId::Digit3 {
        t.del_armed = false;
    }
    if ev.key != KeyId::Digit6 {
        t.adel_armed = false;
    }
    match t.view {
        // the arrows move along the list while the automatic listen goes on, wrapping at
        // either end (Jeff 2026-11-08); a redraw at most every 150 ms, so a held arrow
        // doesn't take poll time from the CW decoder (the 400 ms redraw catches up)
        View::Listen if t.auto_now && matches!(ev.key, KeyId::Up | KeyId::Down) => {
            step_sel(t, ev.key == KeyId::Up);
            t.dirty = uptime::now().wrapping_sub(t.last_draw) >= SCROLL_TICKS;
        }
        View::Listen => {
            // any other key ends the listen
            ctx_on(app, syst);
            side_end();
            unsafe { ffi::xl_stop(t.xl, 3) };
            ctx_off();
            monitor_off(app, syst);
            if core::mem::take(&mut t.auto_now) {
                auto_done();
            }
            note("LISTEN STOPPED");
            t.view = View::List;
        }
        View::Ask => match ev.key {
            KeyId::Digit5 => {
                ask_write(app, syst);
                t.view = View::List;
            }
            KeyId::Exit => {
                note("KEPT SAVED CALL");
                t.view = View::List;
            }
            _ => {}
        },
        View::Edit => edit_key(app, syst, ev.key),
        View::Stations => stations_key(app, syst, ev.key),
        View::Try => match ev.key {
            KeyId::Side1 | KeyId::Exit => {
                monitor_off(app, syst);
                t.view = View::List;
            }
            KeyId::Digit0 => listen_start(app, syst),
            KeyId::Digit5 => save(app, syst),
            _ => {}
        },
        View::List => match ev.key {
            KeyId::Up | KeyId::Down => step_sel(t, ev.key == KeyId::Up),
            // Try on side key 1 (the release plan, Jeff 2026-11-13: MENU is the module list)
            KeyId::Side1 => try_start(app, syst),
            KeyId::Digit0 => listen_start(app, syst),
            KeyId::Digit5 => save(app, syst),
            KeyId::Asterisk => {
                if let Some(c) = shown(t.sel as u32) {
                    c.flags ^= EX_F_IGNORED;
                    note(if c.flags & EX_F_IGNORED != 0 { "IGNORED" } else { "UNIGNORED" });
                }
            }
            KeyId::Pound => {
                ctx_on(app, syst);
                unsafe { ffi::xs_close(t.xs) };
                start_band(t.band + 1);
                ctx_off();
            }
            KeyId::Digit7 => {
                dump();
                note("LIST SENT ON SERIAL");
            }
            KeyId::Digit1 => {
                t.auto = !t.auto;
                note(if t.auto { "AUTO ID ON" } else { "AUTO ID OFF" });
            }
            KeyId::Digit3 => delete(app, syst),
            KeyId::Digit6 => delete_auto(app, syst),
            KeyId::Digit8 => edit_start(app, syst),
            KeyId::Digit9 => {
                t.auto_spk = !t.auto_spk;
                note(if t.auto_spk { "AUTO ID SPEAKER ON" } else { "AUTO ID SPEAKER OFF" });
            }
            KeyId::Exit => {
                if t.leave_armed {
                    leave(app, syst);
                    return;
                }
                t.leave_armed = true;
                note("EXIT AGAIN: LEAVE");
            }
            _ => {}
        },
    }
}

// ---- the station editor (the release plan, Jeff 2026-11-13; c/k6/sted.c in code page 12) ----

/// sted.h's results
const STED_SAVE: i32 = 1;
const STED_EXIT: i32 = 2;
const STED_MENU: i32 = 3;
/// the editor's rows on screen (Jeff's layout D, 2026-11-14: the 4x6 7 pixel rows apart under
/// the 5x7 title, the keys in the 4x6 at the foot) and the fields it has (sted.h SF_N)
const ED_ROWS: i32 = 6;
const ED_FIELDS: i32 = 9;
const ED_TOP: i32 = 12;
const ED_PITCH: i32 = 7;

/// Key 8 on a row: its saved entry into the editor, or a new station from what the sweep found
/// there (frequency, offset, tone, call). The sweep waits, as for Try.
fn edit_start(app: &mut App, syst: &mut SYST) {
    let t = st();
    if t.rt_res < 0 {
        note("NO TABLE");
        return;
    }
    #[cfg(feature = "voice-explore")]
    if page::check(page::EDIT) != 0 {
        note("NO EDITOR PAGE (12)");
        return;
    }
    let Some(c) = shown(t.sel as u32) else { return };
    let mut r: RtRec = RT_REC0;
    ctx_on(app, syst);
    let saved = c.flags & EX_F_KNOWN != 0 && unsafe { ffi::rt_find(&mut t.rt, 0, c.hz, RT_TOL_HZ, &mut r) } >= 0;
    ctx_off();
    if !saved {
        r = RT_REC0;
        unsafe { ffi::ex_to_rt(c, &mut r) };
        r.id = 0;
    }
    pause_sweep(app, syst);
    unsafe { ffi::sted_open(&mut t.ed, &r) };
    rl!("K6 EX edit {} id={}", r.rx_hz, r.id);
    t.view = View::Edit;
}

fn edit_key(app: &mut App, syst: &mut SYST, key: KeyId) {
    let k = match key {
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
        KeyId::Up => b'U',
        KeyId::Down => b'D',
        KeyId::Exit => b'X',
        _ => return,
    };
    let t = st();
    match unsafe { ffi::sted_key(&mut t.ed, k as i32, now_ms()) } {
        STED_SAVE => edit_save(app, syst),
        STED_EXIT | STED_MENU => {
            rl!("K6 EX edit end");
            t.view = View::List;
            if core::mem::take(&mut t.st_from) {
                t.view = View::Stations;
                st_seek(app, syst, t.st_i as i32);
            }
        }
        _ => {}
    }
}

// ---- STATIONS (MENU's third row: the release plan, Jeff 2026-11-20 "yes please") ----
//
// The saved table a station at a time, its record in `ask` (the Ask view's, never on screen at
// the same time), read on a key, never while drawing (the table's code page would swap in and
// out of the pool on every band). UP/DOWN step through the live entries (wrapping), 8 edits the
// one shown, 0 opens the editor on a new one, 3 twice deletes it, EXIT goes to the list. The
// sweep waits, as for the editor.

/// MENU > STATIONS (EXPLORE already entered)
pub(super) fn stations(app: &mut App, syst: &mut SYST) {
    let t = st();
    if !t.on {
        return;
    }
    if t.rt_res < 0 {
        note("NO TABLE");
        return;
    }
    pause_sweep(app, syst);
    t.view = View::Stations;
    st_seek(app, syst, 0);
}

/// `ask` = the table's i-th live entry (i wraps), st_i = i; rx_hz 0 when the table is empty
fn st_seek(app: &mut App, syst: &mut SYST, i: i32) {
    let t = st();
    let n = t.rt.live as i32;
    t.ask = RT_REC0;
    t.st_i = 0;
    if n == 0 {
        return;
    }
    let i = i.rem_euclid(n);
    ctx_on(app, syst);
    let mut slot = -1;
    let mut k = 0;
    loop {
        slot = unsafe { ffi::rt_next(&mut t.rt, slot, &mut t.ask) };
        if slot < 0 || k == i {
            break;
        }
        k += 1;
    }
    ctx_off();
    t.st_i = i as u8;
}

fn stations_key(app: &mut App, syst: &mut SYST, key: KeyId) {
    let t = st();
    match key {
        KeyId::Up | KeyId::Down => st_seek(app, syst, t.st_i as i32 + if key == KeyId::Up { -1 } else { 1 }),
        KeyId::Digit8 | KeyId::Digit0 => {
            #[cfg(feature = "voice-explore")]
            if page::check(page::EDIT) != 0 {
                note("NO EDITOR PAGE (12)");
                return;
            }
            let r = if key == KeyId::Digit8 { t.ask } else { RT_REC0 };
            unsafe { ffi::sted_open(&mut t.ed, &r) };
            t.st_from = true;
            t.view = View::Edit;
        }
        KeyId::Digit3 if t.ask.rx_hz != 0 => {
            if !t.del_armed {
                t.del_armed = true;
                note("3 AGAIN: DELETE");
                return;
            }
            t.del_armed = false;
            ctx_on(app, syst);
            let r = unsafe { ffi::rt_delete(&mut t.rt, t.ask.id) };
            if r >= 0 {
                unsafe { ffi::ex_unknow(&mut t.ex, t.ask.rx_hz) };
            }
            ctx_off();
            rl!("K6 EX st delete {} r={} live={}", t.ask.rx_hz, r, t.rt.live);
            note(if r >= 0 { "DELETED" } else { "DELETE FAILED" });
            st_seek(app, syst, t.st_i as i32);
        }
        KeyId::Exit => t.view = View::List,
        _ => {}
    }
}

/// Save in the editor: the record through rt_put (the table page in for it, the editor's back
/// for the next key), the list's known mark moved with a new frequency
fn edit_save(app: &mut App, syst: &mut SYST) {
    let t = st();
    unsafe { ffi::sted_commit(&mut t.ed) };
    let mut r = t.ed.r;
    let was_hz = t.ed.was.rx_hz;
    ctx_on(app, syst);
    let slot = unsafe { ffi::rt_put(&mut t.rt, &mut r) };
    ctx_off();
    rl!("K6 EX edit save {} id={} slot={} live={}", r.rx_hz, r.id, slot, t.rt.live);
    let mut b: TextBuf<21> = TextBuf::new();
    if slot >= 0 {
        if was_hz != 0 && was_hz != r.rx_hz {
            unsafe { ffi::ex_unknow(&mut t.ex, was_hz) };
        }
        unsafe { ffi::ex_know(&mut t.ex, r.rx_hz) };
        unsafe { ffi::sted_saved(&mut t.ed, &r) };
        out!(b, "SAVED #{}", r.id);
    } else {
        out!(b, "SAVE FAILED {}", slot);
    }
    note(b.as_str());
}

/// stop the sweep for a while: every over that's up ends now, so its H line is honest
fn pause_sweep(app: &mut App, syst: &mut SYST) {
    st().resume = true;
    ctx_on(app, syst);
    unsafe { ffi::xs_close(st().xs) };
    ctx_off();
}

fn try_start(app: &mut App, syst: &mut SYST) {
    let t = st();
    let Some(c) = shown(t.sel as u32) else { return };
    c.flags |= EX_F_TRIED;
    t.hz = c.hz;
    t.off = c.off_hz;
    pause_sweep(app, syst);
    monitor_on(app, syst, t.hz);
    rl!("K6 EX try {} off={}", t.hz, t.off);
    t.view = View::Try;
}

fn listen_start(app: &mut App, syst: &mut SYST) {
    let t = st();
    if t.view != View::Try {
        let Some(c) = shown(t.sel as u32) else { return };
        t.hz = c.hz;
        t.off = c.off_hz;
        pause_sweep(app, syst);
        monitor_on(app, syst, t.hz);
    }
    t.auto_now = false;
    ctx_on(app, syst);
    unsafe { ffi::xl_start(t.xl, &MOD_API, t.hz, LISTEN_S) };
    side_start(true);
    ctx_off();
    t.view = View::Listen;
}

/// A candidate with no call keyed up: go and listen (the sweep's hot list stays as it is, so the
/// over goes on being followed when the sweep comes back). On the speaker unless key 9 muted it.
fn auto_pick(app: &mut App, syst: &mut SYST) {
    let t = st();
    let mut up = [0u32; 10];
    let n = unsafe { ffi::xs_up(t.xs, up.as_mut_ptr(), up.len() as u32) };
    if n == 0 {
        return;
    }
    let p = unsafe { ffi::ex_pick_id(&mut t.ex, up.as_ptr(), n) };
    if p.is_null() {
        return;
    }
    let c = unsafe { &mut *p };
    // a saved repeater keyed up for the first time since entry: its candidate is new, so the
    // table's call isn't on it yet (bench 8, 02:18: the listener went to 440.850, saved as AG7EW)
    if c.flags & EX_F_KNOWN != 0 && c.flags & EX_F_LOOKED == 0 {
        ctx_on(app, syst);
        known_calls();
        ctx_off();
        if c.call[0] != 0 {
            c.listens = c.listens.saturating_sub(1);
            return;
        }
    }
    let max_s = unsafe { ffi::ex_listen_cap(c, AUTO_MAX_S, AUTO_MIN_S) };
    auto_listen(app, syst, c, max_s);
}

/// the automatic listen on c for up to max_s
fn auto_listen(app: &mut App, syst: &mut SYST, c: &mut Cand, max_s: u32) {
    let t = st();
    t.mid = false;
    t.hz = c.hz;
    t.off = c.off_hz;
    t.auto_now = true;
    // a carrier keyed for the first time has no over yet, so no level (bench 4, 21:09: hi 0 made
    // the -18 dBm ID count as down, and the listen ended after 3 s): auto_check takes it from
    // the reads then
    t.auto_lvl = if c.overs == 0 { -127 } else { c.hi - AUTO_DROP_DB };
    t.auto_seen_ms = now_ms();
    t.auto_start_ms = t.auto_seen_ms;
    t.auto_max_s = max_s;
    t.resume = true;
    if t.auto_spk {
        monitor_on(app, syst, t.hz);
    } else {
        app.radio.retune_rx(syst, t.hz, true);
    }
    ctx_on(app, syst);
    unsafe { ffi::xl_start(t.xl, &MOD_API, t.hz, t.auto_max_s) };
    side_start(t.auto_spk);
    ctx_off();
    rl!("K6 EX auto {} listens={} heard={} max={}", t.hz, c.listens, c.heard_s, t.auto_max_s);
    t.view = View::Listen;
    t.dirty = true;
}

/// The peek heard keying on an up carrier: if it has no call, listen now (that ID's tail: logged,
/// not kept) and watch the station from now on.
fn tone_heard(app: &mut App, syst: &mut SYST, hz: u32) {
    let t = st();
    let p = unsafe { ffi::ex_find(&mut t.ex, hz) };
    if p.is_null() {
        return;
    }
    let c = unsafe { &mut *p };
    if c.call[0] != 0 || c.flags & (EX_F_IGNORED | EX_F_SPUR) != 0 {
        return;
    }
    let now = now_ms();
    let misses = match t.watch.iter().position(|w| w.0 == hz) {
        // already watched: its gap holds for peeks too
        Some(i) if now.wrapping_sub(t.watch[i].1) < watch_gap(t.watch[i].2) => return,
        Some(i) => {
            t.watch[i].1 = now;
            t.watch[i].2 = t.watch[i].2.saturating_add(1);
            t.watch[i].2
        }
        None => {
            // a full list: the one listened to longest ago makes room
            let i = t.watch.iter().position(|w| w.0 == 0);
            let i = i.unwrap_or_else(|| (0..t.watch.len()).min_by_key(|&k| t.watch[k].1).unwrap_or(0));
            t.watch[i] = (hz, now, 0);
            0
        }
    };
    rl!("K6 EX tone {} watch gap={} s", hz, watch_gap(misses) / 1000);
    auto_listen(app, syst, c, AUTO_MAX_S);
    t.mid = true;
}

/// A watched station with no call keyed up: listen to the whole carrier (true)
fn watch_up(app: &mut App, syst: &mut SYST) -> bool {
    let t = st();
    if t.watch.iter().all(|w| w.0 == 0) {
        return false;
    }
    let mut up = [0u32; 10];
    let n = unsafe { ffi::xs_up(t.xs, up.as_mut_ptr(), up.len() as u32) } as usize;
    let now = now_ms();
    for i in 0..t.watch.len() {
        let (hz, last, misses) = t.watch[i];
        if hz == 0 || now.wrapping_sub(last) < watch_gap(misses) || !up[..n].contains(&hz) {
            continue;
        }
        let p = unsafe { ffi::ex_find(&mut t.ex, hz) };
        if p.is_null() || unsafe { (*p).call[0] } != 0 {
            t.watch[i] = (0, 0, 0);
            continue;
        }
        t.watch[i].1 = now;
        t.watch[i].2 = misses.saturating_add(1);
        rl!("K6 EX watch {} gap={} s", hz, watch_gap(misses.saturating_add(1)) / 1000);
        auto_listen(app, syst, unsafe { &mut *p }, AUTO_MAX_S);
        return true;
    }
    false
}

/// An automatic listen, between steps: a call decoded once is kept, a second time alike (on this
/// listen or a later one) it's confirmed; the listen ends AUTO_TAIL_MS after the carrier drops.
fn auto_check(app: &mut App, syst: &mut SYST, r: i32) {
    let t = st();
    let now = now_ms();
    let lvl = rssi_dbm(app, syst);
    if lvl >= t.auto_lvl {
        t.auto_seen_ms = now;
        // the loudest read so far sets what counts as up
        t.auto_lvl = t.auto_lvl.max(lvl - AUTO_DROP_DB);
    }
    if r == XL_MSG {
        t.dirty = true;
        let mut info: XlInfo = unsafe { core::mem::zeroed() };
        unsafe { ffi::xl_info(t.xl, &mut info) };
        if t.mid && info.msgs == 1 {
            // begun mid-ID: this is its tail (WA7DMR heard from the A would be "A7DMR")
            rl!("K6 EX tail {} {}", t.hz, cstr(&info.text));
        } else if info.call[0] != 0 {
            let same = t.once.iter().any(|(hz, call)| *hz == t.hz && cstr(call) == cstr(&info.call));
            if same {
                listen_end(app, syst, XL_OK);
                return;
            }
            let i = t.once_i as usize % t.once.len();
            t.once[i] = (t.hz, info.call);
            t.once_i = t.once_i.wrapping_add(1);
        }
    }
    if now.wrapping_sub(t.auto_seen_ms) >= AUTO_TAIL_MS {
        listen_end(app, syst, XL_TIME);
    }
}

/// The listen's side detectors on (inside ctx_on, after xl_start): DMR mutes the speaker when the
/// listen plays on it (spk), the CTCSS read runs throughout
fn side_start(spk: bool) {
    let t = st();
    t.dmr_now = false;
    if t.xd.is_null() {
        return;
    }
    // DMR by default (Jeff 2026-11-08): a row a listen heard DMR on, now or in the saved table,
    // starts shut and opens only once XD_CLEAR_MS go by with no DMR sync (xdmr.c xd_known). The
    // table is looked up first: in the voice + EXPLORE image its code is a page of its own, and
    // loading it once the detector runs would cost the detector its first syncs
    let p = unsafe { ffi::ex_find(&mut t.ex, t.hz) };
    let mut known = !p.is_null() && unsafe { (*p).flags } & EX_F_DMR != 0;
    if !known && t.rt_res >= 0 {
        let mut rec: RtRec = unsafe { core::mem::zeroed() };
        if unsafe { ffi::rt_find(&mut t.rt, 0, t.hz, RT_TOL_HZ, &mut rec) } >= 0 && rec.flags & RT_F_DMR != 0 {
            known = true;
            if !p.is_null() {
                unsafe { (*p).flags |= EX_F_DMR };
            }
        }
    }
    unsafe {
        TRACE_N = 0;
        ffi::xd_start(t.xd, &MOD_API, spk as i32, t.cps8);
        DMR_ADJ = ffi::xd_adj(t.xd) as i16;
    }
    if known {
        unsafe { ffi::xd_known(t.xd) };
    }
    trace!("K6 EX t started adj={} known={}", unsafe { DMR_ADJ }, known as u8);
}

/// After a listen step: DMR heard marks the row (once), and the header follows DMR on and off
fn dmr_seen() {
    let t = st();
    if t.xd.is_null() {
        return;
    }
    let f = unsafe { ffi::xd_flags(t.xd) };
    if (f & XD_NOW != 0) != t.dmr_now {
        t.dmr_now = f & XD_NOW != 0;
        t.dirty = true;
    }
    if f & XD_EVER != 0 {
        let p = unsafe { ffi::ex_find(&mut t.ex, t.hz) };
        if !p.is_null() && unsafe { (*p).flags } & EX_F_DMR == 0 {
            unsafe { (*p).flags |= EX_F_DMR };
            rl!("K6 EX dmr {}", t.hz);
            t.dirty = true;
        }
    }
}

/// The listen's side detectors off (inside ctx_on, before xl_stop): the tone heard goes on the
/// row, and with DMR into the saved entry by the rule for calls (Jeff 2026-11-07): a blank tone
/// fills, a different one is shown, not written (rt_heard_apply)
fn side_end() {
    let t = st();
    t.dmr_now = false;
    if t.xd.is_null() {
        return;
    }
    let (tone, dmr) = unsafe { (ffi::xd_tone(t.xd) as u16, ffi::xd_flags(t.xd) & XD_EVER != 0) };
    trace!("K6 EX t end tone={} dmr={}", tone, dmr as u8);
    unsafe {
        t.cps8 = ffi::xd_rate8(t.xd);
        ffi::xd_stop(t.xd);
        DMR_ADJ = 0;
    }
    trace!("K6 EX t stopped");
    if tone != 0 {
        unsafe { ffi::ex_tone(&mut t.ex, t.hz, tone) };
    }
    // the row's DMR mark: set by any DMR, cleared after EX_CLEAN_N automatic listens of
    // EX_CLEAN_S or more with none (a listen by hand never counts: it may sit on dead air)
    let secs = if t.auto_now { now_ms().wrapping_sub(t.auto_start_ms) / 1000 } else { 0 };
    let h = unsafe { ffi::ex_dmr_heard(&mut t.ex, t.hz, dmr as i32, secs) };
    let cleared = h == EX_DMR_CLEARED;
    if cleared {
        rl!("K6 EX dmr clear {}", t.hz);
    }
    if h == EX_DMR_SAVE && t.rt_res >= 0 {
        auto_save();
    }
    if t.rt_res < 0 || (tone == 0 && !dmr && !cleared) {
        return;
    }
    let d: i32 = if dmr { 1 } else if cleared { -1 } else { 0 };
    let mut rec: RtRec = unsafe { core::mem::zeroed() };
    let k = unsafe { ffi::rt_heard_apply(&mut t.rt, t.hz, RT_TOL_HZ, tone, d, &mut rec) };
    trace!("K6 EX t applied {}", k);
    rl!("K6 EX heard {} tone={} dmr={} rt={} saved_tone={}", t.hz, tone, d, k, rec.rx_tone);
    if k == RT_TONE_FILL {
        note("TONE SAVED");
    } else if k == RT_TONE_DIFFERS {
        note("TONE DIFFERS FROM SAVED");
    }
}

/// DMR heard on a second listen at a row not in the table (Jeff 2026-11-09: "saving a DMR
/// channel table to SPI after two listens makes sense"): a new entry marked RT_F_AUTO, so it
/// starts muted after the next power-up too; key 6 twice deletes them all, key 3 twice one.
/// Inside ctx_on (side_end).
fn auto_save() {
    let t = st();
    let p = unsafe { ffi::ex_find(&mut t.ex, t.hz) };
    if p.is_null() {
        return;
    }
    let mut r: RtRec = unsafe { core::mem::zeroed() };
    unsafe { ffi::ex_to_rt(p, &mut r) };
    r.flags |= RT_F_AUTO | RT_F_DMR;
    let slot = unsafe { ffi::rt_put(&mut t.rt, &mut r) };
    if slot >= 0 {
        unsafe { ffi::ex_know(&mut t.ex, t.hz) };
    }
    rl!("K6 EX autosave {} id={} slot={} live={}", t.hz, r.id, slot, t.rt.live);
    note(if slot >= 0 { "DMR SAVED (6 6: DEL)" } else { "DMR SAVE FAILED" });
}

/// An automatic listen ended: the next pick waits for a whole pass, and the time it took counts
/// against the station's next cap (ex_listen_cap).
fn auto_done() {
    let t = st();
    let now = now_ms();
    for w in t.watch.iter_mut() {
        if w.0 == t.hz {
            w.1 = now;
        }
    }
    t.auto_mark = passes();
    let s = now_ms().wrapping_sub(t.auto_start_ms) / 1000;
    unsafe { ffi::ex_heard(&mut t.ex, t.hz, s) };
}

/// A listen ended: a confirmed call goes on the candidate, and into the table by Jeff's rule
/// (2026-11-07): a saved entry with no call takes it at once, a different saved call waits for
/// MENU, no saved entry keeps it in RAM until Save.
fn listen_end(app: &mut App, syst: &mut SYST, r: i32) {
    let t = st();
    let mut info: XlInfo = unsafe { core::mem::zeroed() };
    ctx_on(app, syst);
    unsafe { ffi::xl_info(t.xl, &mut info) };
    side_end();
    unsafe { ffi::xl_stop(t.xl, r) };
    trace!("K6 EX t xl stopped");
    let auto = core::mem::take(&mut t.auto_now);
    if auto {
        auto_done();
    }
    monitor_off(app, syst);
    trace!("K6 EX t monitor off");
    t.view = View::List;
    if r != XL_OK {
        ctx_off();
        if !auto {
            note("NO ID HEARD");
        }
        return;
    }
    t.heard = info.call;
    t.heard_fl = info.fl;
    t.heard_tone = info.tone_hz;
    t.heard_wpm = info.wpm;
    unsafe { ffi::ex_cw(&mut t.ex, t.hz, info.call.as_ptr()) };
    let mut rec: RtRec = unsafe { core::mem::zeroed() };
    let k = if t.rt_res >= 0 {
        unsafe { ffi::rt_cw_check(&mut t.rt, t.hz, RT_TOL_HZ, info.call.as_ptr(), &mut rec) }
    } else {
        0
    };
    let fl = cw_flags(info.fl);
    let mut out = 0;
    if k == 2 {
        out = unsafe {
            ffi::rt_cw_apply(&mut t.rt, t.hz, RT_TOL_HZ, info.call.as_ptr(), fl, info.tone_hz as i32, info.wpm as i32, 0, &mut rec)
        };
    }
    ctx_off();
    rl!("K6 EX cw {} call={} check={} apply={} id={}", t.hz, cstr(&info.call), k, out, rec.id);
    match k {
        1 => note("ID SAME AS SAVED"),
        2 => note("CALL SAVED"),
        3 => {
            t.ask = rec;
            t.view = View::Ask;
        }
        // a new frequency: saved with its call, so it goes under the rule (Jeff 2026-11-07 22:46)
        _ => {
            let c = unsafe { ffi::ex_find(&mut t.ex, t.hz) };
            if t.rt_res >= 0 && !c.is_null() {
                save_cand(app, syst, unsafe { &mut *c });
            } else {
                note("ID HEARD (NOT SAVED)");
            }
        }
    }
}

/// CW_CALL_P/M/R (cwid.h) to the table's call flags, from a CW decode heard twice alike
fn cw_flags(f: u8) -> u8 {
    (f & 0x07) | RT_C_FROM_CW | RT_C_TWICE
}

fn ask_write(app: &mut App, syst: &mut SYST) {
    let t = st();
    let mut rec: RtRec = unsafe { core::mem::zeroed() };
    ctx_on(app, syst);
    let r = unsafe {
        ffi::rt_cw_apply(
            &mut t.rt,
            t.hz,
            RT_TOL_HZ,
            t.heard.as_ptr(),
            cw_flags(t.heard_fl),
            t.heard_tone as i32,
            t.heard_wpm as i32,
            1,
            &mut rec,
        )
    };
    ctx_off();
    rl!("K6 EX cw write {} call={} r={} id={}", t.hz, cstr(&t.heard), r, rec.id);
    note(if r == 4 { "CALL UPDATED" } else { "WRITE FAILED" });
}

/// Save the selected candidate (or Try's): a new entry, or a new version of the saved one on
/// its frequency, keeping a typed name and call
fn save(app: &mut App, syst: &mut SYST) {
    let t = st();
    if t.rt_res < 0 {
        note("NO TABLE");
        return;
    }
    let c = if t.view == View::Try {
        let mut found = None;
        let mut i = 0;
        while let Some(c) = cand(i) {
            if c.hz == t.hz {
                found = Some(c);
                break;
            }
            i += 1;
        }
        found
    } else {
        shown(t.sel as u32)
    };
    let Some(c) = c else { return };
    save_cand(app, syst, c);
}

/// a candidate into the table (a new entry, or a new version of the saved one there)
fn save_cand(app: &mut App, syst: &mut SYST, c: &mut Cand) {
    let t = st();
    let mut r: RtRec = unsafe { core::mem::zeroed() };
    let mut old: RtRec = unsafe { core::mem::zeroed() };
    unsafe { ffi::ex_to_rt(c, &mut r) };
    ctx_on(app, syst);
    if unsafe { ffi::rt_find(&mut t.rt, 0, c.hz, RT_TOL_HZ, &mut old) } >= 0 {
        r.id = old.id;
        r.group = old.group;
        if old.flags & RT_F_NAME_USER != 0 {
            r.name = old.name;
            r.flags |= RT_F_NAME_USER;
        }
        if old.flags & RT_F_CALL_USER != 0 || (r.call[0] == 0 && old.call[0] != 0) {
            r.call = old.call;
            r.call_flags = old.call_flags;
            r.cw = old.cw;
            r.flags |= old.flags & RT_F_CALL_USER;
        }
    }
    let slot = unsafe { ffi::rt_put(&mut t.rt, &mut r) };
    if slot >= 0 {
        unsafe { ffi::ex_know(&mut t.ex, c.hz) };
    }
    let erases = unsafe { (*addr_of!(MOD_CTX)).fl_erases };
    ctx_off();
    rl!(
        "K6 EX save {} id={} slot={} live={} erases={}/{}",
        c.hz, r.id, slot, t.rt.live, erases, FL_ERASES
    );
    let mut b: TextBuf<21> = TextBuf::new();
    if slot >= 0 {
        out!(b, "SAVED #{} ({} in table)", r.id, t.rt.live);
    } else {
        out!(b, "SAVE FAILED {}", slot);
    }
    note(b.as_str());
}

/// Key 3 on a saved row, twice: its table entry goes, and the row is new again (Jeff 2026-11-07:
/// "Is there a way to delete entries from the table")
fn delete(app: &mut App, syst: &mut SYST) {
    let t = st();
    let Some(c) = shown(t.sel as u32) else { return };
    if c.flags & EX_F_KNOWN == 0 || t.rt_res < 0 {
        note("NOT IN THE TABLE");
        return;
    }
    if !t.del_armed {
        t.del_armed = true;
        note("3 AGAIN: DELETE");
        return;
    }
    t.del_armed = false;
    let hz = c.hz;
    let mut old: RtRec = unsafe { core::mem::zeroed() };
    ctx_on(app, syst);
    let mut n = 0;
    let mut r = 0;
    // every entry within the tolerance (a frequency saved twice goes at once)
    while n < 8 && unsafe { ffi::rt_find(&mut t.rt, 0, hz, RT_TOL_HZ, &mut old) } >= 0 {
        r = unsafe { ffi::rt_delete(&mut t.rt, old.id) };
        if r < 0 {
            break;
        }
        n += 1;
    }
    if r >= 0 {
        unsafe { ffi::ex_unknow(&mut t.ex, hz) };
    }
    ctx_off();
    rl!("K6 EX delete {} n={} r={} live={}", hz, n, r, t.rt.live);
    let mut b: TextBuf<21> = TextBuf::new();
    if r >= 0 {
        out!(b, "DELETED ({} in table)", t.rt.live);
    } else {
        out!(b, "DELETE FAILED {}", r);
    }
    note(b.as_str());
}

/// Key 6 twice: every entry the explorer saved on its own (RT_F_AUTO) goes, for leaving a
/// repeater's area (Jeff 2026-11-09: "we should be able to delete these entries in case we
/// move out of the repeater's area"); an entry Saved by hand since is the user's and stays
fn delete_auto(app: &mut App, syst: &mut SYST) {
    let t = st();
    if t.rt_res < 0 {
        note("NO TABLE");
        return;
    }
    if !t.adel_armed {
        t.adel_armed = true;
        note("6 AGAIN: DEL AUTO DMR");
        return;
    }
    t.adel_armed = false;
    ctx_on(app, syst);
    let mut n = 0u32;
    let mut r = 0;
    // at most 128 batches of 8: more than the table's ~960 slots
    'outer: for _ in 0..128 {
        // a batch of ids first, then the deletes: rt_next walks slots, which a delete changes
        let mut ids = [(0u16, 0u32); 8];
        let mut k = 0;
        let mut rec: RtRec = unsafe { core::mem::zeroed() };
        let mut slot = -1;
        while k < ids.len() {
            slot = unsafe { ffi::rt_next(&mut t.rt, slot, &mut rec) };
            if slot < 0 {
                break;
            }
            if rec.flags & RT_F_AUTO != 0 {
                ids[k] = (rec.id, rec.rx_hz);
                k += 1;
            }
        }
        if k == 0 {
            break;
        }
        for &(id, hz) in &ids[..k] {
            r = unsafe { ffi::rt_delete(&mut t.rt, id) };
            if r < 0 {
                break 'outer;
            }
            unsafe { ffi::ex_unknow(&mut t.ex, hz) };
            n += 1;
        }
    }
    ctx_off();
    rl!("K6 EX delauto n={} r={} live={}", n, r, t.rt.live);
    let mut b: TextBuf<21> = TextBuf::new();
    if r >= 0 {
        out!(b, "DELETED {} ({} LEFT)", n, t.rt.live);
    } else {
        out!(b, "DELETE FAILED {}", r);
    }
    note(b.as_str());
}

/// The list and the table on the serial report: K6 CAND and K6 RT lines (tools/k6_explore.py
/// score reads them)
fn dump() {
    let t = st();
    let mut info: XsInfo = unsafe { core::mem::zeroed() };
    unsafe { ffi::xs_info(t.xs, &mut info) };
    rl!(
        "K6 CAND band={} n={} sweeps={} bins={} ms={}",
        BANDS[t.band as usize].0,
        t.ex.n,
        info.sweeps,
        info.bins,
        info.ms
    );
    let mut i = 0;
    while let Some(c) = cand(i) {
        rl!(
            "K6 CAND {} {} {} off={} tone={} call={} score={} overs={} on={} hi={} lo={} ids={} skirts={} fl={}",
            i,
            c.grade as char,
            c.hz,
            c.off_hz,
            c.tone,
            cstr(&c.call),
            c.score,
            c.overs,
            c.on_ms / 1000,
            c.hi,
            c.lo,
            c.ids,
            c.skirts,
            c.flags
        );
        i += 1;
    }
    if t.rt_res >= 0 {
        let mut r: RtRec = unsafe { core::mem::zeroed() };
        let mut slot = -1;
        loop {
            slot = unsafe { ffi::rt_next(&mut t.rt, slot, &mut r) };
            if slot < 0 {
                break;
            }
            rl!(
                "K6 RT {} {} off={} tone={} call={} score={} rssi={} fl={}",
                r.id,
                r.rx_hz,
                r.tx_off,
                r.tx_tone,
                cstr(&r.call),
                r.score as char,
                r.best_rssi as i32 - 200,
                r.flags
            );
        }
    }
}

// ---- the screen ----

/// the level bar's column on the list (after "r 443.525 +5.0 ") and its full width
const BAR_X: i32 = 1 + 15 * 4;
const BAR_W: u32 = 21;

/// the footer's row number for line()
const FOOT: i32 = 8;

/// Jeff's pick (2026-11-07, the font-size sheet's inverted mixed layout): row 0 is a header,
/// 6x10 drawn dark on a lit bar (21 characters; the stock 5x7 in its cells since 2026-11-10, ui::glyph);
/// rows 1-7 are the list in 4x6 (32 characters);
/// row FOOT is the footer, the hint or the selected row's detail, also 4x6
fn line<D>(lcd: &mut D, r: i32, t: &str)
where
    D: DrawTarget<Color = BinaryColor>,
{
    if r == 0 {
        fill(lcd, 0, 10, BinaryColor::On);
        glyph::text(lcd, 1, 1, t, false, false); // the stock 5x7, caps where 6x10's were
        return;
    }
    row(lcd, if r >= FOOT { 58 } else { 11 + 6 * (r - 1) }, t);
}

/// full-width rows y..y+h lit or dark
fn fill<D>(lcd: &mut D, y: i32, h: u32, c: BinaryColor)
where
    D: DrawTarget<Color = BinaryColor>,
{
    lcd.fill_solid(&Rectangle::new(Point::new(0, y), Size::new(128, h)), c).ok();
}

/// a 4x6 line with its top at y
fn row<D>(lcd: &mut D, y: i32, t: &str)
where
    D: DrawTarget<Color = BinaryColor>,
{
    glyph::text(lcd, 1, y, t, true, true);
}

/// the candidate rows (Jeff 2026-11-07): new ones above a rule, known (saved) ones below it,
/// the selected row inverted, an arrow at the one at boxed_hz (being listened to; 0 none).
/// The selected row's candidate, for the footer.
fn list_rows<D>(lcd: &mut D, t: &State, boxed_hz: u32) -> Option<&'static mut Cand>
where
    D: DrawTarget<Color = BinaryColor>,
{
    // keep the selected row on screen
    let top = (t.sel as usize).saturating_sub(ROWS - 1) as u32;
    // the row above the screen (for the rule) and the screen's rows from one sort: a shown()
    // a row ranked the whole list again, and the 8 kHz voice + EXPLORE image draws the screen
    // once for each of its 8 bands (Jeff 2026-11-12: 57 found, the keys took seconds)
    let mut got = [core::ptr::null_mut::<Cand>(); ROWS + 1];
    let first = top.saturating_sub(1);
    let n = unsafe { ffi::ex_view_rows(&mut st().ex, first, got.as_mut_ptr(), (ROWS + 1) as u32) } as usize;
    let (above, rows) = if top > 0 { (got[0], &got[1..n.max(1)]) } else { (core::ptr::null_mut(), &got[..n]) };
    let mut sel_p = core::ptr::null_mut::<Cand>();
    // new repeaters above a rule, known (saved) ones below it (Jeff 2026-11-07): the
    // rows under it move down 2 px, which the 4 px above the footer leave room for
    let mut shift = 0;
    let mut was_new = !above.is_null() && unsafe { (*above).flags } & EX_F_KNOWN == 0;
    for (k, &p) in rows.iter().take(ROWS).enumerate() {
        let k = k as u32;
        let c = unsafe { &mut *p };
        let known = c.flags & EX_F_KNOWN != 0;
        let ruled = known && was_new && shift == 0;
        if ruled {
            fill(lcd, 11 + 6 * k as i32, 1, BinaryColor::On);
            shift = 2;
        }
        was_new = !known;
        let mut b: TextBuf<32> = TextBuf::new();
        let ign = c.flags & EX_F_IGNORED != 0;
        let bird = !ign && c.flags & EX_F_SPUR != 0;
        // a confirmed call: a check mark for the grade (Jeff's pick "B", 2026-11-07 22:54)
        let checked = !ign && !bird && c.call[0] != 0;
        let g = if ign { 'x' } else if bird || checked { ' ' } else { c.grade as char };
        out!(b, "{} ", g);
        mhz(&mut b, c.hz);
        out!(b, " ");
        off_text(&mut b, c);
        let y = 11 + 6 * k as i32 + shift;
        let sel = top + k == t.sel as u32;
        if sel {
            sel_p = p;
        }
        // the selected row inverted (Jeff 2026-11-07, for the column a '>' took) and the one
        // being listened to boxed: both from the blank pixel row above, unless that touches
        // the header or the rule
        let up = if k == 0 || ruled { 0 } else { 1 };
        let marked = boxed_hz != 0 && c.hz == boxed_hz;
        if sel {
            fill(lcd, y - up, 6 + up as u32, BinaryColor::On);
        }
        let ink = if sel { BinaryColor::Off } else { BinaryColor::On };
        glyph::text(lcd, 1, y, b.as_str(), true, !sel);
        if checked {
            const CHECK: [u8; 4] = [0x01, 0x02, 0x14, 0x08];
            for (r, &m) in CHECK.iter().enumerate() {
                for c in 0..5 {
                    if m & (0x10 >> c) != 0 {
                        Pixel(Point::new(c, y + 1 + r as i32), ink).draw(lcd).ok();
                    }
                }
            }
        }
        if bird {
            // a birdie or other spur: a gull in the grade column and the space after it
            // Jeff's pick "A" (2026-11-07): 5 wide, 2 high
            const GULL: [u8; 2] = [0x0a, 0x15];
            for (r, &m) in GULL.iter().enumerate() {
                for c in 0..5 {
                    if m & (0x10 >> c) != 0 {
                        Pixel(Point::new(1 + c, y + 1 + r as i32), ink).draw(lcd).ok();
                    }
                }
            }
        }
        // Jeff's split bar (2026-11-07): the loudest over as a 2 px bar, 5 dB a pixel
        // from -120 dBm, and under it a dotted line for the score (10 points = full),
        // in the columns the number took; the numbers are in the footer
        let w = ((c.hi as i32 + 120) / 5).clamp(0, BAR_W as i32) as u32;
        if w > 0 {
            lcd.fill_solid(&Rectangle::new(Point::new(BAR_X, y), Size::new(w, 2)), ink).ok();
        }
        let d = (c.score as i32 * BAR_W as i32 / 10).clamp(0, BAR_W as i32);
        for x in (0..d).step_by(2) {
            Pixel(Point::new(BAR_X + x, y + 3), ink).draw(lcd).ok();
        }
        let x = BAR_X + BAR_W as i32 + 3;
        if marked {
            // the row being listened to: an arrow pointing at it from the call column, which is
            // still empty then (Jeff 2026-11-07)
            const ARROW: [u16; 5] = [0x100, 0x300, 0x7fe, 0x300, 0x100];
            for (r, &m) in ARROW.iter().enumerate() {
                for c in 0..11 {
                    if m & (0x400 >> c) != 0 {
                        Pixel(Point::new(x + c, y + r as i32), ink).draw(lcd).ok();
                    }
                }
            }
        } else if c.call[0] != 0 {
            glyph::text(lcd, x, y, cstr(&c.call), true, !sel);
        } else if c.flags & EX_F_DMR != 0 {
            // DMR heard, no ID yet (Jeff 2026-11-08: detect DMR and mute it)
            glyph::text(lcd, x, y, "DMR", true, !sel);
        }
    }
    // the scroll bar (Jeff 2026-11-14): rows 11 .. 55, the list's with the rule's 2 px
    glyph::scroll_bar(lcd, 11, 45, top, ROWS as u32, unsafe { ffi::ex_view_n(&t.ex) });
    if sel_p.is_null() {
        None
    } else {
        Some(unsafe { &mut *sel_p })
    }
}

/// the header's state icons (Jeff 2026-11-07), on the lit bar: half-lidded eyes (his pick "D")
/// that look the other way each time a sweep of the band set ends (Jeff 22:38: a visual of how
/// fast the scan is going)
fn eyes<D>(lcd: &mut D, right: bool)
where
    D: DrawTarget<Color = BinaryColor>,
{
    // the white of one 7-wide eye under its lid, rows 3-8, a bit per column (bit 6 the left one)
    const WHITE: [u8; 6] = [0x7f, 0x7f, 0x7f, 0x7f, 0x3e, 0x1c];
    for e in [1, 9] {
        for (r, &m) in WHITE.iter().enumerate() {
            for c in 0..7 {
                if m & (0x40 >> c) != 0 {
                    Pixel(Point::new(e + c, 3 + r as i32), BinaryColor::Off).draw(lcd).ok();
                }
            }
        }
        fill_box(lcd, e + if right { 3 } else { 1 }, 3, 3, 3, BinaryColor::On);
    }
}

/// dit-dah, then dah-dit, while listening for a CW ID
fn ditdah<D>(lcd: &mut D, second: bool)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let (a, b): (u32, u32) = if second { (6, 3) } else { (3, 6) };
    fill_box(lcd, 1, 4, a, 2, BinaryColor::Off);
    fill_box(lcd, 2 + a as i32, 4, b, 2, BinaryColor::Off);
}

/// a 9 x 9 watch face at the header's right end: the time left of the listen as a wedge that
/// empties clockwise from 12 (used = ms of max_ms)
fn watch<D>(lcd: &mut D, used: u32, max_ms: u32)
where
    D: DrawTarget<Color = BinaryColor>,
{
    // each pixel's angle from 12 o'clock in 250ths of a turn; 254 the rim, 255 outside
    const A: [u8; 81] = [
        255, 255, 254, 254, 254, 254, 254, 255, 255, 255, 254, 254, 237, 0, 12, 254, 254, 255, 254, 254, 218, 231,
        0, 18, 31, 254, 254, 254, 200, 205, 218, 0, 31, 44, 49, 254, 254, 187, 187, 187, 0, 62, 62, 62, 254, 254,
        174, 169, 156, 125, 93, 80, 75, 254, 254, 254, 156, 143, 125, 106, 93, 254, 254, 255, 254, 254, 137, 125,
        112, 254, 254, 255, 255, 255, 254, 254, 254, 254, 254, 255, 255,
    ];
    let gone = if max_ms == 0 { 250 } else { (used.min(max_ms) as u64 * 250 / max_ms as u64) as u8 };
    for (i, &a) in A.iter().enumerate() {
        if a == 254 || (a < 250 && a >= gone) {
            let p = Point::new(118 + (i % 9) as i32, (i / 9) as i32);
            Pixel(p, BinaryColor::Off).draw(lcd).ok();
        }
    }
}

/// the header bar with text dark on it at x
fn head_text<D>(lcd: &mut D, x: i32, t: &str)
where
    D: DrawTarget<Color = BinaryColor>,
{
    glyph::text(lcd, x, 1, t, false, false);
}

fn fill_box<D>(lcd: &mut D, x: i32, y: i32, w: u32, h: u32, c: BinaryColor)
where
    D: DrawTarget<Color = BinaryColor>,
{
    lcd.fill_solid(&Rectangle::new(Point::new(x, y), Size::new(w, h)), c).ok();
}

/// MHz with 3 decimals, as 146.940
fn mhz<const N: usize>(b: &mut TextBuf<N>, hz: u32) {
    let k = hz / 1000;
    out!(b, "{}.{:03}", k / 1000, k % 1000);
}

/// A row's tone: CTCSS in 0.1 Hz (`tone 136.5`), or a DCS code | 0x8000 (`DCS 023`; `i` after it
/// when it was sent inverted, 0x4000), as xd_tone gives it
fn tone_text(b: &mut dyn crate::out::Sink, tone: u16) {
    if tone & 0x8000 != 0 {
        out!(b, "DCS {:03o}{}", tone & 0x1FF, if tone & 0x4000 != 0 { "i" } else { "" });
    } else {
        out!(b, "tone {}.{}", tone / 10, tone % 10);
    }
}

fn off_text<const N: usize>(b: &mut TextBuf<N>, c: &Cand) {
    if c.flags & EX_F_SIMPLEX != 0 || c.off_hz == 0 {
        out!(b, "  S ");
    } else {
        let a = c.off_hz.unsigned_abs() / 100_000;
        out!(b, "{}{}.{}", if c.off_hz < 0 { '-' } else { '+' }, a / 10, a % 10);
    }
}

pub fn draw<D>(lcd: &mut D, app: &App)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let _ = app;
    lcd.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();
    let t = st();
    let mut b: TextBuf<24> = TextBuf::new();
    // the top line: a message for 2.5 s, else the view's own
    let msg = t.msg_len != 0;
    if msg {
        out!(b, "{}", crate::out::ascii(&t.msg[..t.msg_len as usize]));
    }
    match t.view {
        View::List => {
            if msg {
                line(lcd, 0, b.as_str());
            } else {
                // the eyes, then the band set and how many it has found (Jeff 2026-11-07: the
                // sweep time doesn't matter to a person)
                fill(lcd, 0, 10, BinaryColor::On);
                eyes(lcd, passes() % 2 == 1);
                out!(b, "{}  {} found", BANDS[t.band as usize].0, unsafe { ffi::ex_view_n(&t.ex) });
                head_text(lcd, 19, b.as_str());
            }
            let sel_c = list_rows(lcd, t, 0);
            let mut b: TextBuf<32> = TextBuf::new();
            if let Some(c) = sel_c {
                if c.flags & EX_F_SPUR != 0 {
                    out!(b, "{} dBm  birdie, not a station", c.hi);
                } else {
                    if c.tone != 0 {
                        out!(b, "{} dBm  score {}  ", c.hi, c.score);
                        tone_text(&mut b, c.tone);
                    } else {
                        out!(b, "{} dBm  score {}  {} overs", c.hi, c.score, c.overs);
                    }
                }
            } else {
                out!(b, "listening...");
            }
            line(lcd, FOOT, b.as_str());
        }
        View::Try => {
            if !msg {
                out!(b, "TRY ");
                mhz(&mut b, t.hz);
                if t.off != 0 {
                    let a = t.off.unsigned_abs() / 100_000;
                    out!(b, " {}{}.{}", if t.off < 0 { '-' } else { '+' }, a / 10, a % 10);
                }
            }
            line(lcd, 0, b.as_str());
            let mut b: TextBuf<24> = TextBuf::new();
            out!(b, "now {} dBm", t.level);
            line(lcd, 1, b.as_str());
            let mut i = 0;
            while let Some(c) = cand(i) {
                if c.hz == t.hz {
                    let mut b: TextBuf<24> = TextBuf::new();
                    out!(b, "{} sc{} n{} {}s", c.grade as char, c.score, c.overs, c.on_ms / 1000);
                    line(lcd, 2, b.as_str());
                    let mut b: TextBuf<24> = TextBuf::new();
                    if c.call[0] != 0 {
                        out!(b, "{}", cstr(&c.call));
                    } else {
                        out!(b, "no call yet");
                    }
                    if c.flags & EX_F_OFF_HEARD != 0 {
                        out!(b, " in heard");
                    }
                    line(lcd, 3, b.as_str());
                    if c.tone != 0 {
                        let mut b: TextBuf<24> = TextBuf::new();
                        tone_text(&mut b, c.tone);
                        line(lcd, 4, b.as_str());
                    }
                    break;
                }
                i += 1;
            }
            line(lcd, FOOT, "EXIT back 0 ID 5 save");
        }
        View::Listen => {
            let mut info: XlInfo = unsafe { core::mem::zeroed() };
            unsafe { ffi::xl_info(t.xl, &mut info) };
            if msg {
                line(lcd, 0, b.as_str());
            } else {
                // dit-dah, the frequency, and a watch face for the time left
                fill(lcd, 0, 10, BinaryColor::On);
                ditdah(lcd, now_ms() / 1000 % 2 == 1);
                mhz(&mut b, t.hz);
                if t.dmr_now {
                    out!(b, " DMR");
                }
                head_text(lcd, 15, b.as_str());
                watch(lcd, info.ms, if t.auto_now { t.auto_max_s } else { LISTEN_S } * 1000);
            }
            if t.auto_now {
                // the automatic listen keeps the list on screen, an arrow at its row
                let _ = list_rows(lcd, t, t.hz);
                line(lcd, FOOT, "listening ^v scroll  keys stop");
                return;
            }
            let mut b: TextBuf<24> = TextBuf::new();
            out!(b, "tone {} heard {}", if info.keyed != 0 { "ON " } else { "off" }, info.msgs);
            line(lcd, 1, b.as_str());
            line(lcd, 2, cstr(&info.text));
            if info.call[0] != 0 {
                let mut b: TextBuf<24> = TextBuf::new();
                out!(b, "{} x{}", cstr(&info.call), info.seen);
                line(lcd, 3, b.as_str());
            }
            line(lcd, FOOT, "any key stops");
        }
        View::Ask => {
            if !msg {
                out!(b, "NEW CALL ");
                mhz(&mut b, t.hz);
            }
            line(lcd, 0, b.as_str());
            let mut b: TextBuf<24> = TextBuf::new();
            out!(b, "heard {}", cstr(&t.heard));
            line(lcd, 1, b.as_str());
            let mut b: TextBuf<24> = TextBuf::new();
            out!(b, "saved {}", cstr(&t.ask.call));
            line(lcd, 2, b.as_str());
            line(lcd, 4, "5 write new call");
            line(lcd, FOOT, "EXIT keep saved");
        }
        View::Stations => {
            let r = &t.ask;
            if !msg {
                out!(b, "STATIONS {}/{}", if r.rx_hz != 0 { t.st_i as u16 + 1 } else { 0 }, t.rt.live);
            }
            line(lcd, 0, b.as_str());
            if r.rx_hz != 0 {
                let mut b: TextBuf<24> = TextBuf::new();
                mhz(&mut b, r.rx_hz);
                out!(b, " #{}", r.id);
                line(lcd, 1, b.as_str());
                line(lcd, 2, cstr(&r.call));
                line(lcd, 3, cstr(&r.name));
                if r.rx_tone != 0 {
                    let mut b: TextBuf<24> = TextBuf::new();
                    tone_text(&mut b, r.rx_tone);
                    line(lcd, 4, b.as_str());
                }
            } else {
                line(lcd, 2, "NO SAVED STATIONS");
            }
            line(lcd, FOOT, "8 edit 0 new 3x2 del");
        }
        View::Edit => {
            // Jeff's layout D (2026-11-14): the title in the 5x7, six fields in the 4x6 7 pixel
            // rows apart (the selected one inverted), the keys in the 4x6 at the foot
            let e = &t.ed;
            if !msg {
                out!(b, "EDIT ");
                if e.r.rx_hz != 0 {
                    mhz(&mut b, e.r.rx_hz);
                } else {
                    out!(b, "NEW");
                }
                if e.r.id != 0 {
                    out!(b, " #{}", e.r.id);
                }
                if unsafe { ffi::sted_changed(e) } != 0 {
                    out!(b, " *");
                }
            }
            line(lcd, 0, b.as_str());
            let top = (e.f as i32 - 2).clamp(0, ED_FIELDS - ED_ROWS);
            let mut buf = [0u8; 22];
            for i in 0..ED_ROWS {
                let f = top + i;
                let n = unsafe { ffi::sted_row(e, f, buf.as_mut_ptr()) }.clamp(0, 21) as usize;
                let y = ED_TOP + ED_PITCH * i;
                let sel = f == e.f as i32;
                if sel {
                    fill(lcd, y - 1, ED_PITCH as u32, BinaryColor::On);
                }
                glyph::text(lcd, 1, y, crate::out::ascii(&buf[..n]), true, !sel);
            }
            let (rows, n) = (ED_ROWS as u32, ED_FIELDS as u32);
            glyph::scroll_bar(lcd, ED_TOP - 1, rows * ED_PITCH as u32, top as u32, rows, n);
            let n = unsafe { ffi::sted_hint(e, buf.as_mut_ptr()) }.clamp(0, 21) as usize;
            line(lcd, FOOT, crate::out::ascii(&buf[..n]));
        }
    }
}
