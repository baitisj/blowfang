//! UV-21 Pro RAM modules over the cable (the operator, 2026-11-24: "The rest of the twiddling of
//! these registers can be done from a RAM module ... that, my good Claude, can be automated!").
//!
//! A module is position-independent Thumb code (radios/uv21/mod, built by modem/uv21mod.py) that
//! runs once in the 12 KB RAM arena (hal::arena) and returns. It reaches the radio only through
//! `UmApi`, whose register writes pass the RF driver's receive-only filter like every other write.
//! Nothing is kept: the next upload replaces it, and a power cycle clears it. The SPI flash isn't
//! touched.
//!
//! The upload is text lines on the K-plug serial line, each answered on a line of its own. SURF's
//! housekeeping reads the line (`service`); `um b` makes SURF leave, since SURF's state lives in
//! the same arena, and `run_session` takes the rest of the upload and the run. SURF starts again
//! afresh, on its last listening frequency, when the module returns or after 10 s with nothing on
//! the line (`um idle`).
//!
//! | line                    | does                                          | answer             |
//! |-------------------------|-----------------------------------------------|--------------------|
//! | `um ?`                  | asks                                          | `um abi=2 room=3000 stk=SSSS` |
//! | `um b LLLL`             | begins an image of LLLL bytes (hex)           | `um ok b`          |
//! | `um w OOOO HEX.. CCCC`  | bytes at offset OOOO, CRC-16/XMODEM CCCC      | `um ok OOOO`       |
//! | `um r CCCCCCCC`         | runs it, if whole with that CRC-32            | `um run`, the module's lines, `um stk=SSSS`, `um end rrrrrrrr` |
//!
//! stk= is the stack never touched since boot (hal::stackprobe), in bytes: the margin left.
//! Anything wrong answers `um bad ...` and changes nothing. A write must start where the last one
//! ended. Unsigned on purpose: the cable already reaches the bootloader, which takes whole images.
//!
//! The image: a 12-byte header (`UM21`, u16 ABI, u16 entry offset, u16 length, u16 0), then the
//! code. The entry is `int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len)`, with
//! `ram` the zeroed rest of the arena (radios/uv21/mod/uv21_mod.h).

use crate::hal::{arena, con, stackprobe, uart, uptime};

pub const ABI: u16 = 2;
pub const LEVEL: u16 = 1;
pub const ROOM: usize = arena::BYTES;
const HDR: usize = 12;
const MAGIC: [u8; 4] = *b"UM21";
const LINE_MAX: usize = 160;

/// radios/uv21/mod/uv21_mod.h's um_api_t (starts with c/common/radio_api.h's shared prefix)
#[repr(C)]
pub struct UmApi {
    pub abi: u16,
    pub level: u16,
    pub say: extern "C" fn(*const u8),
    pub ticks: extern "C" fn() -> u32,
    pub fd_read: extern "C" fn(u8) -> u16,
    pub fd_write: extern "C" fn(u8, u16) -> i32,
    pub delay_us: extern "C" fn(u32),
    pub tune: extern "C" fn(u32, i32),
    pub rx_hz: extern "C" fn() -> u32,
    pub ms: extern "C" fn() -> u32,
    pub front_end: extern "C" fn(i32) -> i32,
}

type Entry = extern "C" fn(*const UmApi, *mut u8, u32) -> i32;

/// an upload that goes quiet this long (ms) is dropped and SURF starts again
const SESSION_IDLE_MS: u32 = 10_000;

static mut LINE: [u8; LINE_MAX] = [0; LINE_MAX];
static mut LINE_N: usize = 0;
static mut WANT: usize = 0;
static mut GOT: usize = 0;
/// the length `um b` asked for while SURF ran: SURF leaves, then `run_session` begins the upload
static mut PENDING_WANT: usize = 0;

fn block() -> &'static mut [u8; ROOM] {
    arena::slice()
}

pub fn pending() -> bool {
    unsafe { PENDING_WANT != 0 }
}

/// Reads the serial line into LINE and acts on each whole line, until the line is empty or a line
/// asks to stop (`handle` returned true). `heard` is set when any byte came in.
fn drain(api: &UmApi, in_surf: bool, heard: &mut bool) -> bool {
    while let Some(b) = uart::take_byte() {
        *heard = true;
        let n = unsafe { LINE_N };
        if b == b'\n' || b == b'\r' {
            unsafe { LINE_N = 0 };
            if n > 0 && n <= LINE_MAX {
                // (read in place: a 160 B copy on the stack would sit under a running module)
                let line = unsafe { &(&*core::ptr::addr_of!(LINE))[..n] };
                if handle(api, line, in_surf) {
                    return true;
                }
            }
        } else if n < LINE_MAX {
            unsafe {
                (&mut *core::ptr::addr_of_mut!(LINE))[n] = b;
                LINE_N = n + 1;
            }
        } else {
            unsafe { LINE_N = LINE_MAX + 1 }; // too long: dropped at its end
        }
    }
    false
}

/// Drains the serial line while SURF runs (its housekeeping). Returns true when `um b` has asked
/// for the shared arena, so SURF leaves and `surf::run` hands it to `run_session`.
pub fn service(api: &UmApi) -> bool {
    drain(api, true, &mut false)
}

