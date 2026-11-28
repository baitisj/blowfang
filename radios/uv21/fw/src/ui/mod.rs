//! UV-21 Pro: SURF draws its own screen (surf.rs); what's left here is the boot clear and the
//! 5x5 subpixel font SURF's text uses, and its 6x10 / 10x20 monospace fonts (mono.rs).
pub(crate) mod boot;
pub(crate) mod font55;
pub(crate) mod mono;
mod mono_glyphs;

pub(crate) const SCREEN_W: i32 = crate::drivers::st7735::WIDTH as i32;
pub(crate) const SCREEN_H: i32 = crate::drivers::st7735::HEIGHT as i32;
