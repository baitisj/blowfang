//! The bootloader's idle RAM, 0x2000_0100-0x2000_0FFF (memory.x `RAM0`, section `.ram0`).
//!
//! SRAM 0x2000_0000-0x2000_1027 lies below Aura's region. The bootloader uses it before it
//! starts Aura (its own data in 0x2000_0000-0x2000_046F) and clears all of it at every reset.
//! Once Aura runs, only two parts are live: hal::txdog's vector table (0x2000_0000-0x2000_00BF,
//! while it is mapped) and the bootloader's UserVectors (0x2000_1000-0x2000_1027, through which
//! SysTick and USART1 reach Aura). The rest is idle (sotto's ram0soak module, on the radio
//! 2026-11-19: every 256-byte block filled and held for a minute with interrupts running,
//! nothing changed, nothing hung; docs/k6-ram0.md).
//!
//! So the core's buffers that never have to survive a reset live there: Aura's `App` (main.rs
//! APP_SLOT), the serial rings (hal::uart), the module screen (app::pager MOD_SCREEN) and
//! EXPLORE's known-station list. Their 3.2 KB went to the shared block (hal::arena), which grew
//! from 11,776 to 14,976 bytes in every image that plays voice from SPI codebooks: every RAM
//! module gets that much more. Module API level 12 says so (msg_mem returns nothing: a
//! receiver keeps its message in its scratch).
//!
//! The section is NOLOAD: the startup code doesn't zero it. `clear` does, first thing in main,
//! so everything here starts out as it would in .bss.

extern "C" {
    static mut __sram0: u32;
    static mut __eram0: u32;
}

/// RAM0's first byte, as memory.x places it
pub const BASE: usize = 0x2000_0100;
/// RAM0's size: up to the bootloader's UserVectors at 0x2000_1000
pub const BYTES: usize = 0x0F00;

/// Zeroes the `.ram0` section. Call it before anything in it is used: first thing in main.
#[inline(always)]
pub fn clear() {
    let (mut p, end) = unsafe {
        (
            core::ptr::addr_of_mut!(__sram0),
            core::ptr::addr_of_mut!(__eram0) as *const u32,
        )
    };
    while (p as *const u32) < end {
        unsafe {
            core::ptr::write_volatile(p, 0);
            p = p.add(1);
        }
    }
}

/// `boottrace` images: a boot marker straight to USART1, polled with a time-out, as hal::txdog's
/// raw lines: `BT <n> <a> <b>` in hex. Without the feature it is nothing.
#[inline(never)]
pub fn bt(n: u32, a: u32, b: u32) {
    if !cfg!(feature = "boottrace") {
        return;
    }
    const ISR: usize = 0x4001_381C;
    const TDR: usize = 0x4001_3828;
    fn put(c: u8) {
        let mut k = 0u32;
        while unsafe { core::ptr::read_volatile(ISR as *const u32) } & 0x80 == 0 && k < 100_000 {
            k += 1;
        }
        unsafe { core::ptr::write_volatile(TDR as *mut u32, c as u32) };
    }
    fn hex(v: u32) {
        put(b' ');
        for i in (0..8).rev() {
            put(b"0123456789abcdef"[(v >> (4 * i)) as usize & 15]);
        }
    }
    for &c in b"\r\nK6 BT" {
        put(c);
    }
    hex(n);
    hex(a);
    hex(b);
    put(b'\r');
    put(b'\n');
}

/// What the bootloader left when it started Aura, read before `clear` (boottrace images):
/// printed by `snap_print` once the UART is up
#[cfg(feature = "boottrace")]
pub struct Snap {
    pub w: [u32; 24],
}

#[cfg(feature = "boottrace")]
fn rd(a: usize) -> u32 {
    unsafe { core::ptr::read_volatile(a as *const u32) }
}

/// boottrace: the bootloader's hand-over state, read first thing in main (nothing written).
/// w[0] DMA channels enabled (bit n = channel n+1), w[1] channels whose memory address points
/// into 0x2000_0000-0x2000_0FFF, w[2] USART1 CR1, w[3] USART1 CR3, w[4] NVIC ISER, w[5] SysTick
/// CSR, w[6] RCC CSR (reset flags), w[7] MSP, w[8] non-zero words in RAM0, w[9] the first one's
/// address, w[10] SYSCFG CFGR1, w[11] PRIMASK, w[12..24] the words at 0x2000_0440-0x2000_046F
/// (the top of the bootloader's own RAM, above its 1 KB RX buffer)
#[cfg(feature = "boottrace")]
#[inline(always)]
pub fn snap() -> Snap {
    let mut w = [0u32; 24];
    for ch in 0..7usize {
        let ccr = rd(0x4002_0008 + 20 * ch);
        let cmar = rd(0x4002_0014 + 20 * ch);
        if ccr & 1 != 0 {
            w[0] |= 1 << ch;
        }
        if (0x2000_0000..0x2000_1000).contains(&cmar) {
            w[1] |= 1 << ch;
        }
    }
    w[2] = rd(0x4001_3800);
    w[3] = rd(0x4001_3808);
    w[4] = rd(0xE000_E100);
    w[5] = rd(0xE000_E010);
    w[6] = rd(0x4002_1024);
    w[7] = sp();
    let mut a = BASE;
    while a < BASE + BYTES {
        if rd(a) != 0 {
            if w[8] == 0 {
                w[9] = a as u32;
            }
            w[8] += 1;
        }
        a += 4;
    }
    w[10] = rd(0x4001_0000);
    w[11] = cortex_m::register::primask::read().is_inactive() as u32;
    for i in 0..12 {
        w[12 + i] = rd(0x2000_0440 + 4 * i);
    }
    Snap { w }
}

#[cfg(feature = "boottrace")]
pub fn snap_print(s: &Snap) {
    for i in 0..12u32 {
        bt(0x10 + i, s.w[2 * i as usize], s.w[2 * i as usize + 1]);
    }
}

/// boottrace: the independent watchdog as a net. A hang anywhere resets the K6 within about
/// 13 s (LSI ~40 kHz / 128, reload 4095), so the bootloader's 200 ms window opens again and
/// `k6_update.py --manual` can put an image back with nobody at the radio. `kick` runs in
/// hal::uptime::now, which every loop of Aura's reads.
#[cfg(feature = "boottrace")]
pub fn dog_start() {
    const KR: usize = 0x4000_3000;
    unsafe {
        core::ptr::write_volatile(KR as *mut u32, 0xCCCC);
        core::ptr::write_volatile(KR as *mut u32, 0x5555);
        core::ptr::write_volatile((KR + 4) as *mut u32, 5); // PR: /128
        core::ptr::write_volatile((KR + 8) as *mut u32, 0xFFF);
        core::ptr::write_volatile(KR as *mut u32, 0xAAAA);
    }
}

#[inline(always)]
pub fn dog_kick() {
    #[cfg(feature = "boottrace")]
    unsafe {
        core::ptr::write_volatile(0x4000_3000 as *mut u32, 0xAAAA)
    };
}

/// The stack pointer, for the boot markers
#[inline(always)]
pub fn sp() -> u32 {
    cortex_m::register::msp::read()
}

/// Bytes of RAM0 in use (the `.ram0` section)
pub fn used() -> usize {
    unsafe { core::ptr::addr_of!(__eram0) as usize - core::ptr::addr_of!(__sram0) as usize }
}
