//! Text without embedded-graphics' text code or its 6x10 font (Jeff 2026-11-10: "switching over
//! to the stock fonts -- save some space!"). The SC5260 has no character ROM, so Baofeng's own
//! firmware kept its fonts in the K6's SPI flash, and Aura's first-boot format leaves them there
//! (docs/k6-stock-firmware.md; the stockfont module found all of them on the bench radio,
//! byte for byte).
//!
//! - The 21-column lines use the stock 5x7 font, read a glyph at a time: 5 column bytes, bit 0
//!   the top row, rows 0-6, caps and digits on row 6, no descenders. In a 6-pixel cell, with
//!   the glyph's top one row below a FONT_6X10 cell's top, the caps land on the rows 6x10's took.
//! - The 32-column lines keep embedded-graphics' FONT_4X6 bitmap (288 bytes; the stock firmware
//!   has no 4x6), drawn here too.
//! - A module screen's title bar keeps embedded-graphics' FONT_6X10 (720 bytes of bitmap; Jeff
//!   2026-11-14: "keep the font in the title but move everything else to 5x7 and make that the
//!   default"), drawn here too, so no text code comes back with it.
//!
//! At power-on main checks the 5x7 font's CRC, and VOICE MSG's first entry reports it on the
//! serial cable (`K6 FONT 5x7 crc=07acb73c ok=1`); a radio whose SPI flash lacks the font draws
//! the 21-column lines in 4x6, in the same cells.
//! We use the font where Baofeng's firmware left it, and copy none of Baofeng's code.

use crate::drivers::norflash::NorFlash;
use embedded_graphics::image::GetPixel;
use embedded_graphics::mono_font::ascii::{FONT_4X6, FONT_6X10};
use embedded_graphics::pixelcolor::BinaryColor;
use embedded_graphics::prelude::*;

/// The stock 5x7 ASCII font: character c's glyph at F5X7 + 8 * (c - 0x20), 5 bytes used
const F5X7: u32 = 0x04_BFC0;
/// zlib's CRC-32 of its 95 glyphs (760 bytes), the same on the bench radio and in its pre-Aura
/// SPI dump (2026-11-10)
const F5X7_CRC: u32 = 0x07AC_B73C;

static mut NOR: *mut NorFlash<'static> = core::ptr::null_mut();
/// the CRC set_flash found: the 21-column lines use the stock font only if it is F5X7_CRC
static mut CRC: u32 = 0;

/// Read the glyphs through `n` (main's Storage at boot, then the App's). With `check`, test the
/// 5x7 font's CRC first; the 21-column lines use the font only if it matched.
pub fn set_flash(n: &mut NorFlash<'static>, check: bool) {
    unsafe { NOR = n as *mut _ };
    if !check {
        return;
    }
    let mut crc = !0u32;
    let mut b = [0u8; 40];
    for k in 0..19 {
        n.read_bytes(F5X7 + 40 * k, &mut b);
        for &x in &b {
            crc ^= x as u32;
            for _ in 0..8 {
                crc = (crc >> 1) ^ (0xEDB8_8320 & (crc & 1).wrapping_neg());
            }
        }
    }
    unsafe { CRC = !crc };
}

fn stock() -> bool {
    unsafe { CRC == F5X7_CRC }
}

/// The serial line for VOICE MSG's first entry (main's own boot lines never reach the cable)
pub fn report() {
    let crc = unsafe { CRC };
    rl!("K6 FONT 5x7 crc={:08x} ok={}", crc, stock() as u8);
}

/// A glyph's columns (bit 0 the top row) and its cell's width: 6 for the 5x7, 4 for the 4x6.
/// 0x80 and up (a RAM module's own glyphs, ui::pager draws them) leave the cell empty
fn glyph(c: u8, small: bool, cols: &mut [u8; 5]) -> i32 {
    if c >= 0x80 {
        return if small { 4 } else { 6 };
    }
    let i = if (0x20..0x7F).contains(&c) { c - 0x20 } else { b'?' - 0x20 };
    if !small && stock() {
        unsafe { (*NOR).read_bytes(F5X7 + 8 * i as u32, cols) };
        return 6;
    }
    // FONT_4X6: 16 glyphs a row of its bitmap, each 4 x 6
    let (gx, gy) = ((i % 16) as i32 * 4, (i / 16) as i32 * 6);
    for (x, m) in cols.iter_mut().take(4).enumerate() {
        *m = 0;
        for y in 0..6 {
            if FONT_4X6.image.pixel(Point::new(gx + x as i32, gy + y)) == Some(BinaryColor::On) {
                *m |= 1 << y;
            }
        }
    }
    cols[4] = 0;
    if small {
        4
    } else {
        6
    }
}

/// `t` from column x, lit (`on`) or dark: the 5x7 with its top at `top` (FONT_6X10's baseline
/// less 6), or (`small`) the 4x6 with its cell's top at `top`. Only foreground pixels are drawn,
/// as embedded-graphics' MonoTextStyle without a background. A line outside the draw target's
/// rows (a band of 8 in the `banded` image, ui::Band) reads no glyphs.
pub fn text<D>(lcd: &mut D, x: i32, top: i32, t: &str, small: bool, on: bool)
where
    D: DrawTarget<Color = BinaryColor>,
{
    text_b(lcd, x, top, t.as_bytes(), small, on)
}

