//! Crash and stuck-transmitter backstop (2026-11-08: a module overran its stack, the report
//! key-up after it hung, and the K6 sent a bare carrier on 440.850 for 16 minutes).
//!
//! The KD32F328 has no VTOR and the bootloader's vector table at flash 0x0800_0000 stays live:
//! its HardFault and every IRQ it doesn't forward are `b .`, so a fault hangs the radio with
//! the transmitter on if it was keyed. SYSCFG CFGR1 MEM_MODE = 3 maps SRAM at address 0
//! instead (sotto's vecprobe module, on the K6 2026-11-08 17:51Z: 0x2000_0000-0x2000_00BF
//! takes writes, the remap reads back and a deliberate fault reached its handler). `install`
//! copies the bootloader's 48 vectors there, points HardFault and TIM14 at Aura's and maps
//! SRAM at 0. Everything the bootloader forwarded (SysTick, USART1 via UserVectors) still goes
//! through its own code, as before.
//!
//! - HardFault: notes the pc and lr (at 0x2000_5FF0) and reboots at once (the bootloader
//!   and boot soft-reset the FD6818: transmitter off). The next boot prints the note.
//! - TIM14 is uptime's 10 kHz counter; it wraps every 6.5536 s and its update interrupt is the
//!   watchdog tick. `keyed` (radio enter_tx*) starts a key-up's clock with a limit, `limit`
//!   tightens it, `unkeyed` (radio enter_rx) stops it; a key-up past its limit reboots, at
//!   the first wrap after it (up to 6.6 s late). The wraps also make `ticks`, uptime that never
//!   wraps, which the core clock (module API level 8) runs on.
//!
//! If the remap doesn't read back, nothing is enabled and the radio runs as before.
//!
//! 2026-11-08: the first voice image with it (acd06ba) reset in a loop about 5 times a second at
//! boot, every time it was flashed. sotto's RAM modules then ruled out the copy (vectab), writes
//! to the table's RAM (loram), the table itself held 10 s with interrupts on (remapsoak) and any
//! cost in speed (remapspeed). This version, with the note out of the vector slots and lines straight to
//! USART1, boots and passed the crash and stuck key-up tests on the K6, and so did the same
//! without the lines (`txdog-quiet`): the note's old place in vector slots 45-47 is the one
//! difference left. The `txdog` feature turns it on (sotto's build: every image that transmits);
//! without it the key-up clock still runs (it costs nothing) but no table is installed.
//!
//! 2026-11-09: on the K6 the flash prefetch (FLASH_ACR PRFTBE) only works while main flash is at
//! address 0 (MEM_MODE 2, as the bootloader leaves it): MEM_MODE 3 clears it and it won't set
//! again there (sotto's pfprobe module: ACR 0x12 in mode 2, 0x02 in mode 3). With the table
//! mapped all the time, flash code ran slow enough that one FD6818 register read took 369 us
//! (fdtime) and EXPLORE's DMR detector never heard DMR. So the table is mapped only while it is
//! needed, "armed": from a key-up (`keyed*`) to `unkeyed` (a RAM module's key-up included). A RAM
//! module that doesn't key runs at rest, so it gets the prefetch too (EXPLORE's LISTEN as a module).
//! The rest of the time ("at rest") main flash is at 0 with the prefetch on, TIM14's interrupt
//! masked (the bootloader's table has no handler for it) and its wraps counted by `poll` (the
//! main loops, a module's ms/ticks calls, and every `ticks`). A fault at rest hangs the radio as
//! before txdog, with the transmitter off (any key-up arms it).
//!
//! The note lives at 0x2000_5FF0, at the top of SRAM, out of Aura's RAM and out of the
//! bootloader's reach. It isn't in the vector slots or anywhere else below 0x2000_1028, because
//! that is the bootloader's own RAM and it clears all of it at every reset.
#![cfg_attr(feature = "lean", allow(dead_code))]

use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};

