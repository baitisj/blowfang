//! [S12] sotto 1200 receive and decode on the UV-21 Pro (docs/uv21-sotto1200.md): the K6 voice
//! pager's packets on the BK4829's FSK modem, its fixed-point decoder, and TMR1 CH4 PWM with DMA
//! to the speaker, all in MIT C (radios/uv21/sotto, linked by build.rs). It runs as a RAM module
//! does, with the whole of `hal::arena` while SURF is paused, and reaches the radio through the
//! same `UmApi` (every RF write through the driver's receive-only filter).
//!
//! The decoder's shape codebook is in the SPI flash at CB_SPI (written once by the cbspi RAM
//! modules, radios/uv21/mod/cbspi.c; Jeff, 2026-11-28: "Codebook in SPI is fine"). The library
//! asks for it at the K6's address (c/common/sp_cb.h SP_CB_ADDR); `sp_flash_read` here moves the
//! read to CB_SPI. This file only reads: no erase or program is linked (the library's install path
//! isn't built), and `norflash::READ_ONLY` stays as it is.

use crate::drivers::norflash::NorFlash;
use crate::hal::{arena, con};
use crate::umod::UmApi;
use at32f421_pac as pac;

/// where the codebook image is in this radio's SPI flash (blank in the uv21-flash-2e dump)
const CB_SPI: u32 = 0x1F_0000;
/// c/common/sp_cb.h SP_CB_ADDR: where the library reads it from
const SP_CB_ADDR: u32 = 0x10_0000;
/// the window cbspi.c writes
const CB_WINDOW: u32 = 0x8000;

extern "C" {
    fn uv21_sotto(api: *const UmApi, ram: *mut u8, ram_len: u32, hz: u32, listen_s: u32) -> i32;
    fn uv21_sotto_last(out: *mut u8) -> i32;
}

/// the last receive's summary line ("so last pk= g= f=/ late= pcm="), after SURF's alive line:
/// a check made with the cable out reads it once the cable is back
pub fn say_last() {
    let mut buf = [0u8; 72];
    if unsafe { uv21_sotto_last(buf.as_mut_ptr()) } != 0 {
        let n = buf.iter().position(|&b| b == 0).unwrap_or(0);
        con::puts(core::str::from_utf8(&buf[..n]).unwrap_or("so last ?"));
        con::nl();
    }
}

/// c/common/sp_cb.h: the library's flash reads. Inside the codebook's window they come from
/// CB_SPI; anything else reads as erased flash, so the library can't read the owner's data
#[no_mangle]
pub extern "C" fn sp_flash_read(addr: u32, buf: *mut u8, n: u32) {
    let out = unsafe { core::slice::from_raw_parts_mut(buf, n as usize) };
    let off = addr.wrapping_sub(SP_CB_ADDR);
    if addr < SP_CB_ADDR || off >= CB_WINDOW || n > CB_WINDOW - off {
        out.fill(0xFF);
        return;
    }
    let dp = unsafe { pac::Peripherals::steal() };
    NorFlash::new(&dp.gpioa, &dp.spi1).read_bytes(CB_SPI + off, out);
}

/// Listens on `hz` for up to `listen_s` seconds for a sotto 1200 message, plays it as it
/// streams in, and returns once it has played (the receiver's `so` lines on the console)
pub fn run(api: &UmApi, hz: u32, listen_s: u32) -> i32 {
    con::puts("so begin");
    con::kv_hex("hz", hz, 8);
    con::kv_hex("s", listen_s, 4);
    con::nl();
    let r = unsafe { uv21_sotto(api, arena::base(), arena::BYTES as u32, hz, listen_s) };
    con::puts("so ret");
    con::kv_hex("r", r as u32, 8);
    con::kv_hex("stk", crate::hal::stackprobe::spare(), 4); // the stack never touched since boot
    con::nl();
    r
}
