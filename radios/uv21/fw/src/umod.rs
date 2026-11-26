//! UV-21 Pro RAM modules over the cable (the operator, 2026-11-24: "The rest of the twiddling of
//! these registers can be done from a RAM module ... that, my good Claude, can be automated!").
//!
//! A module is position-independent Thumb code (radios/uv21/mod, built by modem/uv21mod.py) that
//! runs once in a 4 KB block of RAM and returns. It reaches the radio only through `UmApi`, whose
//! register writes pass the RF driver's receive-only filter like every other write. Nothing is
//! kept: the next upload replaces it, and a power cycle clears it. The SPI flash isn't touched.
//!
//! The upload is text lines on the K-plug serial line, while SURF runs (its housekeeping calls
//! `service`), each answered on a line of its own:
//!
//! | line                    | does                                          | answer             |
//! |-------------------------|-----------------------------------------------|--------------------|
//! | `um ?`                  | asks                                          | `um abi=1 room=2000` |
//! | `um b LLLL`             | begins an image of LLLL bytes (hex)           | `um ok b`          |
//! | `um w OOOO HEX.. CCCC`  | bytes at offset OOOO, CRC-16/XMODEM CCCC      | `um ok OOOO`       |
//! | `um r CCCCCCCC`         | runs it, if whole with that CRC-32            | `um run`, then the module's lines, `um end rrrrrrrr` |
//!
//! Anything wrong answers `um bad ...` and changes nothing. A write must start where the last one
//! ended. Unsigned on purpose: the cable already reaches the bootloader, which takes whole images.
//!
//! The image: a 12-byte header (`UM21`, u16 ABI, u16 entry offset, u16 length, u16 0), then the
//! code. The entry is `int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len)`, with
//! `ram` the zeroed rest of the block (radios/uv21/mod/uv21_mod.h).

use crate::hal::{con, uart};

pub const ABI: u16 = 1;
const ROOM: usize = 8192;
const HDR: usize = 12;
const MAGIC: [u8; 4] = *b"UM21";
const LINE_MAX: usize = 160;

/// radios/uv21/mod/uv21_mod.h's um_api_t
#[repr(C)]
pub struct UmApi {
    pub abi: u32,
    pub fd_read: extern "C" fn(u8) -> u16,
    pub fd_write: extern "C" fn(u8, u16),
    pub tune: extern "C" fn(u32, i32),
    pub delay_us: extern "C" fn(u32),
    pub ms: extern "C" fn() -> u32,
    pub say: extern "C" fn(*const u8),
    pub front_end: extern "C" fn(i32) -> i32,
}

type Entry = extern "C" fn(*const UmApi, *mut u8, u32) -> i32;

#[repr(C, align(8))]
struct Block([u8; ROOM]);

static mut BLOCK: Block = Block([0; ROOM]);
static mut LINE: [u8; LINE_MAX] = [0; LINE_MAX];
static mut LINE_N: usize = 0;
static mut WANT: usize = 0;
static mut GOT: usize = 0;

fn block() -> &'static mut [u8; ROOM] {
    unsafe { &mut (*core::ptr::addr_of_mut!(BLOCK)).0 }
}

/// Drains the serial line; a whole line is acted on. Called from SURF's housekeeping.
pub fn service(api: &UmApi) {
    while let Some(b) = uart::take_byte() {
        let n = unsafe { LINE_N };
        if b == b'\n' || b == b'\r' {
            unsafe { LINE_N = 0 };
            if n > 0 && n <= LINE_MAX {
                // LINE is only modified by service() (never in an IRQ) and not re-entered while
                // a module runs, so reading it directly avoids pinning 160 B on the module stack.
                let line = unsafe { &(&*core::ptr::addr_of!(LINE))[..n] };
                handle(api, line);
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

fn crc16_xmodem(d: &[u8]) -> u16 {
    let mut crc: u16 = 0;
    for &b in d {
        crc ^= (b as u16) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 { crc << 1 ^ 0x1021 } else { crc << 1 };
        }
    }
    crc
}

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

#[inline(never)] // keep the 80-byte decode buffer off the stack while a module runs (`b"r"`)
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

fn handle(api: &UmApi, line: &[u8]) {
    let mut parts = line.split(|&c| c == b' ').filter(|p| !p.is_empty());
    if parts.next() != Some(b"um") {
        return;
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
            con::nl();
        }
        b"b" => match a.and_then(num) {
            Some(n) if (HDR as u32..=ROOM as u32).contains(&n) => {
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
                return bad("short", got as u32);
            }
            if a.and_then(num) != Some(crc32(img)) {
                return bad("crc32", want as u32);
            }
            let abi = u16::from_le_bytes([img[4], img[5]]);
            let entry = u16::from_le_bytes([img[6], img[7]]) as usize;
            let len = u16::from_le_bytes([img[8], img[9]]) as usize;
            if img[..4] != MAGIC || abi != ABI || len != want || !(HDR..want).contains(&entry) {
                return bad("header", abi as u32);
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
            con::puts("um end ");
            con::hex(r as u32, 8);
            con::nl();
        }
        _ => bad("cmd", 0),
    }
}