const SRAM0: u32 = 0x2000_0000;
const BOOT_VEC: u32 = 0x0800_0000;
const APP_VEC: u32 = 0x0800_2000;
const NVEC: u32 = 48; // 16 system + 29 IRQs, rounded up; 45-47 unused
const V_HARDFAULT: u32 = 3;
const V_TIM14: u32 = 16 + 19;
const RCC_APB2ENR: u32 = 0x4002_1018;
const SYSCFG_CFGR1: u32 = 0x4001_0000;
const TIM14_DIER: u32 = 0x4000_200C;
const TIM14_SR: u32 = 0x4000_2010;
const TIM14_CNT: u32 = 0x4000_2024;
const NVIC_ISER: u32 = 0xE000_E100;
const NVIC_ICER: u32 = 0xE000_E180;
const NVIC_ICPR: u32 = 0xE000_E280;
const FLASH_ACR: u32 = 0x4002_2000;
const ACR_PRFTBE: u32 = 1 << 4;
/// why the table is mapped: a key-up
const ARM_TX: u32 = 1;
/// the last reboot's reason (magic, a, b), kept across the reset: 0x2000_5FF0, the top 16 bytes of
/// SRAM, which memory.x leaves out of Aura's RAM (the stack starts below them). The bootloader's
/// stack starts at 0x2000_4028 and grows down, so it never reaches them. Not the bootloader's own
/// RAM (0x2000_0000-0x2000_1027), which it clears at every reset: the note at 0x2000_0FF0 was
/// lost there (2026-11-09 00:05Z, EXPLORE image). Not `.uninit` either: that lands below the
/// bootloader's stack in the smaller images, and the EXPLORE image lost its note there too
/// (2026-11-08 23:21Z)
const NOTE: u32 = 0x2000_5FF0;
fn note_addr() -> u32 {
    NOTE
}
const NOTE_FAULT: u32 = 0xFA17_0001;
const NOTE_TX: u32 = 0xFA17_0002;
/// install the SRAM table (the `txdog` feature)
const REMAP: bool = cfg!(feature = "txdog");
const USART1_ISR: u32 = 0x4001_381C;
const USART1_TDR: u32 = 0x4001_3828;

/// Aura's PTT TOT goes up to 180 s; this is the cap for a key-up that sets no limit of its own
pub const DEFAULT_LIMIT_MS: u32 = 200_000;

static LIVE: AtomicBool = AtomicBool::new(false);
/// ARM_TX while the table is mapped; 0 at rest
static ARMED: AtomicU32 = AtomicU32::new(0);
static BOOT_CFGR1: AtomicU32 = AtomicU32::new(0);
/// what install found, for the boot report (the debug UART prints nothing in release builds)
static BOOT: AtomicU32 = AtomicU32::new(0);
static NOTE_A: AtomicU32 = AtomicU32::new(0);
static NOTE_B: AtomicU32 = AtomicU32::new(0);
static NOTE_K: AtomicU32 = AtomicU32::new(0);
static WRAPS: AtomicU32 = AtomicU32::new(0);
/// TIM14 ticks (100 us, unwrapped) at key-up, plus 1; 0 while not keyed
static KEYED_AT: AtomicU32 = AtomicU32::new(0);
static LIMIT: AtomicU32 = AtomicU32::new(DEFAULT_LIMIT_MS * 10);

fn rd(a: u32) -> u32 {
    unsafe { read_volatile(a as *const u32) }
}
fn wr(a: u32, v: u32) {
    unsafe { write_volatile(a as *mut u32, v) }
}

/// Writes SYSCFG CFGR1, the remap. On the K6 a remap with the prefetch on (MEM_MODE 2 to 3) turns
/// the prefetch off, and an instruction fetched from a new flash line right after it came back
/// wrong: on 35e982f, where txdog::arm's write was the last instruction of a 16-byte line, every
/// armed key-up hard-faulted two instructions on (pc 0800e064, 2026-11-10). Images where a few
/// more instructions of the same line followed the write ran fine. So the write starts a line
/// and a short wait (about 100 cycles) runs in the same line before anything is fetched past it.
/// Every CFGR1 write goes through here.
#[inline(never)]
fn remap(v: u32) {
    unsafe {
        core::arch::asm!(
            ".balign 16",
            "str r1, [r0]",
            "movs r2, #24",
            "2:",
            "subs r2, #1",
            "bne 2b",
            in("r0") SYSCFG_CFGR1,
            in("r1") v,
            out("r2") _,
            options(nostack),
        );
    }
}

