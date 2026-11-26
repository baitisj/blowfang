//! Unified 12 KB RAM arena shared by SURF (4,000 B while running) and RAM modules (all 12,288 B
//! while SURF is paused during a module upload/run).
//!
//! SURF and a RAM module never run at the same time: `um b` asks `surf::poll` to leave SURF,
//! `surf::run` hands the whole arena to `umod::run_session` once `uv21_surf` has returned (also
//! freeing `uv21_surf`'s stack frame), and `main`'s loop re-enters SURF at its last frequency
//! when the module finishes.

/// Total size of the shared arena in bytes (12 KB = 0x3000), 8-byte aligned.
pub const BYTES: usize = 12288;

#[repr(C, align(8))]
struct Arena([u8; BYTES]);

static mut ARENA: Arena = Arena([0; BYTES]);

/// Base pointer of the shared arena (8-byte aligned).
#[inline(always)]
pub fn base() -> *mut u8 {
    unsafe { (*core::ptr::addr_of_mut!(ARENA)).0.as_mut_ptr() }
}

/// Exclusive mutable view of the whole arena (only while SURF is not running).
#[inline(always)]
pub fn slice() -> &'static mut [u8; BYTES] {
    unsafe { &mut (*core::ptr::addr_of_mut!(ARENA)).0 }
}
