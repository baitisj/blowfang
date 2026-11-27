//! One block of RAM shared by the things that never run at the same time.
//!
//! A voice message needs the message store, the PWM audio ring and the decoder's working
//! memory, but only while it is received and played. A RAM module, EXPLORE and the coded 4FSK
//! session need memory only while no message plays. So all three live here side by side, in
//! this order, and a RAM module gets the whole block (its code, then its scratch), instead of
//! only the 4 KB store as before 2026-11-08:
//!
//! ```text
//!   0             STORE_BYTES        + RING_BYTES                         ARENA_BYTES
//!   | message store | PWM audio ring  | decoder pool (sp_dec_pool) ...      |
//!   | module header, code, tag, scratch ................ | telemetry text  |
//! ```
//!
//! The pager sets the store up again (sp_init) after anything that borrowed the block, and
//! sp_init forgets the decoder that was in the pool; a playback fills the ring before it
//! starts. The telemetry text of a module (the last TLM_BYTES) stays there after the module
//! returns, for the upload report, until a playback's decoder overwrites it.
//!
//! The screen's 1 KB page buffer borrows the ring's first bytes too (`lcd`, in the images built
//! with `shared_lcd`: device::display), only while the screen is drawn, which never happens
//! while a message plays or streams. A running module has the ring, so its screen goes out a
//! page (128 bytes of stack) at a time. Before, the buffer sat in main's stack frame for good.
//!
//! 2026-11-19: 14,976 bytes, 3,200 more. The core's buffers that never need to survive a reset
//! (Aura's App 2,164, the serial rings 864, the module screen 196: 3,224 bytes in every image)
//! moved to the bootloader's idle RAM below UserVectors (hal::ram0), and the block took their
//! place in Aura's region; the stack keeps what it had (24 bytes more). Module API level 12.
//!
//! Later on 2026-11-19: 15,408 bytes, 432 more (module API level 13; Jeff: move EXPLORE's state
//! into the reclaimed RAM). hal::ram0 also took sotto's C statics (libsotto_k6.a's .bss, 444 B
//! in the hiss image, memory.x.in and build.rs) in the images with no EXPLORE, and EXPLORE's
//! state (600 B, for 256 B of its KNOWN list moved back out) in the voice + EXPLORE image. The
//! hiss image's running stack still limits it: 268 bytes to spare (256 before), the voice +
//! EXPLORE image 552.
//!
//! 2026-11-26: 16,688 bytes, 1,280 more (module API level 16). The pager-only build leaves 13
//! of Aura's modes out of `App` (APP_SLOT 2,164 -> 1,236 B in hal::ram0), and the room went to
//! libsotto_k6.a's .bss, EXPLORE's KNOWN list and the pager's zeroed statics. main's one-time
//! set-up moved into boot_init_app (main's frame 880 -> 160 B in the hiss image), and the hiss
//! image builds with FX_SYNTH_STATE, so synth's 2 KB arrays sit in the decoder pool (4,280 B)
//! instead of on the stack (the M0 model: the same cycles, the same PCM). On the radio at
//! 16,432 (d3ac7ad): one clean boot, a FUSOTTO RX call returned with stack_free=772 B (408 on
//! the level 13-15 images) and played with ram_free=1,468. So 256 more: 16,688 bytes, which
//! leaves the stack about 516 B, still above what the earlier images ran with.
//!
//! Size until then: the same 11,776 bytes in every image that reads its codebooks from SPI flash and plays
//! voice, so a module that fits one fits all. In the hiss band image that is store 4,096 + ring
//! 2,560 + decoder 2,240 (sotto_pager.c SP_DEC_MAX 2,232) + the 864 bytes the pager-only build
//! takes out of Aura's `App` (MAX_SATELLITES 1, the spectrum's MAX_BINS 1: screens it never
//! shows) + the 2,016 bytes main's frame gave up with the page buffer (2,952 -> 928); the 8 kHz
//! images (ring 1,280, decoder at most 1,504) give the rest of the block to modules, out of stack
//! they had to spare. The running stack is what limits it. Its deepest call, from main's frame
//! through the coded 4FSK session (main 928 + if_session 496 + the receiver's `run` 2,736 and
//! the frames around them: 4,588 bytes; a playback's decoder, with synth's 2,240, is 4.2 KB),
//! keeps the 244 bytes to spare it had before (`build.sh` prints both). The EXPLORE image has
//! no decoder, so its block is the store and the ring (5,376 bytes). So is the lean images'
//! (codebook install, codebooks in MCU flash): they have no flash for the pool's code, and their
//! library keeps the decoder's memory in its own static. Both keep the page buffer in main.

/// the message store: sp_init's buffer
pub const STORE_BYTES: usize = 4096;
/// the PWM audio ring (hal::pwm_audio)
pub const RING_BYTES: usize = super::pwm_audio::RING_BYTES;
#[cfg(not(small_arena))]
pub const ARENA_BYTES: usize = 16688;
#[cfg(small_arena)]
pub const ARENA_BYTES: usize = STORE_BYTES + RING_BYTES;
/// the decoder's pool: what is left after the store and the ring
pub const DEC_OFF: usize = STORE_BYTES + RING_BYTES;
pub const DEC_BYTES: usize = ARENA_BYTES - DEC_OFF;
/// the module telemetry text at the top: its header and up to 12 parts of 57 bytes
pub const TLM_BYTES: usize = 696;

const _: () = assert!(ARENA_BYTES % 4 == 0 && DEC_OFF % 4 == 0);
const _: () = assert!(ARENA_BYTES >= DEC_OFF);
#[cfg(feature = "hiss")]
const _: () = assert!(DEC_BYTES >= 4280); // sotto_pager.c's SP_DEC_MAX with SP_HISS + FX_SYNTH_STATE
#[cfg(not(small_arena))]
const _: () = assert!(ARENA_BYTES - TLM_BYTES >= DEC_OFF); // telemetry clear of the ring

// k6_arena: tools/k6_pages.py links EXPLORE's code pages at its decoder pool (8 kHz voice +
// EXPLORE image)
#[cfg_attr(feature = "voice-explore", export_name = "k6_arena")]
static mut ARENA: [u32; ARENA_BYTES / 4] = [0; ARENA_BYTES / 4];

/// The block's first byte (word aligned): the message store, and a module's header
#[inline(always)]
pub fn base() -> *mut u8 {
    core::ptr::addr_of_mut!(ARENA) as *mut u8
}

/// One past the block's last byte
#[inline(always)]
pub fn end() -> usize {
    base() as usize + ARENA_BYTES
}

/// The PWM ring (u16 samples, word aligned)
#[inline(always)]
pub fn ring() -> *mut u16 {
    unsafe { base().add(STORE_BYTES) as *mut u16 }
}

/// The screen's page buffer while it is drawn (device::display, `shared_lcd` images): the
/// ring's first bytes, as nothing plays then
#[cfg(shared_lcd)]
#[inline(always)]
pub fn lcd() -> *mut u8 {
    ring() as *mut u8
}

/// The decoder's pool
#[inline(always)]
pub fn dec() -> *mut u8 {
    unsafe { base().add(DEC_OFF) }
}

/// A module's telemetry text: the block's last TLM_BYTES
#[inline(always)]
pub fn tlm() -> *mut u8 {
    unsafe { base().add(ARENA_BYTES - TLM_BYTES) }
}