/// What the boot before this one left: (kind, a, b), kind 1 = fault (pc, lr), 2 = key-up over
/// its limit (ms keyed, limit ms), 0 = nothing
pub struct Note(pub u32, pub u32, pub u32);

/// Install the vector table in SRAM and start the watchdog tick. Returns the last boot's note,
/// CFGR1 as found and whether the remap took.
#[cfg(not(feature = "lean"))]
pub fn install() -> (Note, u32, bool) {
    let n = note_addr();
    let note = match rd(n) {
        NOTE_FAULT => Note(1, rd(n + 4), rd(n + 8)),
        NOTE_TX => Note(2, rd(n + 4), rd(n + 8)),
        _ => Note(0, 0, 0),
    };
    for i in 0..3 {
        wr(n + 4 * i, 0);
    }
    NOTE_K.store(note.0, Ordering::Relaxed);
    NOTE_A.store(note.1, Ordering::Relaxed);
    NOTE_B.store(note.2, Ordering::Relaxed);
    wr(RCC_APB2ENR, rd(RCC_APB2ENR) | 1); // SYSCFG's clock
    let cfgr1 = rd(SYSCFG_CFGR1);
    BOOT.store(cfgr1, Ordering::Relaxed);
    if !REMAP || cfgr1 & 1 != 0 {
        return (note, cfgr1, false); // system flash at 0 (01): not the boot we know
    }
    cortex_m::interrupt::free(|_| {
        for i in 0..NVEC {
            wr(SRAM0 + 4 * i, rd(BOOT_VEC + 4 * i));
        }
        wr(SRAM0 + 4 * V_HARDFAULT, rd(APP_VEC + 4 * V_HARDFAULT));
        wr(SRAM0 + 4 * V_TIM14, tim14 as unsafe extern "C" fn() as usize as u32);
        remap((cfgr1 & !3) | 3); // SRAM at address 0
    });
    let ok = rd(4 * V_HARDFAULT) == rd(SRAM0 + 4 * V_HARDFAULT)
        && rd(4 * V_TIM14) == rd(SRAM0 + 4 * V_TIM14)
        && rd(4 * V_HARDFAULT) != rd(BOOT_VEC + 4 * V_HARDFAULT);
    if !ok {
        remap(cfgr1); // the bootloader's table again: leave everything as it was
        return (note, cfgr1, false);
    }
    BOOT_CFGR1.store(cfgr1, Ordering::Relaxed);
    LIVE.store(true, Ordering::Relaxed);
    wr(TIM14_SR, 0);
    wr(TIM14_DIER, rd(TIM14_DIER) | 1); // update flag (and, when armed, interrupt): one per wrap
    cortex_m::interrupt::free(|_| apply(false)); // at rest until the first key-up
    (note, cfgr1, true)
}

/// Armed (`armed`: MEM_MODE 3, TIM14's interrupt unmasked) or at rest (main flash at 0 again,
/// the prefetch on, TIM14 masked). Interrupts off.
#[cfg(not(feature = "lean"))]
fn apply(armed: bool) {
    if armed {
        count_wrap(); // a wrap seen at rest is counted here, not again by the interrupt
        remap((BOOT_CFGR1.load(Ordering::Relaxed) & !3) | 3);
        wr(NVIC_ICPR, 1 << 19);
        wr(NVIC_ISER, 1 << 19);
    } else {
        wr(NVIC_ICER, 1 << 19);
        remap(BOOT_CFGR1.load(Ordering::Relaxed));
        wr(FLASH_ACR, rd(FLASH_ACR) | ACR_PRFTBE);
    }
}