/// One upload and run with the whole of `hal::arena` (`surf::run` calls it once `uv21_surf` has
/// returned). Returns when the module has run, or after SESSION_IDLE_MS with nothing on the line.
pub fn run_session(api: &UmApi, check_power: fn()) {
    let want = unsafe { core::mem::replace(&mut *core::ptr::addr_of_mut!(PENDING_WANT), 0) };
    if want == 0 {
        return;
    }
    block().fill(0);
    unsafe {
        WANT = want;
        GOT = 0;
    }
    con::puts("um ok b");
    con::nl();

    let mut last = uptime::now32() / 10; // (100 us ticks -> ms)
    loop {
        check_power();
        let mut heard = false;
        if drain(api, false, &mut heard) {
            return;
        }
        let now = uptime::now32() / 10;
        if heard {
            last = now;
        } else if now.wrapping_sub(last) >= SESSION_IDLE_MS {
            unsafe {
                WANT = 0;
                GOT = 0;
            }
            con::puts("um idle");
            con::nl();
            return;
        } else {
            (api.delay_us)(200);
        }
    }
}

fn hexval(c: u8) -> Option<u32> {
    match c {
        b'0'..=b'9' => Some((c - b'0') as u32),
        b'a'..=b'f' => Some((c - b'a' + 10) as u32),
        b'A'..=b'F' => Some((c - b'A' + 10) as u32),
        _ => None,
    }
}

fn num(t: &[u8]) -> Option<u32> {
    if t.is_empty() || t.len() > 8 {
        return None;
    }
    t.iter().try_fold(0u32, |v, &c| Some(v << 4 | hexval(c)?))
}

use radio_common::wear_leveled::crc16_xmodem;

fn crc32(d: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &b in d {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 { crc >> 1 ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }
    !crc
}

fn bad(what: &str, v: u32) {
    con::puts("um bad ");
    con::puts(what);
    con::kv_hex("at", v, 4);
    con::nl();
}

#[inline(never)] // (its 80 B decode buffer stays out of `handle`'s frame, under a running module)
fn handle_write(a: Option<&[u8]>, b: Option<&[u8]>, c: Option<&[u8]>) {
    let (off, data, crc) = match (a.and_then(num), b, c.and_then(num)) {
        (Some(o), Some(d), Some(k)) => (o as usize, d, k as u16),
        _ => return bad("line", 0),
    };
    let n = data.len() / 2;
    let (want, got) = unsafe { (WANT, GOT) };
    if data.len() % 2 != 0 || n == 0 || off != got || off + n > want {
        return bad("off", got as u32);
    }
    let mut buf = [0u8; LINE_MAX / 2];
    for i in 0..n {
        match (hexval(data[2 * i]), hexval(data[2 * i + 1])) {
            (Some(h), Some(l)) => buf[i] = (h << 4 | l) as u8,
            _ => return bad("hex", off as u32),
        }
    }
    if crc16_xmodem(&buf[..n]) != crc {
        return bad("crc", off as u32);
    }
    block()[off..off + n].copy_from_slice(&buf[..n]);
    unsafe { GOT = off + n };
    con::puts("um ok ");
    con::hex(off as u32, 4);
    con::nl();
}

/// Acts on one line. True: in SURF, leave it (`um b`); in `run_session`, the module has run.
fn handle(api: &UmApi, line: &[u8], in_surf: bool) -> bool {
    let mut parts = line.split(|&c| c == b' ').filter(|p| !p.is_empty());
    if parts.next() != Some(b"um") {
        return false;
    }
    let cmd = parts.next().unwrap_or(b"");
    let a = parts.next();
    let b = parts.next();
    let c = parts.next();
    match cmd {
        b"?" => {
            con::puts("um");
            con::kv_hex("abi", ABI as u32, 1);
            con::kv_hex("room", ROOM as u32, 4);
            con::kv_hex("stk", stackprobe::spare(), 4);
            con::nl();
        }
        b"b" => match a.and_then(num) {
            Some(n) if (HDR as u32..=ROOM as u32).contains(&n) => {
                if in_surf {
                    unsafe { PENDING_WANT = n as usize };
                    return true;
                }
                block().fill(0);
                unsafe {
                    WANT = n as usize;
                    GOT = 0;
                }
                con::puts("um ok b");
                con::nl();
            }
            _ => bad("len", 0),
        },
        b"w" => handle_write(a, b, c),
        b"r" => {
            let (want, got) = unsafe { (WANT, GOT) };
            let img = &block()[..want];
            if want == 0 || got != want {
                bad("short", got as u32);
                return false;
            }
            if a.and_then(num) != Some(crc32(img)) {
                bad("crc32", want as u32);
                return false;
            }
            let abi = u16::from_le_bytes([img[4], img[5]]);
            let entry = u16::from_le_bytes([img[6], img[7]]) as usize;
            let len = u16::from_le_bytes([img[8], img[9]]) as usize;
            if img[..4] != MAGIC || abi != ABI || len != want || !(HDR..want).contains(&entry) {
                bad("header", abi as u32);
                return false;
            }
            let ram_at = (want + 7) & !7;
            let base = block().as_mut_ptr();
            con::puts("um run");
            con::nl();
            cortex_m::asm::dsb();
            cortex_m::asm::isb();
            // SAFETY: the image is whole, CRC-checked and says its entry is inside it; a module is
            // code from the cable, which can already reflash the radio.
            let f: Entry = unsafe { core::mem::transmute((base as usize + entry) | 1) };
            let r = f(api, unsafe { base.add(ram_at) }, (ROOM - ram_at) as u32);
            unsafe { WANT = 0 }; // (run once: upload again to run again)
            con::puts("um"); // (its own line: hosts read `um end`'s number to the line's end)
            con::kv_hex("stk", stackprobe::spare(), 4);
            con::nl();
            con::puts("um end ");
            con::hex(r as u32, 8);
            con::nl();
            return true;
        }
        _ => bad("cmd", 0),
    }
    false
}
