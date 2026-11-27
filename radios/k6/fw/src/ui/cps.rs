//! Static "programming in progress" screen shown for the duration of a CPS
//! write session (the session loop takes over the whole MCU, so this is
//! drawn once on entry and never redrawn).

use embedded_graphics::draw_target::DrawTarget;
#[cfg(not(feature = "pager-only"))]
use embedded_graphics::mono_font::{ascii::FONT_6X10, MonoTextStyle};
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;
use embedded_graphics::primitives::Rectangle;
#[cfg(not(feature = "pager-only"))]
use embedded_graphics::text::Text;

pub fn draw_programming<D>(target: &mut D)
where
    D: DrawTarget<Color = BinaryColor>,
{
    target.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();

    // the pager-only images: the stock 5x7 font from the SPI flash (ui::glyph), in 6x10's cells
    #[cfg(feature = "pager-only")]
    draw_lines(target, "AURA CPS", "PROGRAMMING...");
    #[cfg(not(feature = "pager-only"))]
    {
        let style = MonoTextStyle::new(&FONT_6X10, BinaryColor::On);
        Text::new("AURA CPS", Point::new(20, 26), style)
            .draw(target)
            .ok();
        Text::new("PROGRAMMING...", Point::new(8, 40), style)
            .draw(target)
            .ok();
    }
}

/// Two lines on a blank screen in the stock 5x7 font (ui::glyph): the CPS dump session's
/// screen, and the cable session's (cps::run_cable_session, `BLOWFANG` / `CABLE LOAD...`)
#[cfg(feature = "pager-only")]
pub fn draw_lines<D>(target: &mut D, a: &str, b: &str)
where
    D: DrawTarget<Color = BinaryColor>,
{
    target.fill_solid(&Rectangle::new(Point::new(0, 0), Size::new(128, 64)), BinaryColor::Off).ok();
    super::glyph::text(target, 20, 26 - 6, a, false, true);
    super::glyph::text(target, 8, 40 - 6, b, false, true);
}
