//! UV-21: SURF's font 1 (6x10) and font 2 (10x20, TUNE's digits), streamed straight to the panel
//! in one window a line, the same pixels embedded-graphics' FONT_6X10 / FONT_10X20 drew with
//! `Baseline::Top` and a background (the crate is gone to save flash). Font 1 has all of ASCII;
//! font 2 keeps only mono_glyphs::TALL_CHARS and draws anything else as font 1 doubled.
use super::mono_glyphs::{SMALL, TALL, TALL_CHARS, TALL_TOP};
use crate::drivers::st7735::St7735;

/// one pixel row `r` (0 at the top of the cell) of `c`'s glyph: bit `cw - 1` the leftmost column
fn row_bits(c: u8, tall: bool, r: i32) -> u16 {
    let small = |c: u8, r: i32| -> u16 {
        let i = if (0x20..0x80).contains(&c) { c - 0x20 } else { b'?' - 0x20 };
        ((SMALL[i as usize] >> (5 * r)) as u16 & 0x1f) << 1
    };
    if !tall {
        return small(c, r);
    }
    if let Some(i) = TALL_CHARS.iter().position(|&t| t == c) {
        let k = r - TALL_TOP;
        return if k >= 0 && (k as usize) < TALL[i].len() { TALL[i][k as usize] } else { 0 };
    }
    // font 1 doubled: 12 x 20, cut to the 10 columns (its sixth column is the gap)
    let s = small(c, r / 2);
    let mut v = 0u16;
    for col in 0..10 {
        v = v << 1 | (s >> (5 - col / 2)) & 1;
    }
    v
}

/// Draws `s` with its top-left corner at (x, y), every cell whole (so it overwrites what was
/// there), clipped to the screen; font 2 is 10x20, any other 6x10. Returns `s`'s width.
pub fn draw(lcd: &mut St7735, x: i32, y: i32, s: &str, fg: u16, bg: u16, font: i32) -> i32 {
    let tall = font == 2;
    let (cw, ch) = if tall { (10, 20) } else { (6, 10) };
    let bytes = s.as_bytes();
    let total_w = bytes.len() as i32 * cw;
    let x0 = x.max(0);
    let y0 = y.max(0);
    let x1 = (x + total_w).min(super::SCREEN_W);
    let y1 = (y + ch).min(super::SCREEN_H);
    if x1 <= x0 || y1 <= y0 {
        return total_w;
    }
    lcd.begin(x0 as u16, y0 as u16, (x1 - x0) as u16, (y1 - y0) as u16);
    for py in y0..y1 {
        for (ci, &c) in bytes.iter().enumerate() {
            let cx0 = x + ci as i32 * cw;
            if cx0 + cw <= x0 || cx0 >= x1 {
                continue;
            }
            let bits = row_bits(c, tall, py - y);
            for col in 0..cw {
                let px = cx0 + col;
                if px >= x0 && px < x1 {
                    lcd.stream_px(if (bits >> (cw - 1 - col)) & 1 != 0 { fg } else { bg });
                }
            }
        }
    }
    lcd.end();
    total_w
}