/// `why` (ARM_TX) starts (`on`) or ends; armed while any is on
#[cfg(not(feature = "lean"))]
fn arm(why: u32, on: bool) {
    if !LIVE.load(Ordering::Relaxed) {
        return;
    }
    cortex_m::interrupt::free(|_| {
        let was = ARMED.load(Ordering::Relaxed);
        let now = if on { was | why } else { was & !why };
        ARMED.store(now, Ordering::Relaxed);
        if (was == 0) != (now == 0) {
            apply(now != 0);
        }
    });
}

/// At rest: a wrap's update flag counted and cleared (the interrupt does it when armed).
/// Interrupts off.
#[cfg(not(feature = "lean"))]
#[inline(never)]
fn count_wrap() {
    if rd(TIM14_SR) & 1 != 0 {
        wr(TIM14_SR, 0);
        WRAPS.store(WRAPS.load(Ordering::Relaxed).wrapping_add(1), Ordering::Relaxed);
    }
}

/// The main loops and a module's ms/ticks calls call this (at least once a wrap, 6.5 s): at
/// rest, it counts TIM14's wraps.
/// Only the main context arms, so the state can't change between the test and the count
#[cfg(not(feature = "lean"))]
#[inline(never)]
pub fn poll() {
    if LIVE.load(Ordering::Relaxed) && ARMED.load(Ordering::Relaxed) == 0 {
        cortex_m::interrupt::free(|_| count_wrap());
    }
}

/// (wraps, CNT): uptime's TIM14 counter unwrapped
#[cfg(not(feature = "lean"))]
fn now2() -> (u32, u32) {
    poll();
    // WRAPS and CNT read so a wrap between them is seen: read WRAPS again after CNT
    loop {
        let w = WRAPS.load(Ordering::Relaxed);
        let c = rd(TIM14_CNT) & 0xFFFF;
        let pending = rd(TIM14_SR) & 1 != 0;
        if WRAPS.load(Ordering::Relaxed) == w {
            // a wrap whose interrupt hasn't run yet (interrupts off): count it
            let w = if pending && c < 0x8000 { w + 1 } else { w };
            return (w, c);
        }
    }
}

#[cfg(not(feature = "lean"))]
fn now() -> u32 {
    let (w, c) = now2();
    w.wrapping_mul(0x1_0000).wrapping_add(c)
}

/// Uptime for the core clock (module API level 8): (wraps since boot, CNT in 100 us ticks), so
/// it never wraps; None unless the TIM14 wrap interrupt runs (the `txdog` table installed)
#[cfg(not(feature = "lean"))]
pub fn ticks() -> Option<(u32, u32)> {
    if !LIVE.load(Ordering::Relaxed) {
        return None;
    }
    Some(now2())
}

/// The transmitter is about to key up: start the clock, `DEFAULT_LIMIT_MS` unless `limit`
/// tightens it
#[cfg(not(feature = "lean"))]
pub fn keyed() {
    keyed_for(DEFAULT_LIMIT_MS);
}

/// The same with a longer cap (an SSTV picture: PD290 takes 290 s)
#[cfg(not(feature = "lean"))]
pub fn keyed_for(ms: u32) {
    arm(ARM_TX, true);
    LIMIT.store(ms * 10, Ordering::Relaxed);
    KEYED_AT.store(now() | 1, Ordering::Relaxed);
}

/// Cap this key-up at `ms` from its start (only ever lower)
#[cfg(not(feature = "lean"))]
pub fn limit(ms: u32) {
    let l = LIMIT.load(Ordering::Relaxed);
    LIMIT.store((ms * 10).min(l), Ordering::Relaxed);
}

/// The transmitter is off
#[cfg(not(feature = "lean"))]
pub fn unkeyed() {
    KEYED_AT.store(0, Ordering::Relaxed);
    arm(ARM_TX, false);
}

