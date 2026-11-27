//! The LCD backlight's idle timeout in the pager images (blowfang, Jeff 2026-11-17: "the
//! backlight remains on the entire time, draining battery"). Aura's own timeout
//! (App::poll_backlight, the ABR setting) runs in its radio main loop, which the pager images
//! never reach: their loop lit the light at power-on and never put it out.
//!
//! The delay is Aura's ABR byte in its settings record (Settings::backlight_time), so the
//! saved value survives a reflash: 0 always on, 1-4 Aura's 5/10/15/20 s, 5-7 blowfang's
//! 30 s / 1 min / 2 min. The SETTINGS module (c/k6/mod/settings.c, module API level 10)
//! changes and saves it. Any key, a good packet, a message playing and transmitting light it
//! again; poll() puts it out when the time is up. Both are cheap (one timer read), so the
//! module calls that read the keys and draw the screen run them too, and a module that runs
//! for an hour (SURF) still times out and wakes on its keys.
//!
//! Time is counted in uptime's 100 us ticks, which wrap every 6.5 s: a gap between polls
//! longer than that (a blocking playback) only keeps the light on longer.
//!
//! The lean images (codebook install, MCU-flash table: tight on flash, used once or as a
//! fallback) keep the light on, as before: their calls are empty.
#[cfg(not(feature = "lean"))]
pub use imp::*;
#[cfg(feature = "lean")]
pub use stub::*;

#[cfg(feature = "lean")]
mod stub {
    pub fn init(_code: u8) {}
    pub fn code() -> u8 {
        0
    }
    pub fn touch() {}
    pub fn poll() {}
}

#[cfg(not(feature = "lean"))]
mod imp {
    use crate::hal::uptime;
    use core::ptr::addr_of_mut;

    /// Seconds for each setting: 0 = always on.
    pub const SECS: [u8; 8] = [0, 5, 10, 15, 20, 30, 60, 120];
    pub const MAX: u8 = 7;

    struct Light {
        code: u8,
        on: bool,
        /// 100 us ticks until it goes out
        left: u32,
        last: u16,
    }

    static mut L: Light = Light {
        code: 0,
        on: true,
        left: 0,
        last: 0,
    };

    fn st() -> &'static mut Light {
        unsafe { &mut *addr_of_mut!(L) }
    }

    fn pin(on: bool) {
        let gpiof = unsafe { &*kd32f328_pac::Gpiof::ptr() };
        crate::board::set_lcd_backlight(gpiof, on);
        st().on = on;
    }

    /// The saved setting at power-on (Aura's ABR byte; anything past MAX reads as MAX).
    pub fn init(code: u8) {
        st().code = code.min(MAX);
        touch();
    }

    pub fn code() -> u8 {
        st().code
    }

    /// A new setting, applied at once (the time starts again from now).
    pub fn set(code: u8) {
        st().code = code.min(MAX);
        touch();
    }

    /// Activity: the light on, and its time from now.
    pub fn touch() {
        let l = st();
        l.left = SECS[l.code as usize] as u32 * 10_000;
        l.last = uptime::now();
        if !l.on {
            pin(true);
        }
    }

    /// The light out once its time is up (always on with setting 0).
    pub fn poll() {
        let l = st();
        let now = uptime::now();
        l.left = l.left.saturating_sub(now.wrapping_sub(l.last) as u32);
        l.last = now;
        let want = l.code == 0 || l.left > 0;
        if want != l.on {
            pin(want);
        }
    }
}