/// text's bytes (a module's line may hold its glyphs' 0x80 + k, which no &str holds)
pub fn text_b<D>(lcd: &mut D, x: i32, top: i32, t: &[u8], small: bool, on: bool)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let bb = lcd.bounding_box();
    if top + 7 <= bb.top_left.y || top >= bb.top_left.y + bb.size.height as i32 {
        return;
    }
    let ink = if on { BinaryColor::On } else { BinaryColor::Off };
    let mut x = x;
    for &c in t {
        if x >= 128 {
            break;
        }
        let mut cols = [0u8; 5];
        let w = glyph(c, small, &mut cols);
        let x0 = x;
        let px = cols.into_iter().enumerate().flat_map(move |(i, m)| {
            (0..7).filter(move |&r| m >> r & 1 != 0).map(move |r| Pixel(Point::new(x0 + i as i32, top + r), ink))
        });
        lcd.draw_iter(px).ok();
        x += w;
    }
}

/// The same 21-column line straight into one controller page (pixel rows y0 .. y0 + 7 in
/// `buf`), for the module screens drawn a page at a time (ui::pager::draw_mod_page)
pub fn text_page(buf: &mut [u8; 128], y0: i32, x: i32, top: i32, t: &[u8], on: bool) {
    let s = top - y0;
    if s <= -7 || s >= 8 {
        return;
    }
    let mut x = x;
    for &c in t {
        if x >= 128 {
            break;
        }
        let mut cols = [0u8; 5];
        let w = glyph(c, false, &mut cols);
        for (i, &m) in cols.iter().enumerate() {
            let xi = x + i as i32;
            if !(0..128).contains(&xi) {
                continue;
            }
            let m = if s >= 0 { (m as u16) << s } else { (m as u16) >> -s } as u8;
            let p = &mut buf[xi as usize];
            *p = if on { *p | m } else { *p & !m };
        }
        x += w;
    }
}

/// FONT_6X10's glyph for c: 6 columns of 10 rows, bit 0 the cell's top row (the baseline is row
/// 7). Bytes outside 0x20-0x7E show '?'
fn glyph_6x10(c: u8, cols: &mut [u16; 6]) {
    let i = if (0x20..0x7F).contains(&c) { c - 0x20 } else { b'?' - 0x20 };
    // 16 glyphs a row of its bitmap, each 6 x 10
    let (gx, gy) = ((i % 16) as i32 * 6, (i / 16) as i32 * 10);
    for (x, m) in cols.iter_mut().enumerate() {
        *m = 0;
        for y in 0..10 {
            if FONT_6X10.image.pixel(Point::new(gx + x as i32, gy + y)) == Some(BinaryColor::On) {
                *m |= 1 << y;
            }
        }
    }
}

/// A module screen's title in FONT_6X10, its cell's top row at `top` (baseline less 7), lit or
/// dark; 0x80 and up (the module's own glyphs) leave their 6-pixel cell empty
pub fn title<D>(lcd: &mut D, x: i32, top: i32, t: &[u8], on: bool)
where
    D: DrawTarget<Color = BinaryColor>,
{
    let ink = if on { BinaryColor::On } else { BinaryColor::Off };
    let mut x = x;
    for &c in t {
        if x >= 128 {
            break;
        }
        if c < 0x80 {
            let mut cols = [0u16; 6];
            glyph_6x10(c, &mut cols);
            let x0 = x;
            let px = cols.into_iter().enumerate().flat_map(move |(i, m)| {
                (0..10).filter(move |&r| m >> r & 1 != 0).map(move |r| Pixel(Point::new(x0 + i as i32, top + r), ink))
            });
            lcd.draw_iter(px).ok();
        }
        x += 6;
    }
}

/// title's line straight into one controller page (pixel rows y0 .. y0 + 7 in `buf`)
pub fn title_page(buf: &mut [u8; 128], y0: i32, x: i32, top: i32, t: &[u8], on: bool) {
    let s = top - y0;
    if s <= -10 || s >= 8 {
        return;
    }
    let mut x = x;
    for &c in t {
        if x >= 128 {
            break;
        }
        if c < 0x80 {
            let mut cols = [0u16; 6];
            glyph_6x10(c, &mut cols);
            for (i, &m) in cols.iter().enumerate() {
                let xi = x + i as i32;
                if !(0..128).contains(&xi) {
                    continue;
                }
                let m = if s >= 0 { (m as u32) << s } else { (m as u32) >> -s } as u8;
                let p = &mut buf[xi as usize];
                *p = if on { *p | m } else { *p & !m };
            }
        }
        x += 6;
    }
}

/// A list's scroll bar (Jeff 2026-11-14): 2 px wide at the right edge, rows y0 .. y0 + h, when
/// the list (n rows) is longer than the screen (vis rows from row top): the lit part is the rows
/// on screen, its length and place their share of the list and where they are in it. The two
/// columns are cleared first, so a selected row's inverted bar stops short of it (text ends by
/// x = 124 in EXPLORE, the editor and the module list).
pub fn scroll_bar<D>(lcd: &mut D, y0: i32, h: u32, top: u32, vis: u32, n: u32)
where
    D: DrawTarget<Color = BinaryColor>,
{
    use embedded_graphics::primitives::Rectangle;
    if n <= vis {
        return;
    }
    let th = (h * vis / n).max(3);
    let y = y0 + ((h - th) * top.min(n - vis) / (n - vis)) as i32;
    lcd.fill_solid(&Rectangle::new(Point::new(126, y0), Size::new(2, h)), BinaryColor::Off).ok();
    lcd.fill_solid(&Rectangle::new(Point::new(126, y), Size::new(2, th)), BinaryColor::On).ok();
}