/// TIM14 update: one per 6.5536 s
#[cfg(not(feature = "lean"))]
unsafe extern "C" fn tim14() {
    wr(TIM14_SR, 0);
    let w = WRAPS.load(Ordering::Relaxed).wrapping_add(1);
    WRAPS.store(w, Ordering::Relaxed);
    let at = KEYED_AT.load(Ordering::Relaxed);
    if at == 0 {
        return;
    }
    let on = w.wrapping_mul(0x1_0000).wrapping_sub(at);
    let lim = LIMIT.load(Ordering::Relaxed);
    if on > lim {
        let n = note_addr();
        wr(n + 4, on / 10);
        wr(n + 8, lim / 10);
        wr(n, NOTE_TX);
        raw(b"K6 TXDOG over limit", on / 10, lim / 10);
        reset();
    }
}

// The `lean` images (the codebook install, the -table fallbacks) never transmit and have no
// room: no SRAM table, no key-up clock.
#[cfg(feature = "lean")]
pub fn keyed() {}
#[cfg(feature = "lean")]
pub fn keyed_for(_ms: u32) {}
#[cfg(feature = "lean")]
pub fn limit(_ms: u32) {}
#[cfg(feature = "lean")]
pub fn unkeyed() {}
#[cfg(feature = "lean")]
pub fn poll() {}
#[cfg(feature = "lean")]
pub fn ticks() -> Option<(u32, u32)> {
    None
}

/// The boot report's line, once the serial report works: `K6 TXDOG on|OFF cfgr1=` and the
/// last boot's note
pub fn report() {
    let on = if LIVE.load(Ordering::Relaxed) { "on" } else { "OFF" };
    rl!("K6 TXDOG {} cfgr1={:08x}", on, BOOT.load(Ordering::Relaxed));
    let (a, b) = (NOTE_A.load(Ordering::Relaxed), NOTE_B.load(Ordering::Relaxed));
    match NOTE_K.load(Ordering::Relaxed) {
        1 => rl!("K6 FAULT last boot pc={:08x} lr={:08x}", a, b),
        2 => rl!("K6 TXDOG last boot: key-up ran {} ms, limit {} ms: rebooted", a, b),
        _ => {}
    }
}

/// From Aura's HardFault: note where and reboot at once. Printing first could fault again,
/// and a fault inside a fault locks the core up with the transmitter on.
pub fn fault(pc: u32, lr: u32) -> ! {
    let n = note_addr();
    wr(n + 4, pc);
    wr(n + 8, lr);
    wr(n, NOTE_FAULT);
    raw(b"K6 FAULT pc lr", pc, lr);
    reset()
}

/// A line straight to USART1, waiting on each byte (with a time-out): from a fault or before
/// the serial report's ring and interrupt run. `<what> xxxxxxxx xxxxxxxx`. Only in the `txdog`
/// image, where the table is installed.
fn raw(what: &[u8], a: u32, b: u32) {
    // txdog-quiet: the same image without these lines, to see whether their few ms at boot
    // are what keeps it out of acd06ba's loop (2026-11-08)
    if !REMAP || cfg!(feature = "txdog-quiet") {
        return;
    }
    fn put(c: u8) {
        let mut n = 0u32;
        while rd(USART1_ISR) & 0x80 == 0 && n < 100_000 {
            n += 1;
        }
        wr(USART1_TDR, c as u32);
    }
    fn hex(v: u32) {
        put(b' ');
        for i in (0..8).rev() {
            put(b"0123456789abcdef"[(v >> (4 * i)) as usize & 15]);
        }
    }
    put(b'\r');
    put(b'\n');
    for &c in what {
        put(c);
    }
    hex(a);
    hex(b);
    put(b'\r');
    put(b'\n');
    let mut n = 0u32;
    while rd(USART1_ISR) & 0x40 == 0 && n < 100_000 {
        n += 1; // TC: the last byte has left
    }
}

/// Every reboot goes through here: flash at address 0 again first, so the bootloader starts
/// from its own table even on a chip that kept MEM_MODE through a system reset
pub fn reset() -> ! {
    if LIVE.load(Ordering::Relaxed) {
        remap(BOOT_CFGR1.load(Ordering::Relaxed));
    }
    cortex_m::peripheral::SCB::sys_reset()
}
